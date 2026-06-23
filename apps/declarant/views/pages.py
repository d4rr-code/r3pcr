import json
import logging
import re
from collections import defaultdict
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.utils import timezone
from apps.accounts.models import User
from apps.shipments.models import HSCode, Shipment, StatusLog
from apps.notifications.utils import create_notification
from apps.supervisor.models import IssueReport
from apps.supervisor.views import _HS_SECTIONS, _chapter_num

logger = logging.getLogger('r3pcr.declarant')

from .common import *  # noqa: F401,F403

@login_required
@declarant_required
def dashboard(request):
    shipments = Shipment.objects.all()
    my = {'declarant': request.user}

    # ── KPI 1: Incoming — unassigned shipments waiting in the general pool ──────
    incoming_count = shipments.filter(status='incoming').count()

    # ── KPI 2: In Progress — assigned to me and actively being worked on ─────────
    in_progress = shipments.filter(
        declarant=request.user,
        status__in=['arrived', 'computed', 'for_revision', 'lodgement', 'ongoing', 'assessed'],
    ).count()

    # ── KPI 3: Approved by consignee — moving to payment ─────────────────────────
    approved_count = shipments.filter(
        declarant=request.user,
        status__in=['approved', 'paid', 'released'],
    ).count()

    # ── KPI 4: Fully billed (true completion) ──────────────────────────────────
    billed_count = shipments.filter(declarant=request.user, status='billed').count()

    # ── KPI 5: Avg processing time — arrived → billed (actual work time) ──────
    avg_processing_days = None
    billed_qs = list(shipments.filter(status='billed', **my))
    if billed_qs:
        durations = []
        for s in billed_qs:
            # Get when it transitioned to 'arrived' status
            arrived_log = (
                StatusLog.objects
                .filter(shipment=s, new_status='arrived')
                .order_by('changed_at').first()
            )
            # Get when it transitioned to 'billed' status
            billed_log = (
                StatusLog.objects
                .filter(shipment=s, new_status='billed')
                .order_by('changed_at').first()
            )
            start_at = arrived_log.changed_at if arrived_log else s.submitted_at
            end_at = billed_log.changed_at if billed_log else s.updated_at
            if end_at and start_at and end_at >= start_at:
                durations.append((end_at - start_at).total_seconds())
        if durations:
            avg_processing_days = round(sum(durations) / len(durations) / 86400, 1)

    # ── KPI 6: Completion rate — billed / total assigned ────────────────────────
    total_assigned = shipments.filter(**my).count()
    completion_rate = round(billed_count / total_assigned * 100, 1) if total_assigned > 0 else 0

    # Incoming queue for dashboard table (up to 20, annotated with due dates)
    today = timezone.localdate()
    pending_list = list(shipments.filter(status='incoming').select_related('consignee')[:20])
    _annotate_due(pending_list, today)

    my_shipments = (
        Shipment.objects
        .filter(declarant=request.user)
        .select_related('consignee', 'declarant')
    )
    terminal_statuses = ['paid', 'released', 'billed']
    preclearance_done_statuses = ['assessed', 'paid', 'released', 'billed']
    cleared_statuses = ['approved', 'released', 'billed']

    status_counts = {
        row['status']: row['count']
        for row in my_shipments.values('status').annotate(count=Count('id'))
    }
    status_order = [
        'incoming', 'approved', 'assessed',
        'arrived', 'for_revision', 'paid',
        'rejected', 'lodgement', 'released',
        'computed', 'ongoing', 'billed',
    ]
    status_colors = {
        'incoming': '#9DB0C5', 'arrived': '#f59e0b', 'computed': '#2F7FD6',
        'approved': '#20B86F', 'rejected': '#ef4444', 'for_revision': '#F2C715',
        'lodgement': '#06b6d4', 'ongoing': '#FF6A00', 'assessed': '#7c3aed',
        'paid': '#166534', 'released': '#14b8a6', 'billed': '#687481',
    }
    status_display = {'for_revision': 'Revision', 'rejected': 'Flags'}
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
    status_doc_filters = {'ongoing', 'assessed', 'paid', 'released', 'billed'}
    latest_status_shipment_ids = {}
    for shipment in my_shipments.order_by('-updated_at'):
        if shipment.status in status_doc_filters and shipment.status not in latest_status_shipment_ids:
            latest_status_shipment_ids[shipment.status] = shipment.id
    my_total_shipments = my_shipments.count()
    status_rows = []
    for key in status_order:
        label = dict(Shipment.STATUS_CHOICES).get(key, key.title())
        count = status_counts.get(key, 0)
        status_rows.append({
            'key': key,
            'label': status_display.get(key, label),
            'subtitle': status_subtitles.get(key, ''),
            'count': count,
            'pct': round(count / my_total_shipments * 100, 1) if my_total_shipments else 0,
            'color': status_colors.get(key, '#64748B'),
            'doc_filter_available': key in status_doc_filters and count > 0,
            'sample_shipment_id': latest_status_shipment_ids.get(key),
        })

    type_meta = [
        ('fcl', 'Full Container Load (FCL)', '#6F8B9B'),
        ('air', 'Airfreight', '#24466E'),
        ('lcl', 'Less Container Load (LCL)', '#F59E0B'),
    ]
    type_counts = {
        row['shipment_type']: row['count']
        for row in my_shipments.values('shipment_type').annotate(count=Count('id'))
    }
    type_rows = [
        {'key': key, 'label': label, 'color': color, 'count': type_counts.get(key, 0)}
        for key, label, color in type_meta
    ]

    now = timezone.now()
    monthly_durations = defaultdict(list)
    completed_durations = []
    for shipment in my_shipments.filter(status__in=cleared_statuses):
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
    dashboard_on_time_rate = (
        round(sum(1 for days in completed_durations if days <= 3) / len(completed_durations) * 100)
        if completed_durations else 0
    )
    trend_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    trend_data = [
        round(sum(monthly_durations[month]) / len(monthly_durations[month]), 1)
        if monthly_durations.get(month) else 0
        for month in range(1, 13)
    ]

    due_buckets = {'one_day': 0, 'three_days': 0, 'five_days': 0, 'over_five': 0}
    _today_d = now.date()
    for shipment in my_shipments.exclude(status__in=preclearance_done_statuses):
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

    record_q = request.GET.get('q', '').strip()
    record_status = request.GET.get('status', '').strip()
    record_urgency = request.GET.get('urgency', '').strip()
    record_date_from = request.GET.get('date_from', '').strip()
    record_date_to = request.GET.get('date_to', '').strip()

    records_qs = my_shipments.order_by('-submitted_at')
    if record_q:
        records_qs = records_qs.filter(
            Q(hawb_number__icontains=record_q)
            | Q(job_order_reference__icontains=record_q)
            | Q(consignee__first_name__icontains=record_q)
            | Q(consignee__last_name__icontains=record_q)
            | Q(consignee__username__icontains=record_q)
        )
    valid_statuses = {key for key, _label in Shipment.STATUS_CHOICES}
    if record_status in valid_statuses:
        records_qs = records_qs.filter(status=record_status)
    valid_urgencies = {key for key, _label in Shipment.URGENCY_CHOICES}
    if record_urgency in valid_urgencies:
        records_qs = records_qs.filter(urgency=record_urgency)
    if record_date_from:
        records_qs = records_qs.filter(submitted_at__date__gte=record_date_from)
    if record_date_to:
        records_qs = records_qs.filter(submitted_at__date__lte=record_date_to)

    my_records = list(records_qs[:20])

    context = {
        'queue':               incoming_count,
        'in_progress':         in_progress,
        'ecdt_approved':       approved_count,
        'billed':              billed_count,
        'avg_processing_days': avg_processing_days,
        'completion_rate':     completion_rate,
        'pending_shipments':   pending_list,
        'my_total_shipments':  my_total_shipments,
        'my_active_shipments': my_shipments.exclude(status__in=terminal_statuses).count(),
        'my_cleared_shipments': my_shipments.filter(status__in=cleared_statuses).count(),
        'my_handled_consignees': my_shipments.values('consignee_id').distinct().count(),
        'dashboard_on_time_rate': dashboard_on_time_rate,
        'status_rows':         status_rows,
        'type_rows':           type_rows,
        'trend_labels':        json.dumps(trend_labels),
        'trend_data':          json.dumps(trend_data),
        'trend_year':          now.year,
        'due_data':            due_buckets,
        'due_total':           sum(due_buckets.values()),
        'due_chart_labels':    json.dumps(['1 Day Left', '3 Days Left', '5 Days Left', '5+ Days Left']),
        'due_chart_data':      json.dumps([due_buckets['one_day'], due_buckets['three_days'], due_buckets['five_days'], due_buckets['over_five']]),
        'due_chart_colors':    json.dumps(['#dc0000', '#f75b5b', '#f9a1a1', '#ffd6d6']),
        'my_records':          my_records,
        'record_filters': {
            'q': record_q,
            'status': record_status if record_status in valid_statuses else '',
            'urgency': record_urgency if record_urgency in valid_urgencies else '',
            'date_from': record_date_from,
            'date_to': record_date_to,
        },
        'record_status_choices': Shipment.STATUS_CHOICES,
        'record_urgency_choices': Shipment.URGENCY_CHOICES,
    }
    return render(request, 'declarant/dashboard.html', context)


