import json
import logging
from collections import defaultdict
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from apps.accounts.models import User
from apps.shipments.models import Shipment, StatusLog
from apps.computation.models import DutyComputation
from apps.computation.wmcda import wmcda_weight_rows
from apps.consignee.models import Feedback
from apps.notifications.utils import create_notification, notify_shipment_status_change
from ..models import AuditLog, IssueReport, SystemConfig
from ..audit import log_audit
from .reports import (
    REPORT_PERIOD_CHOICES, apply_period, build_report_meta, csv_report_response,
    format_filters, pdf_report_response, resolve_report_period,
)

logger = logging.getLogger(__name__)

from .common import *  # noqa: F401,F403


def compact_page_links(page_obj, page_url, *, edge_count=1, window=2):
    """Build compact pagination links with ellipsis gaps for long page ranges."""
    total_pages = page_obj.paginator.num_pages
    if total_pages <= 1:
        return []

    current = page_obj.number
    visible_pages = set()
    visible_pages.update(range(1, min(edge_count, total_pages) + 1))
    visible_pages.update(range(max(total_pages - edge_count + 1, 1), total_pages + 1))
    visible_pages.update(range(max(current - window, 1), min(current + window, total_pages) + 1))

    links = []
    previous = 0
    for number in sorted(visible_pages):
        if previous and number - previous > 1:
            links.append({'ellipsis': True})
        links.append({
            'number': number,
            'url': page_url(number),
            'current': number == current,
            'ellipsis': False,
        })
        previous = number
    return links


@login_required
@supervisor_required
def shipment_detail(request, shipment_id):
    from apps.shipments.status_progress import build_status_progress, CONSIGNEE_STATUS_SUBLABELS
    from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
    shipment    = get_object_or_404(Shipment, id=shipment_id)
    advisory    = getattr(shipment, 'shipping_advisory', None)
    computation = getattr(shipment, 'computation', None)
    status_logs = shipment.status_logs.order_by('-changed_at')
    sad_document = shipment.documents.filter(document_type='sad').first()
    fan_rows = fan_assessment_rows(sad_document)
    current_sublabel = CONSIGNEE_STATUS_SUBLABELS.get(shipment.status, '')
    back_url = request.GET.get('return_to') or ''
    back_label = request.GET.get('return_label') or 'Back to Dashboard'
    if not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back_url = reverse('supervisor:dashboard')
        back_label = 'Back to Dashboard'

    explanation = wmcda_scores = wmcda_breakdown = None
    declared_score = declared_breakdown = declared_rating = None

    if advisory:
        try:
            from apps.computation.views import compute_wmcda
            wmcda_scores, _, wmcda_breakdown, explanation = compute_wmcda(
                float(advisory.gross_weight), float(advisory.cargo_volume),
                float(advisory.declared_value), advisory.urgency_level,
                float(advisory.distance_km),
            )
            if wmcda_scores and shipment.shipment_type:
                declared_score = wmcda_scores.get(shipment.shipment_type)
                if wmcda_breakdown:
                    declared_breakdown = wmcda_breakdown.get(shipment.shipment_type)
                if declared_score is not None:
                    if declared_score >= 0.80:   declared_rating = 'Excellent'
                    elif declared_score >= 0.65: declared_rating = 'Good'
                    elif declared_score >= 0.50: declared_rating = 'Fair'
                    else:                        declared_rating = 'Poor'
        except Exception as e:
            logger.debug('Declared-score breakdown failed: %s', e)

    return render(request, 'supervisor/shipment_detail.html', {
        'shipment':           shipment,
        'advisory':           advisory,
        'computation':        computation,
        'status_logs':        status_logs,
        'explanation':        explanation,
        'wmcda_scores':       wmcda_scores,
        'wmcda_breakdown':    wmcda_breakdown,
        'wmcda_weights':      wmcda_weight_rows(SystemConfig.get),
        'wmcda_method':       SystemConfig.get('wmcda_weight_method', 'manual'),
        'wmcda_consistency_ratio': SystemConfig.get('wmcda_ahp_consistency_ratio', ''),
        'declared_score':     declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':    declared_rating,
        'status_steps':       build_status_progress(shipment.status, 'consignee'),
        'sad_document':       sad_document,
        'fan_assessment_rows': fan_rows,
        'fan_assessment_has_values': fan_assessment_has_values(fan_rows),
        'current_sublabel':   current_sublabel,
        'back_url':           back_url,
        'back_label':         back_label,
    })


#  Memos & Announcements 


@login_required
@supervisor_required
def reset_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment   = get_object_or_404(Shipment, id=shipment_id)
        old_status = shipment.status
        hawb       = shipment.hawb_number

        shipment.status        = 'incoming'
        shipment.declarant     = None
        shipment.processed_at  = None
        shipment.save()

        DutyComputation.objects.filter(shipment=shipment).delete()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='incoming',
            notes='Reset to incoming by supervisor. Computation cleared.',
        )
        log_audit(
            'status_update',
            f'Supervisor reset shipment {hawb} to incoming.',
            request=request,
            shipment=shipment,
            target=shipment,
            details={'old_status': old_status, 'new_status': 'incoming'},
        )
        messages.success(request, f'Shipment {hawb} reset to Incoming.')
    return redirect('supervisor:dashboard')


