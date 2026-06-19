from collections import defaultdict

from django.db.models import Avg, Count
from django.shortcuts import render
from django.utils import timezone

from apps.computation.models import ShipmentLineItem
from apps.computation.views.hs_codes import suggest_hs_codes
from apps.shipments.models import Shipment, ShipmentHSCode, StatusLog

from .common import supervisor_required


TERMINAL_STATUSES = {'released', 'billed'}
REQUIRED_DOCUMENTS = {'invoice', 'packing_list', 'airway_bill'}
IN_PROGRESS_STATUSES = {'arrived', 'computed', 'for_revision', 'lodgement', 'ongoing', 'assessed'}


def _status_label(status):
    return dict(Shipment.STATUS_CHOICES).get(status, status.replace('_', ' ').title())


def _duration_days(start, end):
    if not start or not end or end <= start:
        return 0
    return (end - start).total_seconds() / 86400


def _stage_metrics(shipments):
    shipments = list(shipments)
    if not shipments:
        return [], None, 0, 0

    shipment_map = {s.id: s for s in shipments}
    logs_by_shipment = defaultdict(list)
    logs = (
        StatusLog.objects
        .filter(shipment_id__in=shipment_map)
        .order_by('shipment_id', 'changed_at')
    )
    for log in logs:
        logs_by_shipment[log.shipment_id].append(log)

    stage_totals = defaultdict(lambda: {'days': 0.0, 'count': 0})
    completed_totals = []
    now = timezone.now()

    for shipment in shipments:
        logs = logs_by_shipment.get(shipment.id, [])
        if logs:
            first = logs[0]
            initial_status = first.old_status or 'incoming'
            initial_days = _duration_days(shipment.submitted_at, first.changed_at)
            if initial_days:
                stage_totals[initial_status]['days'] += initial_days
                stage_totals[initial_status]['count'] += 1

            for index, log in enumerate(logs):
                next_at = logs[index + 1].changed_at if index + 1 < len(logs) else None
                end_at = next_at or (shipment.updated_at if shipment.status in TERMINAL_STATUSES else now)
                days = _duration_days(log.changed_at, end_at)
                if days:
                    stage_totals[log.new_status]['days'] += days
                    stage_totals[log.new_status]['count'] += 1
        else:
            end_at = shipment.updated_at if shipment.status in TERMINAL_STATUSES else now
            days = _duration_days(shipment.submitted_at, end_at)
            if days:
                stage_totals[shipment.status]['days'] += days
                stage_totals[shipment.status]['count'] += 1

        if shipment.status in TERMINAL_STATUSES:
            total_days = _duration_days(shipment.submitted_at, shipment.updated_at)
            if total_days:
                completed_totals.append(total_days)

    stage_rows = []
    for status, data in stage_totals.items():
        avg_days = data['days'] / data['count'] if data['count'] else 0
        stage_rows.append({
            'status': status,
            'label': _status_label(status),
            'avg_days': round(avg_days, 1),
            'count': data['count'],
            'total_days': round(data['days'], 1),
        })
    stage_rows.sort(key=lambda row: row['avg_days'], reverse=True)

    bottleneck = stage_rows[0] if stage_rows else None
    avg_total = round(sum(completed_totals) / len(completed_totals), 1) if completed_totals else 0
    median_total = 0
    if completed_totals:
        ordered = sorted(completed_totals)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            median_total = round(ordered[midpoint], 1)
        else:
            median_total = round((ordered[midpoint - 1] + ordered[midpoint]) / 2, 1)
    return stage_rows[:8], bottleneck, avg_total, median_total


def _current_status_age_days(shipment):
    log = (
        StatusLog.objects
        .filter(shipment=shipment, new_status=shipment.status)
        .order_by('-changed_at')
        .first()
    )
    start = log.changed_at if log else shipment.submitted_at
    return _duration_days(start, timezone.now())