# ─── System Reference (Read-only Config Viewer) ─────────────────────────────────

@login_required
@declarant_required
def system_reference(request):
    """Main system reference page with links to sub-sections."""
    return render(request, 'declarant/system_reference.html', {})


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
@declarant_required
def report_issue(request):
    shipments = Shipment.objects.filter(declarant=request.user).order_by('-submitted_at')
    location_choices = IssueReport.locations_for_role('declarant')

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
                    return redirect('declarant:report_issue')

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
            return redirect('declarant:report_issue')

    reports = IssueReport.objects.filter(reporter=request.user).select_related('related_shipment', 'handled_by')
    return render(request, 'declarant/report_issue.html', {
        'shipments': shipments,
        'reports': reports,
        'shared_issues': IssueReport.cross_role_summary(exclude_user=request.user),
        'category_choices': IssueReport.CATEGORY_CHOICES,
        'location_choices': location_choices,
        'priority_choices': IssueReport.PRIORITY_CHOICES,
    })


@login_required
@declarant_required
def system_parameters(request):
    """View global exchange rate parameters."""
    from apps.supervisor.models import SystemConfig
    from apps.supervisor.exchange_rates import ensure_daily_exchange_rates

    ensure_daily_exchange_rates()

    rate_keys = {
        'USD': 'rate_USD', 'EUR': 'rate_EUR', 'JPY': 'rate_JPY',
        'HKD': 'rate_HKD', 'CNY': 'rate_CNY', 'GBP': 'rate_GBP',
        'SGD': 'rate_SGD',
    }

    parameters = {}
    for code, key in rate_keys.items():
        try:
            val = SystemConfig.objects.get(key=key).value
            parameters[code] = val
        except SystemConfig.DoesNotExist:
            parameters[code] = '—'

    urgency_days = _urgency_business_days()

    return render(request, 'declarant/system_parameters.html', {
        'parameters': parameters,
        'urgency_days': [
            {'label': 'Standard', 'value': urgency_days.get('standard')},
            {'label': 'Priority', 'value': urgency_days.get('priority')},
            {'label': 'Urgent', 'value': urgency_days.get('urgent')},
            {'label': 'Rush', 'value': urgency_days.get('rush')},
        ],
    })