@login_required
@supervisor_required
def update_shipment_status(request, shipment_id):
    if request.method == 'POST':
        shipment = get_object_or_404(Shipment, id=shipment_id)
        new_status = request.POST.get('status', '').strip()
        notes = request.POST.get('notes', '').strip()
        allowed = {'approved', 'rejected', 'for_revision'}

        if new_status not in allowed:
            messages.error(request, 'Invalid supervisor status.')
            return redirect('supervisor:dashboard')

        old_status = shipment.status
        shipment.status = new_status
        if new_status in {'approved', 'rejected'} and not shipment.processed_at:
            shipment.processed_at = timezone.now()
        shipment.save()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status=new_status,
            notes=notes or f'Supervisor marked shipment {shipment.get_status_display()}.',
        )
        notify_shipment_status_change(
            shipment=shipment,
            old_status=old_status,
            new_status=new_status,
            changed_by=request.user,
            notes=notes,
        )
        log_audit(
            'status_update',
            f'Supervisor updated {shipment.hawb_number} to {shipment.get_status_display()}.',
            request=request,
            shipment=shipment,
            target=shipment,
            details={'old_status': old_status, 'new_status': new_status, 'notes': notes},
        )
        messages.success(request, f'Shipment {shipment.hawb_number} marked {shipment.get_status_display()}.')

    return redirect('supervisor:dashboard')


@login_required
@supervisor_required
def delete_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment = get_object_or_404(Shipment, id=shipment_id)
        hawb     = shipment.hawb_number

        # Persist audit record to server logs BEFORE deleting.
        # StatusLog can't survive (CASCADE), so we write to the application log
        # which is retained by Railway and can be reviewed later.
        logger.warning(
            'AUDIT: Shipment %s (consignee=%s, status=%s) permanently deleted by supervisor %s at %s',
            hawb,
            shipment.consignee.username,
            shipment.status,
            request.user.username,
            timezone.now().isoformat(),
        )
        log_audit(
            'shipment_delete',
            f'Supervisor deleted shipment {hawb}.',
            request=request,
            target=shipment,
            details={'hawb_number': hawb, 'consignee': shipment.consignee.username, 'status': shipment.status},
        )

        shipment.delete()
        messages.success(request, f'Shipment {hawb} permanently deleted.')
    return redirect('supervisor:dashboard')


#  Feedback Management 

@login_required
@supervisor_required
def manage_feedbacks(request):
    feedbacks = Feedback.objects.select_related('consignee', 'shipment').order_by('-created_at')
    return render(request, 'supervisor/feedbacks.html', {'feedbacks': feedbacks})


@login_required
@supervisor_required
def approve_feedback(request, feedback_id):
    if request.method == 'POST':
        fb = get_object_or_404(Feedback, id=feedback_id)
        fb.is_approved = True
        fb.save()
        messages.success(request, 'Feedback approved  it will now appear on the landing page.')
    return redirect('supervisor:feedbacks')


@login_required
@supervisor_required
def reject_feedback(request, feedback_id):
    if request.method == 'POST':
        fb = get_object_or_404(Feedback, id=feedback_id)
        fb.delete()
        messages.success(request, 'Feedback removed.')
    return redirect('supervisor:feedbacks')


#  Audit Trail

@login_required
@supervisor_required
def _audit_log_queryset(request):
    """Filtered audit log queryset plus the applied filters.

    Shared by the audit trail page and its export so both always apply exactly
    the same filtering.
    """
    action_f = request.GET.get('action', '').strip()
    user_f = request.GET.get('user', '').strip()
    shipment_f = request.GET.get('shipment', '').strip()
    period = resolve_report_period(request)
    date_from = period['date_from']
    date_to = period['date_to']

    logs = AuditLog.objects.select_related('user', 'shipment')
    if action_f:
        logs = logs.filter(action=action_f)
    if user_f:
        logs = logs.filter(
            Q(user__username__icontains=user_f) |
            Q(user_full_name__icontains=user_f) |
            Q(user__first_name__icontains=user_f) |
            Q(user__last_name__icontains=user_f) |
            Q(user_email__icontains=user_f)
        )
    if shipment_f:
        logs = logs.filter(shipment__hawb_number__icontains=shipment_f)
    logs = apply_period(logs, period, 'created_at')

    return logs, {
        'action': action_f,
        'user': user_f,
        'shipment': shipment_f,
        'date_from': date_from,
        'date_to': date_to,
        'period': period,
    }