def _risk_for_shipment(shipment):
    score = 0
    reasons = []
    actions = []
    age_days = _current_status_age_days(shipment)

    if age_days >= 5:
        score += 25
        reasons.append(f'{round(age_days, 1)} days in {_status_label(shipment.status)}')
        actions.append('Review current workflow stage for delay.')
    elif age_days >= 3:
        score += 15
        reasons.append(f'{round(age_days, 1)} days in current status')

    timing_status = getattr(shipment, 'kpi_timing_status', '')
    if timing_status == 'delayed':
        score += 30
        reasons.append('Past KPI target')
        actions.append('Escalate KPI-delayed shipment.')
    elif timing_status == 'due_soon':
        score += 10
        reasons.append('KPI window ending soon')

    if shipment.has_deficiency:
        score += 25
        reasons.append('Document deficiency flagged')
        actions.append('Request or review revised documents.')
    if shipment.status == 'for_revision':
        score += 20
        reasons.append('Waiting for consignee revision')
        actions.append('Follow up document resubmission.')

    uploaded = set(shipment.documents.values_list('document_type', flat=True))
    missing = sorted(REQUIRED_DOCUMENTS - uploaded)
    if missing:
        score += min(len(missing) * 10, 25)
        reasons.append('Missing ' + ', '.join(label.replace('_', ' ') for label in missing))
        actions.append('Verify required pre-clearance documents.')

    if shipment.urgency in {'urgent', 'rush'}:
        score += 10
        reasons.append(f'{shipment.get_urgency_display()} urgency')
    elif shipment.urgency == 'priority':
        score += 5
        reasons.append('Priority shipment')

    if shipment.status in {'computed', 'approved'}:
        score += 8
        reasons.append('Awaiting next clearance action')
        actions.append('Move shipment to the next filing step if verified.')

    score = min(score, 100)
    if score >= 70:
        label = 'High'
    elif score >= 40:
        label = 'Medium'
    else:
        label = 'Low'

    return {
        'shipment': shipment,
        'score': score,
        'label': label,
        'age_days': round(age_days, 1),
        'reasons': reasons[:4] or ['No major delay factors detected'],
        'action': actions[0] if actions else 'Continue normal monitoring.',
    }


def _risk_rows(shipments):
    active_shipments = [
        shipment for shipment in shipments
        if shipment.status not in TERMINAL_STATUSES
    ]
    rows = [_risk_for_shipment(shipment) for shipment in active_shipments]
    rows.sort(key=lambda row: row['score'], reverse=True)
    distribution = {
        'high': sum(1 for row in rows if row['label'] == 'High'),
        'medium': sum(1 for row in rows if row['label'] == 'Medium'),
        'low': sum(1 for row in rows if row['label'] == 'Low'),
    }
    return rows[:12], distribution


def _hs_review_rows():
    historical_count = ShipmentHSCode.objects.filter(is_confirmed=True).count()
    top_hs_codes = list(
        ShipmentHSCode.objects
        .filter(is_confirmed=True)
        .values('hs_code__code', 'hs_code__description')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    avg_confidence = (
        ShipmentLineItem.objects
        .exclude(confidence=0)
        .aggregate(avg=Avg('confidence'))['avg']
    )

    candidates = (
        ShipmentLineItem.objects
        .select_related('shipment', 'hs_code')
        .order_by('-updated_at')[:80]
    )
    rows = []
    for item in candidates:
        if not item.description:
            continue
        needs_review = not item.hs_code_id or float(item.confidence or 0) < 0.65 or not item.is_confirmed
        if not needs_review:
            continue
        suggestions = suggest_hs_codes(item.description, top_n=3)
        rows.append({
            'item': item,
            'suggestions': suggestions,
            'confidence_pct': item.confidence_pct,
            'needs_code': not item.hs_code_id,
        })
        if len(rows) >= 10:
            break

    return {
        'rows': rows,
        'historical_count': historical_count,
        'top_hs_codes': top_hs_codes,
        'avg_confidence_pct': round(float(avg_confidence or 0) * 100, 1),
        'review_count': len(rows),
    }


@supervisor_required
def intelligence(request):
    shipments = (
        Shipment.objects
        .select_related('consignee', 'declarant')
        .prefetch_related('documents')
        .order_by('-submitted_at')
    )
    shipments_list = list(shipments[:500])

    stage_rows, bottleneck, avg_total_days, median_total_days = _stage_metrics(shipments_list)
    risk_rows, risk_distribution = _risk_rows(shipments_list)
    hs_review = _hs_review_rows()
    delayed_count = sum(1 for shipment in shipments_list if getattr(shipment, 'kpi_timing_status', '') == 'delayed')
    on_time_count = sum(1 for shipment in shipments_list if getattr(shipment, 'kpi_timing_status', '') in {'on_track', 'complete'})
    measurable = delayed_count + on_time_count
    on_time_rate = round(on_time_count / measurable * 100, 1) if measurable else 0

    return render(request, 'supervisor/intelligence.html', {
        'stage_rows': stage_rows,
        'bottleneck': bottleneck,
        'avg_total_days': avg_total_days,
        'median_total_days': median_total_days,
        'risk_rows': risk_rows,
        'risk_distribution': risk_distribution,
        'hs_review': hs_review,
        'delayed_count': delayed_count,
        'on_time_rate': on_time_rate,
    })