@login_required
@declarant_required
def system_fees(request):
    """View brokerage and IPF fee schedules."""
    from apps.supervisor.models import SystemConfig
    import json

    try:
        bf_raw = SystemConfig.objects.get(key='bf_tiers').value
        bf_tiers = json.loads(bf_raw) if bf_raw else []
    except (SystemConfig.DoesNotExist, json.JSONDecodeError):
        bf_tiers = []

    try:
        ipf_raw = SystemConfig.objects.get(key='ipf_tiers').value
        ipf_tiers = json.loads(ipf_raw) if ipf_raw else []
    except (SystemConfig.DoesNotExist, json.JSONDecodeError):
        ipf_tiers = []

    return render(request, 'declarant/system_fees.html', {
        'bf_tiers': bf_tiers,
        'ipf_tiers': ipf_tiers,
    })


@login_required
@declarant_required
def system_wmcda(request):
    """View MCDA criteria weights and configuration."""
    from apps.supervisor.models import SystemConfig

    criteria_meta = [
        {
            'key': 'wmcda_w_cost',
            'label': 'Cost',
            'description': 'Weighs the total landed cost (freight + duties + fees) of each shipping mode. Higher weight favors the most cost-efficient option.',
        },
        {
            'key': 'wmcda_w_time',
            'label': 'Time',
            'description': 'Weighs transit time and urgency level of the shipment (Rush/Urgent/Normal). Higher weight favors faster shipping modes.',
        },
        {
            'key': 'wmcda_w_weight',
            'label': 'Weight',
            'description': 'Weighs the gross cargo weight when scoring modes. Higher weight prioritizes modes suited for heavier shipments such as FCL.',
        },
        {
            'key': 'wmcda_w_distance',
            'label': 'Distance',
            'description': 'Weighs transport route distance. Higher weight prioritizes shorter transit routes and proximity to the destination port.',
        },
    ]

    wmcda_items = []
    for meta in criteria_meta:
        try:
            val = SystemConfig.objects.get(key=meta['key']).value
        except SystemConfig.DoesNotExist:
            val = None
        wmcda_items.append({
            'label': meta['label'],
            'description': meta['description'],
            'value': val,
        })

    return render(request, 'declarant/system_wmcda.html', {
        'wmcda_items': wmcda_items,
        'wmcda_method': SystemConfig.get('wmcda_weight_method', 'manual'),
        'wmcda_consistency_ratio': SystemConfig.get('wmcda_ahp_consistency_ratio', ''),
    })