def audit_trail(request):
    logs, filters = _audit_log_queryset(request)
    action_f = filters['action']
    user_f = filters['user']
    shipment_f = filters['shipment']
    date_from = filters['date_from']
    date_to = filters['date_to']

    paginator = Paginator(logs, 25)
    page = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'supervisor/audit_trail.html', {
        'logs': page,
        'action_choices': AuditLog.ACTION_CHOICES,
        'active_action': action_f,
        'active_user': user_f,
        'active_shipment': shipment_f,
        'active_date_from': date_from,
        'active_date_to': date_to,
        'total_logs': logs.count(),
        'active_period': filters['period']['period'],
        'period_label': filters['period']['label'],
        'PERIOD_CHOICES': REPORT_PERIOD_CHOICES,
    })


AUDIT_EXPORT_LIMIT = 5000

AUDIT_EXPORT_HEADERS = [
    'Date/Time', 'Action', 'User', 'Email', 'Role',
    'Shipment', 'Summary', 'IP Address',
]


def _audit_export_rows(logs):
    """Flatten audit logs into export rows, newest first."""
    rows = []
    for log in logs:
        rows.append([
            timezone.localtime(log.created_at).strftime('%Y-%m-%d %H:%M:%S'),
            log.get_action_display(),
            log.display_name,
            log.user_email or (log.user.email if log.user else ''),
            (log.user_role or '').title(),
            log.shipment.hawb_number if log.shipment else '',
            log.display_summary,
            log.ip_address or '',
        ])
    return rows


@login_required
@supervisor_required
def audit_trail_export(request):
    """Download the audit trail as CSV or PDF, honouring the page's filters."""
    fmt = (request.GET.get('format') or 'csv').lower()
    logs, filters = _audit_log_queryset(request)
    period = filters['period']
    total = logs.count()
    rows = _audit_export_rows(logs[:AUDIT_EXPORT_LIMIT])

    log_audit(
        'report_download',
        f'Supervisor downloaded audit trail ({fmt}).',
        request=request,
        details={
            'format': fmt,
            'period': period['period'],
            'date_from': period['date_from'],
            'date_to': period['date_to'],
            'action': filters['action'],
            'rows': len(rows),
            'matched': total,
        },
    )

    title = 'R3-PCR Audit Trail Report'
    total_label = (
        f'{total} (showing most recent {AUDIT_EXPORT_LIMIT})'
        if total > AUDIT_EXPORT_LIMIT else total
    )
    meta = build_report_meta(
        request,
        period=period,
        filters=format_filters([
            ('Action', dict(AuditLog.ACTION_CHOICES).get(filters['action'], filters['action'])),
            ('User', filters['user']),
            ('Shipment', filters['shipment']),
        ]),
        total=total_label,
    )

    if fmt == 'pdf':
        return pdf_report_response(
            'R3PCR_Audit_Trail', title, meta, AUDIT_EXPORT_HEADERS, rows,
            empty_message='No audit entries match these filters.',
        )

    return csv_report_response(
        'R3PCR_Audit_Trail', title, meta, AUDIT_EXPORT_HEADERS, rows,
    )


#  System Issue Reports

@login_required
@supervisor_required
def issue_reports(request):
    status_f = request.GET.get('status', '').strip()
    priority_f = request.GET.get('priority', '').strip()
    role_f = request.GET.get('role', '').strip()

    reports = IssueReport.objects.select_related(
        'reporter', 'related_shipment', 'handled_by'
    )
    if status_f:
        reports = reports.filter(status=status_f)
    if priority_f:
        reports = reports.filter(priority=priority_f)
    if role_f:
        reports = reports.filter(reporter_role=role_f)

    counts = {
        'open': IssueReport.objects.filter(status='open').count(),
        'in_review': IssueReport.objects.filter(status='in_review').count(),
        'resolved': IssueReport.objects.filter(status='resolved').count(),
        'closed': IssueReport.objects.filter(status='closed').count(),
    }

    return render(request, 'supervisor/issue_reports.html', {
        'reports': reports,
        'counts': counts,
        'status_choices': IssueReport.STATUS_CHOICES,
        'priority_choices': IssueReport.PRIORITY_CHOICES,
        'active_status': status_f,
        'active_priority': priority_f,
        'active_role': role_f,
    })


@login_required
@supervisor_required
def update_issue_report(request, report_id):
    if request.method != 'POST':
        return redirect('supervisor:issue_reports')

    issue = get_object_or_404(IssueReport, id=report_id)
    old_status = issue.status
    status = request.POST.get('status', issue.status).strip()
    note = request.POST.get('supervisor_note', '').strip()
    valid_statuses = {choice[0] for choice in IssueReport.STATUS_CHOICES}

    if status not in valid_statuses:
        messages.error(request, 'Please choose a valid issue status.')
        return redirect('supervisor:issue_reports')

    issue.status = status
    issue.supervisor_note = note
    issue.handled_by = request.user
    if status in {'resolved', 'closed'} and old_status not in {'resolved', 'closed'}:
        issue.resolved_at = timezone.now()
    elif status not in {'resolved', 'closed'}:
        issue.resolved_at = None
    issue.save(update_fields=[
        'status', 'supervisor_note', 'handled_by', 'resolved_at', 'updated_at',
    ])

    create_notification(
        recipient=issue.reporter,
        shipment=issue.related_shipment,
        notification_type='general',
        title='Issue Report Updated',
        message=(
            f'Your issue report "{issue.title}" is now '
            f'{issue.get_status_display()}. {note or ""}'
        ).strip(),
    )
    log_audit(
        'issue_update',
        f'Supervisor updated issue report "{issue.title}".',
        request=request,
        target=issue,
        details={'old_status': old_status, 'new_status': status, 'has_note': bool(note)},
    )
    messages.success(request, 'Issue report updated and reporter notified.')
    return redirect('supervisor:issue_reports')


