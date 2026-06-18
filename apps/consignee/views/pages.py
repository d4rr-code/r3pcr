import logging
import calendar
import json
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count
from django.db.models.functions import ExtractMonth, ExtractYear
from django.http import JsonResponse
from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.notifications.utils import create_notification
from apps.supervisor.models import IssueReport

logger = logging.getLogger('r3pcr.consignee')
from .common import consignee_required, URGENCY_BUSINESS_DAYS

def _system_rate_parameters():
    from apps.supervisor.models import SystemConfig
    from apps.supervisor.exchange_rates import ensure_daily_exchange_rates

    ensure_daily_exchange_rates()

    rate_keys = {
        'USD': 'rate_USD',
        'EUR': 'rate_EUR',
        'JPY': 'rate_JPY',
        'HKD': 'rate_HKD',
        'CNY': 'rate_CNY',
        'GBP': 'rate_GBP',
        'SGD': 'rate_SGD',
    }
    parameters = {}
    for code, key in rate_keys.items():
        parameters[code] = SystemConfig.get(key, '—') or '—'
    return parameters


def _system_urgency_days():
    from apps.supervisor.models import SystemConfig

    values = dict(URGENCY_BUSINESS_DAYS)
    for key in ('standard', 'priority', 'urgent', 'rush'):
        raw = SystemConfig.get(f'urgency_days_{key}', '')
        try:
            days = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= days <= 60:
            values[key] = days
    return [
        {'label': 'Standard', 'value': values['standard']},
        {'label': 'Priority', 'value': values['priority']},
        {'label': 'Urgent', 'value': values['urgent']},
        {'label': 'Rush', 'value': values['rush']},
    ]


def _system_fee_tiers():
    from apps.supervisor.models import SystemConfig
    import json

    try:
        bf_tiers = json.loads(SystemConfig.get('bf_tiers', '') or '[]')
    except json.JSONDecodeError:
        bf_tiers = []
    try:
        ipf_tiers = json.loads(SystemConfig.get('ipf_tiers', '') or '[]')
    except json.JSONDecodeError:
        ipf_tiers = []
    return bf_tiers, ipf_tiers


def _system_wmcda_items():
    from apps.supervisor.models import SystemConfig

    criteria_meta = [
        {
            'key': 'wmcda_w_cost',
            'label': 'Cost',
            'description': 'Weighs the total landed cost of each shipping mode. Higher weight favors the most cost-efficient option.',
        },
        {
            'key': 'wmcda_w_time',
            'label': 'Time',
            'description': 'Weighs transit time and urgency level. Higher weight favors faster shipping modes.',
        },
        {
            'key': 'wmcda_w_weight',
            'label': 'Weight',
            'description': 'Weighs gross cargo weight. Higher weight prioritizes modes suited for heavier shipments.',
        },
        {
            'key': 'wmcda_w_distance',
            'label': 'Distance',
            'description': 'Weighs route distance and proximity to destination.',
        },
    ]
    return [
        {
            'label': item['label'],
            'description': item['description'],
            'value': SystemConfig.get(item['key'], None),
        }
        for item in criteria_meta
    ]


# ─── Auto-generate HAWB ───────────────────────────────────────────────────────


def _month_points(today, months=12):
    points = []
    for offset in range(months - 1, -1, -1):
        year, month = today.year, today.month - offset
        while month <= 0:
            month += 12
            year -= 1
        points.append((year, month))
    return points


def _monthly_shipment_series(shipments, months=12):
    today = timezone.localdate()
    month_points = _month_points(today, months=months)
    start_year, start_month = month_points[0]
    start_date = today.replace(year=start_year, month=start_month, day=1)
    running_total = shipments.filter(submitted_at__date__lt=start_date).count()
    monthly_qs = (
        shipments
        .filter(submitted_at__date__gte=start_date)
        .annotate(year=ExtractYear('submitted_at'), month=ExtractMonth('submitted_at'))
        .values('year', 'month')
        .annotate(count=Count('id'))
    )
    monthly_lookup = {(row['year'], row['month']): row['count'] for row in monthly_qs}
    labels = [
        f'{calendar.month_abbr[month]} {str(year)[-2:]}'
        for year, month in month_points
    ]
    data = []
    for point in month_points:
        running_total += monthly_lookup.get(point, 0)
        data.append(running_total)
    return labels, data