@login_required
@declarant_required
def tariff_book(request):
    """Read-only tariff book — browse by section or search by code/description/chapter/duty rate."""
    query = request.GET.get('q', '').strip()
    search_results = []
    search_count = 0

    if query:
        q_filter = Q()
        query_lower = query.lower()

        # Collect chapters whose titles match the query (section or chapter name)
        matched_chapters = []
        for ch_num, ch_title in _CHAPTER_TITLES.items():
            if query_lower in ch_title.lower():
                matched_chapters.append(ch_num)
        for _num, _roman, _title, _chapters in _HS_SECTIONS:
            if query_lower in _title.lower() or query.upper() == _roman:
                matched_chapters.extend(_chapters)

        # Determine if query looks like a number (HS code prefix, duty rate, or chapter)
        clean_num = query.rstrip('%').strip()
        if re.match(r'^[\d\.]+$', clean_num):
            # Could be: HS code prefix ("8471", "8471.30"), duty rate ("0", "5"), or chapter ("84")
            q_filter |= Q(code__icontains=clean_num)
            try:
                rate_val = float(clean_num)
                q_filter |= Q(duty_rate=rate_val)
            except ValueError:
                pass
            try:
                ch_num = int(clean_num)
                if 1 <= ch_num <= 99:
                    matched_chapters.append(ch_num)
            except ValueError:
                pass
        else:
            # Text search: description + possible duty rate suffix
            q_filter |= Q(description__icontains=query)
            try:
                q_filter |= Q(duty_rate=float(clean_num))
            except ValueError:
                pass

        # Include all HS codes from matched chapters
        for ch in set(matched_chapters):
            q_filter |= Q(chapter__icontains=str(ch).zfill(2))
            q_filter |= Q(chapter__icontains=str(ch))

        raw_results = list(
            HSCode.objects.filter(q_filter, is_active=True).order_by('code')[:60]
        )
        # Annotate with resolved chapter number for template URL building.
        # Must NOT use a leading underscore — Django templates block _xxx attributes.
        for hs in raw_results:
            hs.chapter_num_resolved = _chapter_num(hs.chapter)
        search_results = raw_results
        search_count = len(search_results)

    # Always build sections (used when no query and as breadcrumb context)
    hs_list = HSCode.objects.filter(is_active=True).values('chapter')
    chapter_counts = {}
    for hs in hs_list:
        ch = _chapter_num(hs['chapter'])
        if ch:
            chapter_counts[ch] = chapter_counts.get(ch, 0) + 1

    sections = []
    for num, roman, title, chapters in _HS_SECTIONS:
        total_codes = sum(chapter_counts.get(ch, 0) for ch in chapters)
        sections.append({
            'num': num,
            'roman': roman,
            'title': title,
            'total_chapters': len(chapters),
            'total_codes': total_codes,
        })
    return render(request, 'declarant/tariff_book.html', {
        'sections': sections,
        'query': query,
        'search_results': search_results,
        'search_count': search_count,
    })