#  Shipment Records (dedicated browse page)

@login_required
@supervisor_required
def _shipment_records_queryset(request):
    """Filtered shipment queryset plus the applied filters.

    Shared by the shipment records page and its export so both always apply
    exactly the same filtering.
    """
    q              = request.GET.get('q', '').strip()
    status_f       = request.GET.get('status_f', '').strip()
    stype_f        = request.GET.get('stype', '').strip()
    mcda_rec_f     = request.GET.get('mcda_rec', '').strip()
    import_type_f  = request.GET.get('import_type', '').strip()
    period         = resolve_report_period(request)
    valid_shipment_types = {key for key, _label in Shipment.SHIPMENT_TYPE_CHOICES}

    all_shipments = Shipment.objects.select_related('consignee', 'declarant', 'shipping_advisory')
    qs = all_shipments.order_by('-submitted_at')
    if q:
        qs = (
            all_shipments.filter(hawb_number__icontains=q)
            | all_shipments.filter(consignee__first_name__icontains=q)
            | all_shipments.filter(consignee__last_name__icontains=q)
            | all_shipments.filter(consignee__username__icontains=q)
        ).select_related('consignee', 'declarant').order_by('-submitted_at')
    if status_f:
        qs = qs.filter(status=status_f)
    if stype_f:
        if stype_f in valid_shipment_types:
            qs = qs.filter(shipment_type=stype_f)
        else:
            stype_f = ''
    if mcda_rec_f:
        if mcda_rec_f in valid_shipment_types:
            qs = qs.filter(shipping_advisory__recommended_type=mcda_rec_f)
        else:
            mcda_rec_f = ''
    if import_type_f:
        qs = qs.filter(import_type=import_type_f)
    qs = apply_period(qs, period, 'submitted_at')

    return qs, all_shipments, {
        'q': q,
        'status_f': status_f,
        'stype': stype_f,
        'mcda_rec': mcda_rec_f,
        'import_type': import_type_f,
        'period': period,
    }