@login_required
def dashboard(request):
    shipments = Shipment.objects.filter(consignee=request.user)
    total = shipments.count()

    status_counts = {
        'incoming':     shipments.filter(status='incoming').count(),
        'arrived':      shipments.filter(status='arrived').count(),
        'computed':     shipments.filter(status='computed').count(),
        'approved':     shipments.filter(status='approved').count(),
        'rejected':     shipments.filter(status='rejected').count(),
        'for_revision': shipments.filter(status='for_revision').count(),
        'lodgement':    shipments.filter(status='lodgement').count(),
        'ongoing':      shipments.filter(status='ongoing').count(),
        'assessed':     shipments.filter(status='assessed').count(),
        'paid':         shipments.filter(status='paid').count(),
        'released':     shipments.filter(status='released').count(),
        'billed':       shipments.filter(status='billed').count(),
    }

    import_breakdown = list(
        shipments.values('import_type')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    import_labels = dict(Shipment.IMPORT_TYPE_CHOICES)
    for item in import_breakdown:
        item['label'] = import_labels.get(item['import_type'], item['import_type'])

    mode_breakdown = list(
        shipments.values('shipment_type')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    mode_labels = dict(Shipment.SHIPMENT_TYPE_CHOICES)
    for item in mode_breakdown:
        item['label'] = mode_labels.get(item['shipment_type'], item['shipment_type'] or 'Not specified')

    urgency_breakdown = list(
        shipments.values('urgency')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    urgency_labels = dict(Shipment.URGENCY_CHOICES)
    for item in urgency_breakdown:
        item['label'] = urgency_labels.get(item['urgency'], item['urgency'] or 'Unknown')

    # ── Cumulative chart data (rolling 12 months) ────────────────────────────
    monthly_labels, monthly_data = _monthly_shipment_series(shipments)
    today = timezone.localdate()

    # Reminder panel — only actionable shipments, capped so the panel stays
    # compact and doesn't stretch the dashboard with empty space.
    recent_shipments = shipments.order_by('-submitted_at')
    reminders = [
        s for s in recent_shipments
        if s.has_deficiency or s.status in ('arrived', 'computed')
    ][:4]

    context = {
        'total': total,
        **status_counts,
        'import_breakdown':   import_breakdown,
        'mode_breakdown':     mode_breakdown,
        'urgency_breakdown':  urgency_breakdown,
        'recent_shipments':   recent_shipments,
        'reminders':          reminders,
        'monthly_labels':     json.dumps(monthly_labels),
        'monthly_data':       json.dumps(monthly_data),
        'monthly_total':      sum(monthly_data),
        'current_year':       today.year,
    }

    from apps.supervisor.models import Announcement
    recent_announcements = Announcement.objects.filter(
        is_active=True,
        target_audience__in=['all', 'consignee'],
    ).order_by('-created_at')[:3]
    context['recent_announcements'] = recent_announcements

    return render(request, 'consignee/dashboard.html', context)


# ─── System Reference (Read-only Config Viewer) ──────────────────────────────

@login_required
@consignee_required
def system_reference(request):
    return render(request, 'consignee/system_reference.html', {})


def _notify_supervisors_of_issue(issue):
    supervisors = User.objects.filter(role='supervisor', is_active=True)
    for supervisor in supervisors:
        create_notification(
            recipient=supervisor,
            shipment=issue.related_shipment,
            notification_type='general',
            title='New System Issue Report',
            message=(
                f'{issue.reporter.get_full_name() or issue.reporter.username} '
                f'reported a {issue.get_category_display()} issue: {issue.title}'
            ),
        )


@login_required
@consignee_required
def report_issue(request):
    shipments = Shipment.objects.filter(consignee=request.user).order_by('-submitted_at')
    location_choices = IssueReport.locations_for_role('consignee')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        category = request.POST.get('category', '').strip()
        location = request.POST.get('location', '').strip()
        priority = request.POST.get('priority', 'normal').strip()
        description = request.POST.get('description', '').strip()
        shipment_id = request.POST.get('related_shipment', '').strip()

        valid_categories = {choice[0] for choice in IssueReport.CATEGORY_CHOICES}
        valid_locations = {choice[0] for choice in location_choices}
        valid_priorities = {choice[0] for choice in IssueReport.PRIORITY_CHOICES}

        if not title or not description:
            messages.error(request, 'Please provide a short title and describe the issue.')
        elif category not in valid_categories or location not in valid_locations or priority not in valid_priorities:
            messages.error(request, 'Please select valid issue details.')
        else:
            related_shipment = None
            if shipment_id:
                related_shipment = shipments.filter(id=shipment_id).first()
                if not related_shipment:
                    messages.error(request, 'Selected shipment is not available.')
                    return redirect('consignee:report_issue')

            issue = IssueReport.objects.create(
                reporter=request.user,
                reporter_role=request.user.role,
                related_shipment=related_shipment,
                category=category,
                location=location,
                priority=priority,
                title=title,
                description=description,
                attachment=request.FILES.get('attachment'),
            )
            _notify_supervisors_of_issue(issue)
            messages.success(request, 'Issue report submitted. A supervisor can now review it.')
            return redirect('consignee:report_issue')

    reports = IssueReport.objects.filter(reporter=request.user).select_related('related_shipment', 'handled_by')
    return render(request, 'consignee/report_issue.html', {
        'shipments': shipments,
        'reports': reports,
        'shared_issues': IssueReport.cross_role_summary(exclude_user=request.user),
        'category_choices': IssueReport.CATEGORY_CHOICES,
        'location_choices': location_choices,
        'priority_choices': IssueReport.PRIORITY_CHOICES,
    })


@login_required
@consignee_required
def system_parameters(request):
    return render(request, 'consignee/system_parameters.html', {
        'parameters': _system_rate_parameters(),
        'urgency_days': _system_urgency_days(),
    })


@login_required
@consignee_required
def system_fees(request):
    bf_tiers, ipf_tiers = _system_fee_tiers()
    return render(request, 'consignee/system_fees.html', {
        'bf_tiers': bf_tiers,
        'ipf_tiers': ipf_tiers,
    })


@login_required
@consignee_required
def system_wmcda(request):
    from apps.supervisor.models import SystemConfig

    return render(request, 'consignee/system_wmcda.html', {
        'wmcda_items': _system_wmcda_items(),
        'wmcda_method': SystemConfig.get('wmcda_weight_method', 'manual'),
        'wmcda_consistency_ratio': SystemConfig.get('wmcda_ahp_consistency_ratio', ''),
    })


# ─── Submit Shipment ──────────────────────────────────────────────────────────


@login_required
def chart_data(request):
    shipments = Shipment.objects.filter(consignee=request.user)
    labels, data = _monthly_shipment_series(shipments)
    return JsonResponse({'labels': labels, 'data': data})


# ─── Cancel Submission ────────────────────────────────────────────────────────