@login_required
@declarant_required
def tariff_book_section(request, section_num):
    """Read-only chapter list for one tariff section."""
    section_data = next((s for s in _HS_SECTIONS if s[0] == section_num), None)
    if not section_data:
        messages.error(request, 'Section not found.')
        return redirect('declarant:tariff_book')

    num, roman, title, chapters = section_data
    hs_list = HSCode.objects.filter(is_active=True).values('chapter', 'code')
    chapter_map = {}
    for hs in hs_list:
        ch = _chapter_num(hs['chapter'])
        if ch and ch in chapters:
            chapter_map.setdefault(ch, {'count': 0, 'samples': []})
            chapter_map[ch]['count'] += 1
            if len(chapter_map[ch]['samples']) < 3:
                chapter_map[ch]['samples'].append(hs['code'])

    chapter_list = [
        {
            'num': ch,
            'num_str': str(ch).zfill(2),
            'title': _CHAPTER_TITLES.get(ch, ''),
            'count': chapter_map.get(ch, {}).get('count', 0),
            'samples': chapter_map.get(ch, {}).get('samples', []),
        }
        for ch in chapters
    ]
    return render(request, 'declarant/tariff_book_section.html', {
        'section_num': num,
        'section_roman': roman,
        'section_title': title,
        'chapters': chapter_list,
    })


@login_required
@declarant_required
def tariff_book_chapter(request, chapter_num):
    """Read-only HS code and duty-rate list for one chapter."""
    section_data = next(
        ((num, roman, title) for num, roman, title, chs in _HS_SECTIONS if chapter_num in chs),
        (None, '', '')
    )
    section_num, section_roman, section_title = section_data

    all_hs = list(HSCode.objects.filter(is_active=True).order_by('code'))
    hs_codes = [hs for hs in all_hs if _chapter_num(hs.chapter) == chapter_num]

    return render(request, 'declarant/tariff_book_chapter.html', {
        'chapter_num': chapter_num,
        'chapter_num_str': str(chapter_num).zfill(2),
        'section_num': section_num,
        'section_roman': section_roman,
        'section_title': section_title,
        'hs_codes': hs_codes,
    })


# ─── Shipment Preview (JSON for queue modal) ──────────────────────────────────