def shipment_records(request):
    qs, all_shipments, applied = _shipment_records_queryset(request)
    q             = applied['q']
    status_f      = applied['status_f']
    stype_f       = applied['stype']
    mcda_rec_f    = applied['mcda_rec']
    import_type_f = applied['import_type']
    period        = applied['period']
    date_from     = period['date_from']
    date_to       = period['date_to']

    stat_qs = all_shipments
    urgency_counts = {
        'standard': stat_qs.filter(urgency__in=['standard', 'normal']).count(),
        'priority': stat_qs.filter(urgency='priority').count(),
        'urgent': stat_qs.filter(urgency='urgent').count(),
        'rush': stat_qs.filter(urgency='rush').count(),
    }
    shipment_type_counts = {
        'air': stat_qs.filter(shipment_type='air').count(),
        'lcl': stat_qs.filter(shipment_type='lcl').count(),
        'fcl': stat_qs.filter(shipment_type='fcl').count(),
    }
    flagged_shipments = qs.filter(has_deficiency=True)
    revision_shipments = qs.filter(status='for_revision')
    flagged_count = flagged_shipments.count()
    revision_count = revision_shipments.count()
    total_shipments = stat_qs.count()
    status_summary = [
        {'key': 'arrived', 'label': 'Arrived', 'count': stat_qs.filter(status='arrived').count(), 'color': '#f59e0b'},
        {'key': 'lodgement', 'label': 'Lodgement', 'count': stat_qs.filter(status='lodgement').count(), 'color': '#06b6d4'},
        {'key': 'paid', 'label': 'Paid', 'count': stat_qs.filter(status='paid').count(), 'color': '#166534'},
        {'key': 'computed', 'label': 'Computed', 'count': stat_qs.filter(status='computed').count(), 'color': '#3b82f6'},
        {'key': 'ongoing', 'label': 'Ongoing', 'count': stat_qs.filter(status='ongoing').count(), 'color': '#f97316'},
        {'key': 'released', 'label': 'Released', 'count': stat_qs.filter(status='released').count(), 'color': '#14b8a6'},
        {'key': 'approved', 'label': 'Approved', 'count': stat_qs.filter(status='approved').count(), 'color': '#22c55e'},
        {'key': 'assessed', 'label': 'Assessed', 'count': stat_qs.filter(status='assessed').count(), 'color': '#8b5cf6'},
        {'key': 'billed', 'label': 'Billed', 'count': stat_qs.filter(status='billed').count(), 'color': '#64748b'},
    ]
    shipping_type_overview = [
        {'key': 'fcl', 'label': 'Full Container Load', 'count': shipment_type_counts['fcl'], 'color': '#6f8b9b'},
        {'key': 'air', 'label': 'Airfreight', 'count': shipment_type_counts['air'], 'color': '#24466e'},
        {'key': 'lcl', 'label': 'Less Container Load', 'count': shipment_type_counts['lcl'], 'color': '#f59e0b'},
    ]
    shipment_type_filter_choices = [
        ('air', 'Air'),
        ('lcl', 'LCL'),
        ('fcl', 'FCL'),
    ]
    for row in shipping_type_overview:
        row['pct'] = round(row['count'] / total_shipments * 100) if total_shipments else 0

    def paginate_records(queryset, param_name):
        paginator = Paginator(queryset, 6)
        page_obj = paginator.get_page(request.GET.get(param_name, 1))

        def page_url(page_number):
            query = request.GET.copy()
            query[param_name] = page_number
            return f'?{query.urlencode()}'

        page_links = compact_page_links(page_obj, page_url)
        return {
            'records': page_obj.object_list,
            'page_obj': page_obj,
            'page_links': page_links,
            'prev_url': page_url(page_obj.previous_page_number()) if page_obj.has_previous() else '',
            'next_url': page_url(page_obj.next_page_number()) if page_obj.has_next() else '',
        }

    def annotate_hold_preview(records):
        today = timezone.localdate()
        for shipment in records:
            start_at  = shipment.deficiency_flagged_at or shipment.submitted_at
            due_date  = _add_business_days(start_at, 3)
            days_left = _business_days_diff(today, due_date)
            if days_left < 0:
                shipment.hold_due_label = f'Overdue {abs(days_left)} Business Day{"s" if abs(days_left) != 1 else ""}'
            elif days_left == 0:
                shipment.hold_due_label = 'Due Today'
            elif days_left == 1:
                shipment.hold_due_label = '1 Business Day Left'
            else:
                shipment.hold_due_label = f'{days_left} Days Left'

    shipments_page = paginate_records(qs, 'shipments_page')
    flagged_page = paginate_records(flagged_shipments, 'flagged_page')
    revision_page = paginate_records(revision_shipments, 'revision_page')
    annotate_hold_preview(flagged_page['records'])

    return render(request, 'supervisor/shipment_records.html', {
        'shipments':      shipments_page['records'],
        'shipments_page': shipments_page,
        'flagged_shipments': flagged_page['records'],
        'flagged_page': flagged_page,
        'flagged_count': flagged_count,
        'revision_shipments': revision_page['records'],
        'revision_page': revision_page,
        'revision_count': revision_count,
        'total_shipments': total_shipments,
        'shipment_type_counts': shipment_type_counts,
        'status_summary': status_summary,
        'shipping_type_overview': shipping_type_overview,
        'urgency_counts': urgency_counts,
        'q':                   q,
        'status_f':            status_f,
        'stype_f':             stype_f,
        'mcda_rec_f':          mcda_rec_f,
        'import_type_f':       import_type_f,
        'date_from':           date_from,
        'date_to':             date_to,
        'STATUS_CHOICES':      Shipment.STATUS_CHOICES,
        'TYPE_CHOICES':        shipment_type_filter_choices,
        'IMPORT_TYPE_CHOICES': Shipment.IMPORT_TYPE_CHOICES,
        'active_period':       period['period'],
        'period_label':        period['label'],
        'PERIOD_CHOICES':      REPORT_PERIOD_CHOICES,
        'export_query':        request.GET.urlencode(),
    })


#  Shipment Register Export

SHIPMENT_EXPORT_LIMIT = 5000

SHIPMENT_EXPORT_HEADERS = [
    'HAWB', 'Consignee', 'Declarant', 'Type', 'Status', 'Urgency',
    'Import Type', 'Declared Value', 'Currency', 'Submitted',
    'Last Updated', 'KPI Status',
]


def _shipment_export_rows(shipments):
    rows = []
    for s in shipments:
        consignee = (
            s.consignee.company_name
            or s.consignee.get_full_name()
            or s.consignee.username
        ) if s.consignee else ''
        declarant = (
            s.declarant.get_full_name() or s.declarant.username
        ) if s.declarant else 'Unassigned'
        rows.append([
            s.hawb_number,
            consignee,
            declarant,
            s.get_shipment_type_display() or '',
            s.get_status_display(),
            s.get_urgency_display(),
            s.get_import_type_display(),
            f'{s.declared_value:.2f}' if s.declared_value is not None else '',
            s.invoice_currency or '',
            timezone.localtime(s.submitted_at).strftime('%Y-%m-%d %H:%M') if s.submitted_at else '',
            timezone.localtime(s.updated_at).strftime('%Y-%m-%d %H:%M') if s.updated_at else '',
            s.kpi_timing_label or '',
        ])
    return rows


