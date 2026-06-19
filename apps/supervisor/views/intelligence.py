from collections import defaultdict
from io import BytesIO
import json

from django.db.models import Avg, Count
from django.http import HttpResponse
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
                if next_at:
                    end_at = next_at
                elif shipment.status in TERMINAL_STATUSES:
                    end_at = shipment.updated_at
                else:
                    # Open/current stage age belongs in delay-risk scoring, not
                    # historical bottleneck averages. Including it makes old
                    # active revisions look like a 100+ day process average.
                    continue
                days = _duration_days(log.changed_at, end_at)
                if days:
                    stage_totals[log.new_status]['days'] += days
                    stage_totals[log.new_status]['count'] += 1
        else:
            if shipment.status not in TERMINAL_STATUSES:
                continue
            end_at = shipment.updated_at
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


def _intelligence_context():
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

    stage_chart = {
        'labels': [row['label'] for row in stage_rows[:6]],
        'values': [row['avg_days'] for row in stage_rows[:6]],
    }
    risk_chart = {
        'labels': ['High', 'Medium', 'Low'],
        'values': [
            risk_distribution['high'],
            risk_distribution['medium'],
            risk_distribution['low'],
        ],
    }
    hs_chart = {
        'labels': [row['hs_code__code'] for row in hs_review['top_hs_codes']],
        'values': [row['count'] for row in hs_review['top_hs_codes']],
    }

    return {
        'stage_rows': stage_rows,
        'bottleneck': bottleneck,
        'avg_total_days': avg_total_days,
        'median_total_days': median_total_days,
        'risk_rows': risk_rows,
        'risk_distribution': risk_distribution,
        'hs_review': hs_review,
        'delayed_count': delayed_count,
        'on_time_rate': on_time_rate,
        'stage_chart_json': json.dumps(stage_chart),
        'risk_chart_json': json.dumps(risk_chart),
        'hs_chart_json': json.dumps(hs_chart),
        'generated_at': timezone.localtime(),
    }


@supervisor_required
def intelligence(request):
    return render(request, 'supervisor/intelligence.html', _intelligence_context())


def _export_rows(context):
    summary = [
        ['Average Processing Time', f"{context['avg_total_days']} days"],
        ['Median Processing Time', f"{context['median_total_days']} days"],
        ['On-Time Rate', f"{context['on_time_rate']}%"],
        ['High Risk Shipments', context['risk_distribution']['high']],
        ['Medium Risk Shipments', context['risk_distribution']['medium']],
        ['Low Risk Shipments', context['risk_distribution']['low']],
        ['Delayed Shipments', context['delayed_count']],
        ['HS Records Confirmed', context['hs_review']['historical_count']],
        ['HS Items Needing Review', context['hs_review']['review_count']],
    ]
    stages = [
        [row['label'], row['avg_days'], row['count'], row['total_days']]
        for row in context['stage_rows']
    ]
    risks = [
        [
            row['shipment'].hawb_number,
            row['shipment'].get_status_display(),
            row['label'],
            row['score'],
            '; '.join(row['reasons']),
            row['action'],
        ]
        for row in context['risk_rows']
    ]
    hs_rows = [
        [
            row['item'].description,
            row['item'].hs_code.code if row['item'].hs_code else 'No code selected',
            f"{row['confidence_pct']}%",
            ', '.join(hs.code for hs in row['suggestions']) or 'No suggestion found',
            row['item'].shipment.hawb_number,
        ]
        for row in context['hs_review']['rows']
    ]
    return summary, stages, risks, hs_rows


@supervisor_required
def intelligence_export(request):
    fmt = (request.GET.get('format') or 'xlsx').lower()
    context = _intelligence_context()
    summary, stages, risks, hs_rows = _export_rows(context)
    filename_date = timezone.localtime().strftime('%Y%m%d')

    if fmt == 'pdf':
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=24,
            leftMargin=24,
            topMargin=24,
            bottomMargin=24,
        )
        styles = getSampleStyleSheet()
        cell_style = ParagraphStyle('cell', fontName='Helvetica', fontSize=8, leading=10)
        story = [
            Paragraph('R3-PCR Pre-Clearance Intelligence Report', styles['Title']),
            Paragraph(f"Generated: {context['generated_at'].strftime('%b %d, %Y %I:%M %p')}", styles['Normal']),
            Spacer(1, 12),
        ]
        tables = [
            ('Executive Summary', ['Metric', 'Value'], summary),
            ('Processing Bottlenecks', ['Stage', 'Avg Days', 'Transitions', 'Total Days'], stages),
            ('Delay Risk', ['Shipment', 'Status', 'Risk', 'Score', 'Reasons', 'Action'], risks),
            ('HS Code Review', ['Description', 'Current HS', 'Confidence', 'Suggestions', 'Shipment'], hs_rows),
        ]
        for title, headers, rows in tables:
            story.append(Paragraph(title, styles['Heading2']))
            body = rows or [['No data'] + [''] * (len(headers) - 1)]
            table_data = [headers] + [
                [Paragraph(str(cell), cell_style) for cell in row]
                for row in body
            ]
            table = Table(table_data, repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1B3358')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#DCE5EF')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.extend([table, Spacer(1, 12)])
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="R3PCR_Pre_Clearance_Intelligence_{filename_date}.pdf"'
        return response

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    header_fill = PatternFill('solid', fgColor='1B3358')
    header_font = Font(color='FFFFFF', bold=True)

    sheets = [
        ('Summary', ['Metric', 'Value'], summary),
        ('Bottlenecks', ['Stage', 'Average Days', 'Transitions', 'Total Days'], stages),
        ('Delay Risk', ['Shipment', 'Status', 'Risk', 'Score', 'Reasons', 'Action'], risks),
        ('HS Review', ['Description', 'Current HS', 'Confidence', 'Suggestions', 'Shipment'], hs_rows),
    ]
    for index, (title, headers, rows) in enumerate(sheets):
        sheet = wb.active if index == 0 else wb.create_sheet(title)
        sheet.title = title
        sheet.append(headers)
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        for row in rows:
            sheet.append(row)
        for col in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 55)

    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="R3PCR_Pre_Clearance_Intelligence_{filename_date}.xlsx"'
    return response