@login_required
@supervisor_required
def shipment_records_export(request):
    """Download the shipment register as CSV or PDF, honouring the page filters."""
    fmt = (request.GET.get('format') or 'csv').lower()
    qs, _all_shipments, applied = _shipment_records_queryset(request)
    period = applied['period']
    total = qs.count()
    rows = _shipment_export_rows(qs[:SHIPMENT_EXPORT_LIMIT])

    log_audit(
        'report_download',
        f'Supervisor downloaded shipment register ({fmt}).',
        request=request,
        details={
            'format': fmt,
            'period': period['period'],
            'date_from': period['date_from'],
            'date_to': period['date_to'],
            'status': applied['status_f'],
            'shipment_type': applied['stype'],
            'rows': len(rows),
            'matched': total,
        },
    )

    title = 'R3-PCR Shipment Register Report'
    total_label = (
        f'{total} (showing most recent {SHIPMENT_EXPORT_LIMIT})'
        if total > SHIPMENT_EXPORT_LIMIT else total
    )
    meta = build_report_meta(
        request,
        period=period,
        filters=format_filters([
            ('Status', dict(Shipment.STATUS_CHOICES).get(applied['status_f'], applied['status_f'])),
            ('Type', dict(Shipment.SHIPMENT_TYPE_CHOICES).get(applied['stype'], applied['stype'])),
            ('Import Type', dict(Shipment.IMPORT_TYPE_CHOICES).get(applied['import_type'], applied['import_type'])),
            ('MCDA', applied['mcda_rec']),
            ('Search', applied['q']),
        ]),
        total=total_label,
    )

    if fmt == 'pdf':
        return pdf_report_response(
            'R3PCR_Shipment_Register', title, meta, SHIPMENT_EXPORT_HEADERS, rows,
            empty_message='No shipments match these filters.',
        )

    return csv_report_response(
        'R3PCR_Shipment_Register', title, meta, SHIPMENT_EXPORT_HEADERS, rows,
    )


#  Client Lists

@login_required
@supervisor_required
def consignee_list(request):
    q  = request.GET.get('q', '').strip()
    qs = User.objects.filter(role='consignee', is_pending_approval=False).order_by('first_name', 'username')
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    qs = qs.annotate(shipment_count=Count('shipments'))
    terminal_statuses = ['paid', 'released', 'billed']
    consignee_rows = []
    for consignee in qs:
        shipments = Shipment.objects.filter(consignee=consignee).select_related('declarant').order_by('-submitted_at')
        consignee_rows.append({
            'user': consignee,
            'name': consignee.get_full_name() or consignee.username,
            'company': consignee.company_name or '-',
            'total_shipments': shipments.count(),
            'in_progress_shipments': shipments.exclude(status__in=terminal_statuses).count(),
            'completed_or_billed_shipments': shipments.filter(status__in=terminal_statuses).count(),
            'flagged_shipments': shipments.filter(has_deficiency=True).count(),
        })
    return render(request, 'supervisor/consignee_list.html', {
        'consignees': consignee_rows,
        'total_consignees': qs.count(),
        'q': q,
    })


@login_required
@supervisor_required
def consignee_detail(request, user_id):
    consignee = get_object_or_404(
        User,
        id=user_id,
        role='consignee',
        is_pending_approval=False,
    )
    q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    shipments_qs = (
        Shipment.objects
        .filter(consignee=consignee)
        .select_related('consignee', 'declarant', 'shipping_advisory')
        .order_by('-submitted_at')
    )
    if q:
        shipments_qs = shipments_qs.filter(
            Q(hawb_number__icontains=q)
            | Q(import_type__icontains=q)
            | Q(status__icontains=q)
            | Q(declarant__first_name__icontains=q)
            | Q(declarant__last_name__icontains=q)
            | Q(declarant__username__icontains=q)
        )
    if date_from:
        shipments_qs = shipments_qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments_qs = shipments_qs.filter(submitted_at__date__lte=date_to)

    all_shipments = Shipment.objects.filter(consignee=consignee)
    terminal_statuses = ['paid', 'released', 'billed']
    paginator = Paginator(shipments_qs, 6)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    def page_url(page_number):
        query = request.GET.copy()
        query['page'] = page_number
        return f'?{query.urlencode()}'

    page_links = compact_page_links(page_obj, page_url)
    shipments_page = {
        'records': page_obj.object_list,
        'page_obj': page_obj,
        'page_links': page_links,
        'prev_url': page_url(page_obj.previous_page_number()) if page_obj.has_previous() else '',
        'next_url': page_url(page_obj.next_page_number()) if page_obj.has_next() else '',
    }

    return render(request, 'supervisor/consignee_detail.html', {
        'consignee': consignee,
        'shipments': shipments_page['records'],
        'shipments_page': shipments_page,
        'detail_return_url': request.get_full_path(),
        'detail_return_label': 'Back to Shipment Records',
        'total_shipments': all_shipments.count(),
        'active_shipments': all_shipments.exclude(status__in=terminal_statuses).count(),
        'flagged_shipments': all_shipments.filter(has_deficiency=True).count(),
        'q': q,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
@supervisor_required
def declarant_list(request):
    q  = request.GET.get('q', '').strip()
    qs = User.objects.filter(role='declarant', is_pending_approval=False).order_by('first_name', 'username')
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    terminal_statuses = ['paid', 'released', 'billed']
    declarant_rows = []
    for declarant in qs:
        shipments = Shipment.objects.filter(declarant=declarant).select_related('consignee').order_by('-submitted_at')
        cleared_statuses = ['approved', 'released', 'billed']
        cleared = shipments.filter(status__in=cleared_statuses).count()
        handled_consignees = shipments.values('consignee_id').distinct().count()
        active = shipments.exclude(status__in=terminal_statuses).count()
        revised = shipments.filter(status='for_revision').count()
        current = shipments.exclude(status__in=terminal_statuses).first() or shipments.first()

        completed_durations = []
        for shipment in shipments.filter(status__in=cleared_statuses):
            end_log = (
                StatusLog.objects
                .filter(shipment=shipment, new_status__in=cleared_statuses)
                .order_by('-changed_at')
                .first()
            )
            end_at = (
                end_log.changed_at if end_log else
                shipment.processed_at or shipment.updated_at
            )
            if end_at and shipment.submitted_at and end_at >= shipment.submitted_at:
                completed_durations.append(end_at - shipment.submitted_at)
        avg_days = None
        if completed_durations:
            avg_days = round(
                sum(duration.total_seconds() for duration in completed_durations)
                / len(completed_durations) / 86400,
                1,
            )

        total = shipments.count()
        clearance_rate = round(cleared / total * 100) if total else 0
        declarant_rows.append({
            'user': declarant,
            'name': declarant.get_full_name() or declarant.username,
            'initial': (declarant.first_name or declarant.username or '?')[:1].upper(),
            'cleared_shipments': cleared,
            'handled_consignees': handled_consignees,
            'active_count': active,
            'revised_count': revised,
            'average_clearance_days': avg_days,
            'current_shipment': current,
            'clearance_rate': clearance_rate,
        })

    top_declarants = sorted(
        declarant_rows,
        key=lambda row: (
            row['cleared_shipments'],
            row['clearance_rate'],
            -(row['average_clearance_days'] or 9999),
        ),
        reverse=True,
    )[:3]
    rank_labels = ['1st', '2nd', '3rd']
    for index, row in enumerate(top_declarants):
        row['rank'] = rank_labels[index]

    return render(request, 'supervisor/declarant_list.html', {
        'declarants': declarant_rows,
        'top_declarants': top_declarants,
        'q': q,
    })


@login_required
@supervisor_required
def declarant_detail(request, user_id):
    declarant = get_object_or_404(
        User,
        id=user_id,
        role='declarant',
        is_pending_approval=False,
    )
    q = request.GET.get('q', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    shipments_qs = (
        Shipment.objects
        .filter(declarant=declarant)
        .select_related('consignee', 'declarant')
        .order_by('-submitted_at')
    )
    if q:
        shipments_qs = shipments_qs.filter(
            Q(hawb_number__icontains=q)
            | Q(import_type__icontains=q)
            | Q(status__icontains=q)
            | Q(consignee__first_name__icontains=q)
            | Q(consignee__last_name__icontains=q)
            | Q(consignee__username__icontains=q)
        )
    if date_from:
        shipments_qs = shipments_qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments_qs = shipments_qs.filter(submitted_at__date__lte=date_to)

    all_shipments = (
        Shipment.objects
        .filter(declarant=declarant)
        .select_related('consignee', 'declarant')
    )
    cleared_statuses = ['approved', 'released', 'billed']
    terminal_statuses = ['paid', 'released', 'billed']
    preclearance_done_statuses = ['assessed', 'paid', 'released', 'billed']
    now = timezone.now()

    status_counts = {
        row['status']: row['count']
        for row in all_shipments.values('status').annotate(count=Count('id'))
    }
    status_colors = {
        'incoming': '#9DB0C5', 'arrived': '#f59e0b', 'computed': '#2F7FD6',
        'approved': '#20B86F', 'rejected': '#ef4444', 'for_revision': '#F2C715',
        'lodgement': '#06b6d4', 'ongoing': '#FF6A00', 'assessed': '#7c3aed',
        'paid': '#166534', 'released': '#14b8a6', 'billed': '#687481',
    }
    status_display = {
        'for_revision': 'Revision',
        'rejected': 'Flags',
    }
    status_subtitles = {
        'incoming': 'Awaits Declarant Assignment',
        'arrived': 'Awaits ECDT Processing',
        'computed': 'Awaits Consignee Approval',
        'for_revision': 'Returned from Consignee',
        'rejected': 'Rejected by Consignee',
        'approved': 'Proceeding to Lodgement',
        'lodgement': 'Filed with BOC',
        'ongoing': 'For final assessment',
        'assessed': 'Awaits payment',
        'paid': 'Payment received',
        'released': 'Released shipment',
        'billed': 'Fully processed',
    }
    status_order = [
        'incoming', 'approved', 'assessed',
        'arrived', 'for_revision', 'paid',
        'rejected', 'lodgement', 'released',
        'computed', 'ongoing', 'billed',
    ]
    status_rows = []
    total_shipments = all_shipments.count()
    for key in status_order:
        label = dict(Shipment.STATUS_CHOICES).get(key, key.title())
        count = status_counts.get(key, 0)
        status_rows.append({
            'key': key,
            'label': status_display.get(key, label),
            'subtitle': status_subtitles.get(key, ''),
            'count': count,
            'pct': round(count / total_shipments * 100, 1) if total_shipments else 0,
            'color': status_colors.get(key, '#64748B'),
        })

    type_meta = [
        ('fcl', 'Full Container Load (FCL)', '#6F8B9B'),
        ('air', 'Airfreight', '#24466E'),
        ('lcl', 'Less Container Load (LCL)', '#F59E0B'),
    ]
    type_counts = {
        row['shipment_type']: row['count']
        for row in all_shipments.values('shipment_type').annotate(count=Count('id'))
    }
    type_rows = [
        {'key': key, 'label': label, 'color': color, 'count': type_counts.get(key, 0)}
        for key, label, color in type_meta
    ]

    monthly_durations = defaultdict(list)
    completed_durations = []
    for shipment in all_shipments.filter(status__in=cleared_statuses):
        end_log = (
            StatusLog.objects
            .filter(shipment=shipment, new_status__in=cleared_statuses)
            .order_by('-changed_at')
            .first()
        )
        end_at = end_log.changed_at if end_log else shipment.processed_at or shipment.updated_at
        if end_at and shipment.submitted_at and end_at >= shipment.submitted_at:
            days = (end_at - shipment.submitted_at).total_seconds() / 86400
            completed_durations.append(days)
            if shipment.submitted_at.year == now.year:
                monthly_durations[shipment.submitted_at.month].append(days)

    average_clearance_days = round(sum(completed_durations) / len(completed_durations), 1) if completed_durations else None
    on_time_count = sum(1 for days in completed_durations if days <= 3)
    on_time_rate = round(on_time_count / len(completed_durations) * 100) if completed_durations else 0
    trend_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    trend_data = [
        round(sum(monthly_durations[month]) / len(monthly_durations[month]), 1)
        if monthly_durations.get(month) else 0
        for month in range(1, 13)
    ]

    due_buckets = {'one_day': 0, 'three_days': 0, 'five_days': 0, 'over_five': 0}
    _today_d = now.date()
    for shipment in all_shipments.exclude(status__in=preclearance_done_statuses):
        alloc     = _urgency_days_for(shipment.urgency)
        deadline  = _add_business_days(shipment.submitted_at, alloc)
        remaining = _business_days_diff(_today_d, deadline)
        if remaining <= 1:
            due_buckets['one_day'] += 1
        elif remaining <= 3:
            due_buckets['three_days'] += 1
        elif remaining <= 5:
            due_buckets['five_days'] += 1
        else:
            due_buckets['over_five'] += 1
    due_total = sum(due_buckets.values())

    paginator = Paginator(shipments_qs, 6)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    def page_url(page_number):
        query = request.GET.copy()
        query['page'] = page_number
        return f'?{query.urlencode()}'

    page_links = compact_page_links(page_obj, page_url)
    shipments_page = {
        'records': page_obj.object_list,
        'page_obj': page_obj,
        'page_links': page_links,
        'prev_url': page_url(page_obj.previous_page_number()) if page_obj.has_previous() else '',
        'next_url': page_url(page_obj.next_page_number()) if page_obj.has_next() else '',
    }

    return render(request, 'supervisor/declarant_detail.html', {
        'declarant': declarant,
        'shipments': shipments_page['records'],
        'shipments_page': shipments_page,
        'total_shipments': total_shipments,
        'active_shipments': all_shipments.exclude(status__in=terminal_statuses).count(),
        'cleared_shipments': all_shipments.filter(status__in=cleared_statuses).count(),
        'handled_consignees': all_shipments.values('consignee_id').distinct().count(),
        'average_clearance_days': average_clearance_days,
        'on_time_rate': on_time_rate,
        'status_rows': status_rows,
        'type_rows': type_rows,
        'type_chart_labels': json.dumps([row['label'] for row in type_rows]),
        'type_chart_data': json.dumps([row['count'] for row in type_rows]),
        'type_chart_colors': json.dumps([row['color'] for row in type_rows]),
        'trend_labels': json.dumps(trend_labels),
        'trend_data': json.dumps(trend_data),
        'trend_year': now.year,
        'due_data': due_buckets,
        'due_total': due_total,
        'due_chart_labels': json.dumps(['1 Day Left', '3 Days Left', '5 Days Left', '5+ Days Left']),
        'due_chart_data': json.dumps([due_buckets['one_day'], due_buckets['three_days'], due_buckets['five_days'], due_buckets['over_five']]),
        'due_chart_colors': json.dumps(['#dc0000', '#f75b5b', '#f9a1a1', '#ffd6d6']),
        'detail_return_url': request.get_full_path(),
        'detail_return_label': 'Back to Declarant Records',
        'q': q,
        'date_from': date_from,
        'date_to': date_to,
    })
