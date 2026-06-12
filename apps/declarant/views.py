import datetime
import json
import os
import re
import tempfile
import threading
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from apps.accounts.models import User
from apps.shipments.models import HSCode, Shipment, ShipmentDocument, StatusLog
from apps.shipments.fan import FAN_ASSESSMENT_FIELDS, fan_assessment_has_values, fan_assessment_rows
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_shipment_status_change, send_assessed_email, send_billed_email
from apps.computation.ocr import process_document, _extract_line_items, _extract_hs_anchored_items
from apps.computation.models import ShipmentLineItem
from apps.supervisor.models import IssueReport
from apps.supervisor.views import _HS_SECTIONS, _chapter_num

_CHAPTER_TITLES = {
    1: 'Live animals',
    2: 'Meat and edible meat offal',
    3: 'Fish and crustaceans, molluscs and other aquatic invertebrates',
    4: "Dairy produce; birds' eggs; natural honey; edible products of animal origin, not elsewhere specified or included",
    5: 'Products of animal origin, not elsewhere specified or included',
    6: 'Live trees and other plants; bulbs, roots and the like; cut flowers and ornamental foliage',
    7: 'Edible vegetables and certain roots and tubers',
    8: 'Edible fruit and nuts; peel of citrus fruit or melons',
    9: 'Coffee, tea, mate and spices',
    10: 'Cereals',
    11: 'Products of the milling industry; malt; starches; inulin; wheat gluten',
    12: 'Oil seeds and oleaginous fruits; miscellaneous grains, seeds and fruit; industrial or medicinal plants; straw and fodder',
    13: 'Lac; gums, resins and other vegetable saps and extracts',
    14: 'Vegetable plaiting materials; vegetable products not elsewhere specified or included',
    15: 'Animal, vegetable or microbial fats and oils and their cleavage products; prepared edible fats; animal or vegetable waxes',
    16: 'Preparations of meat, of fish, crustaceans, molluscs or other aquatic invertebrates, or of insects',
    17: 'Sugars and sugar confectionery',
    18: 'Cocoa and cocoa preparations',
    19: 'Preparations of cereals, flour, starch or milk; pastrycooks products',
    20: 'Preparations of vegetables, fruit, nuts or other parts of plants',
    21: 'Miscellaneous edible preparations',
    22: 'Beverages, spirits and vinegar',
    23: 'Residues and waste from the food industries; prepared animal fodder',
    24: 'Tobacco and manufactured tobacco substitutes; nicotine products',
    25: 'Salt; sulphur; earths and stone; plastering materials, lime and cement',
    26: 'Ores, slag and ash',
    27: 'Mineral fuels, mineral oils and products of their distillation; bituminous substances; mineral waxes',
    28: 'Inorganic chemicals; organic or inorganic compounds of precious metals, rare-earth metals, radioactive elements or isotopes',
    29: 'Organic chemicals',
    30: 'Pharmaceutical products',
    31: 'Fertilisers',
    32: 'Tanning or dyeing extracts; tannins and derivatives; dyes, pigments, paints, varnishes, putty and inks',
    33: 'Essential oils and resinoids; perfumery, cosmetic or toilet preparations',
    34: 'Soap, organic surface-active agents, washing preparations, lubricating preparations, waxes and similar products',
    35: 'Albuminoidal substances; modified starches; glues; enzymes',
    36: 'Explosives; pyrotechnic products; matches; pyrophoric alloys; certain combustible preparations',
    37: 'Photographic or cinematographic goods',
    38: 'Miscellaneous chemical products',
    39: 'Plastics and articles thereof',
    40: 'Rubber and articles thereof',
    41: 'Raw hides and skins, other than furskins, and leather',
    42: 'Articles of leather; saddlery and harness; travel goods, handbags and similar containers; articles of animal gut',
    43: 'Furskins and artificial fur; manufactures thereof',
    44: 'Wood and articles of wood; wood charcoal',
    45: 'Cork and articles of cork',
    46: 'Manufactures of straw, esparto or other plaiting materials; basketware and wickerwork',
    47: 'Pulp of wood or other fibrous cellulosic material; recovered paper or paperboard',
    48: 'Paper and paperboard; articles of paper pulp, of paper or of paperboard',
    49: 'Printed books, newspapers, pictures and other products of the printing industry; manuscripts, typescripts and plans',
    50: 'Silk',
    51: 'Wool, fine or coarse animal hair; horsehair yarn and woven fabric',
    52: 'Cotton',
    53: 'Other vegetable textile fibres; paper yarn and woven fabrics of paper yarn',
    54: 'Man-made filaments; strip and the like of man-made textile materials',
    55: 'Man-made staple fibres',
    56: 'Wadding, felt and nonwovens; special yarns; twine, cordage, ropes and cables and articles thereof',
    57: 'Carpets and other textile floor coverings',
    58: 'Special woven fabrics; tufted textile fabrics; lace; tapestries; trimmings; embroidery',
    59: 'Impregnated, coated, covered or laminated textile fabrics; textile articles suitable for industrial use',
    60: 'Knitted or crocheted fabrics',
    61: 'Articles of apparel and clothing accessories, knitted or crocheted',
    62: 'Articles of apparel and clothing accessories, not knitted or crocheted',
    63: 'Other made up textile articles; sets; worn clothing and worn textile articles; rags',
    64: 'Footwear, gaiters and the like; parts of such articles',
    65: 'Headgear and parts thereof',
    66: 'Umbrellas, sun umbrellas, walking-sticks, seat-sticks, whips, riding-crops and parts thereof',
    67: 'Prepared feathers and down; artificial flowers; articles of human hair',
    68: 'Articles of stone, plaster, cement, asbestos, mica or similar materials',
    69: 'Ceramic products',
    70: 'Glass and glassware',
    71: 'Natural or cultured pearls, precious or semi-precious stones, precious metals, imitation jewellery; coin',
    72: 'Iron and steel',
    73: 'Articles of iron or steel',
    74: 'Copper and articles thereof',
    75: 'Nickel and articles thereof',
    76: 'Aluminium and articles thereof',
    77: 'Reserved for possible future use in the Harmonized System',
    78: 'Lead and articles thereof',
    79: 'Zinc and articles thereof',
    80: 'Tin and articles thereof',
    81: 'Other base metals; cermets; articles thereof',
    82: 'Tools, implements, cutlery, spoons and forks, of base metal; parts thereof',
    83: 'Miscellaneous articles of base metal',
    84: 'Nuclear reactors, boilers, machinery and mechanical appliances; parts thereof',
    85: 'Electrical machinery and equipment and parts thereof; sound recorders and reproducers; television image and sound recorders and reproducers',
    86: 'Railway or tramway locomotives, rolling-stock and parts thereof; railway or tramway track fixtures and fittings',
    87: 'Vehicles other than railway or tramway rolling-stock, and parts and accessories thereof',
    88: 'Aircraft, spacecraft, and parts thereof',
    89: 'Ships, boats and floating structures',
    90: 'Optical, photographic, cinematographic, measuring, checking, precision, medical or surgical instruments and apparatus',
    91: 'Clocks and watches and parts thereof',
    92: 'Musical instruments; parts and accessories of such articles',
    93: 'Arms and ammunition; parts and accessories thereof',
    94: 'Furniture; bedding, mattresses, cushions and similar stuffed furnishings; lamps and lighting fittings; illuminated signs; prefabricated buildings',
    95: 'Toys, games and sports requisites; parts and accessories thereof',
    96: 'Miscellaneous manufactured articles',
    97: 'Works of art, collectors pieces and antiques',
}

ETRADE_LODGEMENT_URL = 'https://www.etrade.net.ph/etrade-2.0/login/auth'


# ─── Role Decorator ───────────────────────────────────────────────────────────

def declarant_required(view_func):
    """Restrict view to authenticated users with role='declarant'."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != 'declarant':
            messages.error(request, 'Access denied — declarants only.')
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ─── Helpers ─────────────────────────────────────────────────────────────────

URGENCY_BUSINESS_DAYS = {
    'rush': 3, 'urgent': 5, 'priority': 10, 'standard': 15, 'normal': 15,
}


def _fan_amount(value):
    cleaned = re.sub(r'[^0-9.]', '', str(value or ''))
    return cleaned


def _urgency_business_days():
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
    values['normal'] = values['standard']
    return values


def _urgency_days_for(urgency):
    return _urgency_business_days().get(urgency or 'standard', URGENCY_BUSINESS_DAYS['standard'])


def _add_business_days(start_dt, n):
    """Return date that is n business days (Mon–Fri) after start_dt."""
    d = start_dt.date() if hasattr(start_dt, 'date') else start_dt
    added = 0
    while added < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            added += 1
    return d


def _business_days_diff(from_date, to_date):
    """Signed count of business days from from_date to to_date.
    Positive = future (days left), negative = past (overdue)."""
    from_date = from_date.date() if hasattr(from_date, 'date') else from_date
    to_date   = to_date.date()   if hasattr(to_date,   'date') else to_date
    if from_date == to_date:
        return 0
    sign = 1 if to_date > from_date else -1
    a, b = (from_date, to_date) if to_date > from_date else (to_date, from_date)
    count, d = 0, a
    while d < b:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return sign * count


def _annotate_due(shipments, today):
    """Attach due_date, due_days_left (business days), due_color per shipment."""
    urgency_days = _urgency_business_days()
    for s in shipments:
        alloc = urgency_days.get(s.urgency or 'standard', urgency_days['standard'])
        s.due_date      = _add_business_days(s.submitted_at, alloc)
        s.due_days_left = _business_days_diff(today, s.due_date)
        if s.due_days_left < 0:
            s.due_color = 'red'
        elif s.due_days_left <= 1:
            s.due_color = 'orange'
        else:
            s.due_color = 'green'


def _send_overdue_emails(overdue_shipments, today):
    """
    For each overdue shipment not yet notified today:
    - Email all supervisors
    - Email the assigned declarant
    Runs in a background thread; marks overdue_notified_at = today.
    """
    from apps.accounts.models import User

    def _do_send():
        supervisors = list(
            User.objects.filter(role='supervisor', is_active=True)
                        .exclude(email='')
                        .values_list('email', flat=True)
        )

        for shipment in overdue_shipments:
            days_over   = abs(shipment.due_days_left)
            urgency_lbl = shipment.get_urgency_display()
            consignee   = shipment.consignee.get_full_name() or shipment.consignee.username
            declarant   = (shipment.declarant.get_full_name() or shipment.declarant.username
                           ) if shipment.declarant else 'Unassigned'

            subject = (
                f'⚠️ Overdue Shipment — {shipment.hawb_number} '
                f'({days_over} business day{"s" if days_over != 1 else ""} overdue)'
            )

            body = (
                f'This is an automated overdue alert from R3-PCR.\n\n'
                f'Shipment Reference : {shipment.hawb_number}\n'
                f'Urgency Level      : {urgency_lbl}\n'
                f'Consignee          : {consignee}\n'
                f'Assigned Declarant : {declarant}\n'
                f'Days Overdue       : {days_over} business day{"s" if days_over != 1 else ""}\n'
                f'Due Date           : {shipment.due_date}\n\n'
                f'This shipment has passed its processing deadline. '
                f'Failure to process promptly may result in Demurrage & Detention (D&D) '
                f'charges and potential Bureau of Customs (BOC) penalties.\n\n'
                f'Please log in to R3-PCR and take action immediately.\n\n'
                f'— R3-PCR Automated Alert'
            )

            # Collect recipients
            recipients = list(supervisors)
            if (shipment.declarant
                    and shipment.declarant.email
                    and shipment.declarant.email not in recipients):
                recipients.append(shipment.declarant.email)

            if recipients:
                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=recipients,
                        fail_silently=True,
                    )
                except Exception:
                    pass

            # Mark notified today (outside the email try so it always saves)
            Shipment.objects.filter(pk=shipment.pk).update(overdue_notified_at=today)

    thread = threading.Thread(target=_do_send, daemon=True)
    thread.start()


def _run_and_store_document_ocr(doc):
    ext = os.path.splitext(doc.file.name)[1] or '.pdf'
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        doc.file.open('rb')
        tmp.write(doc.file.read())
        doc.file.close()
        tmp_path = tmp.name
    try:
        fields, raw_text, quality = process_document(tmp_path, doc.document_type)
        doc.ocr_text = raw_text or ''
        doc.ocr_fields_json = json.dumps(fields or {}, default=str)
        doc.ocr_quality = quality
        doc.ocr_ran_at = timezone.now()
        doc.save(update_fields=['ocr_text', 'ocr_fields_json', 'ocr_quality', 'ocr_ran_at'])
        return fields or {}, raw_text or '', quality
    finally:
        os.unlink(tmp_path)


def _ocr_display_documents(documents):
    display = []
    for doc in documents:
        fields = []
        if doc.ocr_fields_json:
            try:
                data = json.loads(doc.ocr_fields_json)
            except (TypeError, ValueError):
                data = {}
            for key, field in data.items():
                if key.startswith('__'):
                    continue
                value = field.get('value') if isinstance(field, dict) else field
                confidence = field.get('confidence', 0) if isinstance(field, dict) else 0
                if value:
                    fields.append({
                        'name': key.replace('_', ' ').title(),
                        'value': value,
                        'confidence': float(confidence or 0),
                    })
        display.append({'doc': doc, 'fields': fields})
    return display


# ─── Dashboard ────────────────────────────────────────────────────────────────

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

    my_records = list(my_shipments.order_by('-submitted_at')[:6])

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

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        category = request.POST.get('category', '').strip()
        location = request.POST.get('location', '').strip()
        priority = request.POST.get('priority', 'normal').strip()
        description = request.POST.get('description', '').strip()
        shipment_id = request.POST.get('related_shipment', '').strip()

        valid_categories = {choice[0] for choice in IssueReport.CATEGORY_CHOICES}
        valid_locations = {choice[0] for choice in IssueReport.LOCATION_CHOICES}
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
        'category_choices': IssueReport.CATEGORY_CHOICES,
        'location_choices': IssueReport.LOCATION_CHOICES,
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
    """View WMCDA criteria weights and configuration."""
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

@login_required
@declarant_required
def shipment_preview(request, shipment_id):
    """Return JSON details for the queue preview modal (incoming shipments only)."""
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Documents list
    docs = []
    for doc in shipment.documents.all():
        docs.append({
            'type':  doc.get_document_type_display(),
            'name':  doc.file.name.split('/')[-1],
            'url':   doc.file.url,
        })

    # Line items from DutyComputation if it exists
    items = []
    computation = getattr(shipment, 'computation', None)
    if computation and computation.items_json:
        try:
            items = json.loads(computation.items_json)
        except (ValueError, TypeError):
            items = []

    data = {
        'hawb':            shipment.hawb_number,
        'consignee':       shipment.consignee.get_full_name() or shipment.consignee.username,
        'import_type':     shipment.get_import_type_display(),
        'shipment_type':   shipment.get_shipment_type_display() if shipment.shipment_type else None,
        'urgency':         shipment.urgency,
        'urgency_label':   shipment.get_urgency_display(),
        'description':     shipment.description or '',
        'quantity':        str(shipment.quantity) if shipment.quantity else None,
        'invoice_currency': shipment.invoice_currency or 'USD',
        'declared_value':  str(shipment.declared_value) if shipment.declared_value else None,
        'gross_weight':    str(shipment.gross_weight) if shipment.gross_weight else None,
        'freight_cost':    str(shipment.freight_cost) if shipment.freight_cost else None,
        'insurance_cost':  str(shipment.insurance_cost) if shipment.insurance_cost else None,
        'submitted_at':    shipment.submitted_at.strftime('%b %d, %Y %H:%M'),
        'documents':       docs,
        'items':           items,
    }
    return JsonResponse(data)


# ─── Queue Manager ────────────────────────────────────────────────────────────

@login_required
@declarant_required
def queue_manager(request):
    today = timezone.localdate()

    # Incoming queue with optional filters
    pending_qs = Shipment.objects.filter(status='incoming').select_related('consignee')

    urgency_filter = request.GET.get('urgency', '')
    if urgency_filter in ('standard', 'priority', 'urgent', 'rush', 'normal'):
        pending_qs = pending_qs.filter(urgency=urgency_filter)

    pending = list(pending_qs)
    _annotate_due(pending, today)

    # Send overdue email alerts (once per day per shipment)
    newly_overdue = [
        s for s in pending
        if s.due_days_left < 0
        and s.overdue_notified_at != today
    ]
    if newly_overdue:
        _send_overdue_emails(newly_overdue, today)

    # Due-within server-side filter
    due_filter = request.GET.get('due', '')
    if due_filter:
        try:
            max_days = int(due_filter)
            pending = [s for s in pending if s.due_days_left <= max_days]
        except ValueError:
            pass

    # Paginate pending queue — 25 per page
    paginator    = Paginator(pending, 25)
    page_number  = request.GET.get('page', 1)
    pending_page = paginator.get_page(page_number)

    # In-review: all active shipments from arrived through released
    in_review = Shipment.objects.filter(
        declarant=request.user,
        status__in=['arrived', 'computed', 'approved', 'rejected', 'for_revision',
                    'lodgement', 'ongoing', 'assessed', 'paid', 'released'],
    ).select_related('consignee').prefetch_related('computation').order_by('-updated_at')

    # Processed: only fully billed shipments
    history = Shipment.objects.filter(
        declarant=request.user,
        status='billed',
    ).select_related('consignee').order_by('-updated_at')

    context = {
        'pending':        pending_page,   # now a Page object; templates use pending.object_list
        'paginator':      paginator,
        'in_review':      in_review,
        'history':        history,
        'urgency_filter': urgency_filter,
        'due_filter':     due_filter,
    }
    return render(request, 'declarant/queue.html', context)


# ─── Claim Shipment ───────────────────────────────────────────────────────────

@login_required
@declarant_required
def claim_shipment(request, shipment_id):
    """Any active declarant may claim an unclaimed incoming shipment."""
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.status == 'incoming':
        shipment.declarant = request.user
        shipment.status = 'arrived'
        shipment.save()
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status='incoming',
            new_status='arrived',
            notes='Claimed by declarant',
        )
        notify_shipment_status_change(
            shipment=shipment,
            old_status='incoming',
            new_status='arrived',
            changed_by=request.user,
            notes='Claimed by declarant.',
        )
        if False:
            create_notification(
            recipient=shipment.consignee,
            shipment=shipment,
            notification_type='status_update',
            title=f'Shipment {shipment.hawb_number} — Now Under Review',
            message=f'Your shipment {shipment.hawb_number} is being reviewed by a declarant.',
        )
        messages.success(request, f'Shipment {shipment.hawb_number} claimed.')
        # "Claim & Process" from preview modal — go straight to process page
        if request.POST.get('next') == 'process':
            return redirect('declarant:process', shipment_id=shipment_id)
    else:
        messages.error(request, 'Shipment is no longer available.')
    return redirect('declarant:queue')


# ─── Synchronous OCR scan (called from queue "Process →" before redirect) ────

@login_required
@declarant_required
def run_ocr_sync(request, shipment_id):
    """Run OCR on all unscanned documents in parallel, then return when done.
    Called via fetch() from the queue page loading overlay.
    Returns JSON {done: true, scanned: N} when complete.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)

    docs_to_scan = [
        doc for doc in shipment.documents.filter(
            document_type__in=['invoice', 'airway_bill', 'packing_list']
        )
        if not doc.ocr_ran_at
    ]

    if not docs_to_scan:
        return JsonResponse({'done': True, 'scanned': 0, 'already_done': True})

    # Run all documents simultaneously — 3 docs take the time of the slowest one
    # instead of 3× the slowest one.
    scanned = 0
    def _scan(doc):
        try:
            _run_and_store_document_ocr(doc)
            return True
        except Exception as e:
            print(f'[OCR-SYNC] Failed for doc {doc.id} ({doc.document_type}): {e}')
            return False

    with ThreadPoolExecutor(max_workers=len(docs_to_scan)) as executor:
        futures = [executor.submit(_scan, doc) for doc in docs_to_scan]
        for future in as_completed(futures):
            if future.result():
                scanned += 1

    return JsonResponse({'done': True, 'scanned': scanned})


# ─── Process Shipment ─────────────────────────────────────────────────────────

@login_required
@declarant_required
def process_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may access the process page
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    documents = shipment.documents.all()
    # Check if any docs still need OCR (e.g. declarant navigated directly, skipping the queue flow)
    _pending_ocr = [
        doc for doc in documents
        if doc.document_type in ('invoice', 'airway_bill', 'packing_list') and not doc.ocr_ran_at
    ]
    has_pending_ocr = bool(_pending_ocr)  # kept for template auto-reload fallback

    status_logs = shipment.status_logs.order_by('-changed_at')[:5]

    # ── Extract OCR line items from scanned documents (for review panel) ────────
    ocr_items_from_docs = []

    def _ocr_desc_key(value):
        value = re.sub(r'\b\d{4}(?:[\s.]?\d{2}){1,3}\b', ' ', str(value or '').lower())
        value = re.sub(r'[^a-z0-9]+', ' ', value)
        return ' '.join(value.split())

    def _ocr_num(value):
        raw = re.sub(r'[^\d.\-]', '', str(value or ''))
        if not raw:
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    def _ocr_numbers_match(left, right, tolerance=Decimal('0.01')):
        l_val, r_val = _ocr_num(left), _ocr_num(right)
        if l_val is None or r_val is None:
            return False
        return abs(l_val - r_val) <= tolerance

    def _ocr_desc_similar(left, right):
        left_key, right_key = _ocr_desc_key(left), _ocr_desc_key(right)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        left_words, right_words = set(left_key.split()), set(right_key.split())
        overlap = len(left_words & right_words) / max(len(left_words | right_words), 1)
        return overlap >= 0.72

    def _merge_ocr_item(target, incoming):
        for key in ('total_value', 'unit_price', 'doc_hs_code'):
            if incoming.get(key) and (not target.get(key) or incoming.get('source_doc') == 'invoice'):
                target[key] = incoming.get(key)
        for key in ('gross_weight', 'net_weight', 'num_packages'):
            if incoming.get(key) and (not target.get(key) or incoming.get('source_doc') == 'packing_list'):
                target[key] = incoming.get(key)
        for key in ('quantity', 'unit', 'raw_extracted_text'):
            if incoming.get(key) and not target.get(key):
                target[key] = incoming.get(key)
        if incoming.get('confidence_pct', 0) > target.get('confidence_pct', 0):
            target['confidence_pct'] = incoming['confidence_pct']
        sources = {
            src.strip()
            for src in f"{target.get('source_doc', '')},{incoming.get('source_doc', '')}".split(',')
            if src.strip()
        }
        target['source_doc'] = ', '.join(sorted(sources))

    def _add_ocr_item(item, doc_type):
        desc = (item.get('description') or '').strip()
        if not desc:
            return
        incoming = dict(
            item,
            source_doc=doc_type,
            raw_extracted_text=(
                item.get('raw_extracted_text')
                or item.get('raw_text')
                or item.get('description')
                or ''
            ),
            confidence_pct=round(float(item.get('confidence', 0)) * 100, 1),
        )
        for existing in ocr_items_from_docs:
            same_hs = (
                incoming.get('doc_hs_code') and existing.get('doc_hs_code')
                and re.sub(r'\D', '', str(incoming.get('doc_hs_code'))) == re.sub(r'\D', '', str(existing.get('doc_hs_code')))
            )
            same_qty = _ocr_numbers_match(incoming.get('quantity'), existing.get('quantity'))
            same_value = _ocr_numbers_match(incoming.get('total_value'), existing.get('total_value'))
            if _ocr_desc_similar(desc, existing.get('description')) and (same_hs or same_qty or same_value):
                _merge_ocr_item(existing, incoming)
                return
        ocr_items_from_docs.append(incoming)
    _priority_doc_types = ['invoice', 'packing_list', 'airway_bill']
    _docs_by_type = {}
    for _d in documents:
        if _d.document_type in _priority_doc_types and _d.ocr_ran_at and (
            _d.ocr_fields_json or getattr(_d, 'ocr_text', None)
        ):
            _docs_by_type.setdefault(_d.document_type, []).append(_d)
    for _doc_type in _priority_doc_types:
        for _doc in _docs_by_type.get(_doc_type, []):
            # Always re-extract from the stored raw OCR text using the latest
            # _extract_line_items logic (which includes IBAN/banking filters,
            # improved patterns, etc.). This avoids serving stale cached __items__
            # that may contain junk like "IBAN: DE26" from old extractions.
            _items_from_json = []
            if getattr(_doc, 'ocr_text', None):
                _items_from_json = _extract_line_items(_doc.ocr_text)
            for _item in _items_from_json:
                _add_ocr_item(_item, _doc_type)

    # ── Fallback: if no items extracted by pattern matching, use the
    # HS-code-anchored extractor which walks backwards from "HS CODE: XXXX"
    # labels to reconstruct descriptions. This handles multi-line item
    # formats that _extract_line_items patterns don't match.
    if not ocr_items_from_docs:
        for _doc_type in _priority_doc_types:
            for _doc in _docs_by_type.get(_doc_type, []):
                if not getattr(_doc, 'ocr_text', None):
                    continue
                _fallback = _extract_hs_anchored_items(_doc.ocr_text)
                for _item in _fallback:
                    _add_ocr_item(_item, _doc_type)
            if ocr_items_from_docs:
                break  # stop at first document type that yields results

    # ── HS suggestions from raw OCR text ────────────────────────────────────────
    # Two-pass approach:
    # Pass 1 (highest confidence): extract HS codes EXPLICITLY printed in the
    #   document — e.g. "HS CODE: 49111010" or "4911.10.00".  These are looked up
    #   directly in the tariff table and pinned at the top of the recommendations.
    # Pass 2: keyword-based matching on the full raw text for any remaining slots.
    ocr_hs_suggestions = []
    _ocr_raw_parts = []
    for _doc_type in ['invoice', 'packing_list', 'airway_bill']:
        for _doc in _docs_by_type.get(_doc_type, []):
            _rt = getattr(_doc, 'ocr_text', None)
            if _rt:
                _ocr_raw_parts.append(_rt[:3000])

    if _ocr_raw_parts:
        try:
            from apps.computation.views import (
                extract_document_hs_codes as _extract_document_hs_codes,
                find_hs_by_document_code as _find_hs_by_document_code,
                suggest_hs_codes as _suggest_hs_codes,
            )
            _combined_ocr = ' '.join(_ocr_raw_parts)[:5000]
            _seen_hs_ids = set()
            _pinned = []

            # ── Pass 1: explicit HS code patterns in the document ────────────
            # Handles common OCR variants:
            # "HS CODE: 49111010", "H.S. Code 4911 10 10",
            # "Tariff Code: 4911.10.00", and standalone dotted/spaced codes.
            for _raw in _extract_document_hs_codes(_combined_ocr):
                _hs_obj = _find_hs_by_document_code(_raw)
                if _hs_obj and _hs_obj.id not in _seen_hs_ids:
                    _pinned.append(_hs_obj)
                    _seen_hs_ids.add(_hs_obj.id)
            # ── Pass 2: keyword-based matches to fill remaining slots ─────────
            _kw = _suggest_hs_codes(_combined_ocr, top_n=8)
            for _hs in _kw:
                if _hs.id not in _seen_hs_ids:
                    _pinned.append(_hs)
                    _seen_hs_ids.add(_hs.id)

            ocr_hs_suggestions = _pinned[:10]

        except Exception as _e:
            print(f'[HS-OCR] suggestion error: {_e}')

    ocr_fields = None
    if request.session.get('ocr_shipment_id') == shipment_id:
        ocr_fields = request.session.get('ocr_fields')

    # OCR toast survives fetch→reload cycle (Django messages don't)
    ocr_toast = request.session.pop('ocr_toast', None)

    has_pending_ocr = bool(_pending_ocr)

    from apps.supervisor.models import SystemConfig
    vasp_url     = SystemConfig.get('vasp_url', ETRADE_LODGEMENT_URL)
    sad_document = shipment.documents.filter(document_type='sad').first()
    fan_rows = fan_assessment_rows(sad_document)

    context = {
        'shipment':            shipment,
        'documents':           documents,
        'status_logs':         status_logs,
        'ocr_fields':          ocr_fields,
        'ocr_documents':       _ocr_display_documents(documents),
        'ocr_items_from_docs': ocr_items_from_docs,
        'ocr_hs_suggestions':  ocr_hs_suggestions,
        'ocr_toast':           ocr_toast,
        'has_pending_ocr':     has_pending_ocr,
        'manual_status_choices': Shipment.MANUAL_STATUS_CHOICES,
        'status_steps':        build_status_progress(shipment.status, 'declarant'),
        'vasp_url':            vasp_url,
        'etrade_lodgement_url': ETRADE_LODGEMENT_URL,
        'sad_document':        sad_document,
        'fan_assessment_rows': fan_rows,
        'fan_assessment_has_values': fan_assessment_has_values(fan_rows),
    }
    return render(request, 'declarant/process.html', context)


# ─── Update Shipping Mode ─────────────────────────────────────────────────────

@login_required
@declarant_required
def update_shipping_mode(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may update the shipping mode
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    mode = request.POST.get('shipment_type', '').strip()
    if mode in ('lcl', 'fcl'):
        shipment.shipment_type = mode
        shipment.save()
        messages.success(request, f'Shipping mode refined to "{shipment.get_shipment_type_display()}".')
    else:
        messages.error(request, 'Please select LCL or FCL.')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Update Status ────────────────────────────────────────────────────────────

@login_required
@declarant_required
def proceed_to_lodgement(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status != 'approved':
        messages.error(request, 'Only approved ECDT shipments can proceed to BOC lodgement.')
        return redirect('declarant:process', shipment_id=shipment_id)

    old_status = shipment.status
    shipment.status = 'lodgement'
    shipment.save(update_fields=['status', 'updated_at'])

    StatusLog.objects.create(
        shipment=shipment,
        changed_by=request.user,
        old_status=old_status,
        new_status='lodgement',
        notes='Declarant proceeded to BOC lodgement through eTrade.',
    )

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'BOC Lodgement Started - {shipment.hawb_number}',
        message=(
            f'Your shipment {shipment.hawb_number} has been filed for BOC lodgement. '
            f'Your declarant will update the status once it is lined up for final assessment.'
        ),
    )

    messages.success(request, 'Shipment marked for BOC lodgement. Continue lodgement in eTrade.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def update_status(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:queue')

    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may change shipment status
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    new_status = request.POST.get('new_status', '').strip()
    notes      = request.POST.get('notes', '').strip()

    # Validate status against known choices — prevents arbitrary string injection
    valid_statuses = Shipment.MANUAL_STATUS_KEYS
    if not new_status or new_status not in valid_statuses:
        messages.error(request, 'Invalid status selected.')
        return redirect('declarant:process', shipment_id=shipment_id)

    old_status = shipment.status
    shipment.status = new_status

    # Record processing timestamp when shipment reaches a terminal state
    shipment.save()

    StatusLog.objects.create(
        shipment=shipment,
        changed_by=request.user,
        old_status=old_status,
        new_status=new_status,
        notes=notes or 'Status updated by declarant',
    )

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'Shipment {shipment.hawb_number} — Status Updated',
        message=(
            f'Your shipment status changed to '
            f'"{shipment.get_status_display()}". '
            f'{notes}'
        ).strip(),
    )

    if old_status != new_status and new_status == 'assessed':
        send_assessed_email(shipment)
    if old_status != new_status and new_status == 'billed':
        send_billed_email(shipment)

    messages.success(request, f'Status updated to "{shipment.get_status_display()}".')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Payment Confirmation (legacy — redirects to process page) ───────────────

@login_required
@declarant_required
def payment_confirmation(request, shipment_id):
    """Payment happens outside the system. Declarant uses update_status to mark paid."""
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Upload FAN Document ──────────────────────────────────────────────────────

def _process_fan_document_ocr(fan_doc):
    """Run FAN OCR from local or remote storage-backed files."""
    temp_path = None
    try:
        try:
            source_path = fan_doc.file.path
        except NotImplementedError:
            suffix = os.path.splitext(fan_doc.file.name or '')[1] or '.bin'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in fan_doc.file.chunks():
                    tmp.write(chunk)
                temp_path = tmp.name
            source_path = temp_path

        fields, raw_text, quality = process_document(source_path, 'sad')
        fan_doc.ocr_text = raw_text
        fan_doc.ocr_fields_json = json.dumps(fields)
        fan_doc.ocr_quality = quality
        fan_doc.ocr_ran_at = timezone.now()
        fan_doc.save(update_fields=['ocr_text', 'ocr_fields_json', 'ocr_quality', 'ocr_ran_at'])
        return True
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


@login_required
@declarant_required
def upload_sad(request, shipment_id):
    """Declarant uploads the FAN Document and advances ongoing shipments to assessed."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status not in ('ongoing', 'assessed', 'paid', 'released', 'billed'):
        messages.error(request, 'FAN Document can only be uploaded once shipment is ongoing or assessed.')
        return redirect('declarant:process', shipment_id=shipment_id)

    file = request.FILES.get('sad_file')
    if not file:
        messages.error(request, 'Please select a file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    old_status = shipment.status

    # Replace any existing FAN document
    shipment.documents.filter(document_type='sad').delete()
    fan_doc = ShipmentDocument.objects.create(
        shipment=shipment,
        document_type='sad',
        file=file,
    )
    ocr_ok = False
    try:
        ocr_ok = _process_fan_document_ocr(fan_doc)
    except Exception as exc:
        print(f'[FAN OCR] failed for shipment {shipment.id}: {exc}')

    if old_status == 'ongoing':
        shipment.status = 'assessed'
        shipment.save(update_fields=['status', 'updated_at'])
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='assessed',
            notes='FAN Document uploaded by declarant.',
        )
        send_assessed_email(shipment)

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'FAN Document Available — {shipment.hawb_number}',
        message=(
            'The FAN Document has been uploaded by your declarant. '
            'Please check your shipment details for the official BOC assessment amount.'
        ),
    )

    if old_status == 'ongoing':
        messages.success(request, 'FAN Document uploaded. Shipment status updated to assessed and the consignee has been notified.')
    else:
        messages.success(request, 'FAN Document uploaded. The consignee has been notified.')
    if not ocr_ok:
        messages.warning(request, 'FAN OCR could not prefill the assessment breakdown. Please encode the values manually from the uploaded document.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def save_fan_assessment(request, shipment_id):
    """Declarant verifies/overrides the OCR assessment breakdown from FAN."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    fan_doc = shipment.documents.filter(document_type='sad').first()
    if not fan_doc:
        messages.error(request, 'Upload the FAN Document before saving an assessment breakdown.')
        return redirect('declarant:process', shipment_id=shipment_id)

    try:
        data = json.loads(fan_doc.ocr_fields_json or '{}')
    except Exception:
        data = {}

    for key, _label in FAN_ASSESSMENT_FIELDS:
        data[key] = {
            'value': _fan_amount(request.POST.get(key)),
            'confidence': 1.0,
            'verified': True,
        }

    data['_verified_by'] = request.user.get_full_name() or request.user.username
    data['_verified_at'] = timezone.now().isoformat()
    fan_doc.ocr_fields_json = json.dumps(data)
    fan_doc.save(update_fields=['ocr_fields_json'])

    messages.success(request, 'FAN assessment breakdown saved.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def upload_supporting_document(request, shipment_id, stage):
    """Upload post-assessment documents and advance the shipment when appropriate."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    stages = {
        'payment': {
            'allowed': {'assessed', 'paid', 'released', 'billed'},
            'doc_type': 'payment_proof',
            'next_status': 'paid',
            'from_status': 'assessed',
            'title': 'BOC / eTrade Payment Receipt Available',
            'label': 'BOC / eTrade payment receipt',
            'message': 'The official BOC / eTrade payment receipt has been uploaded for your shipment.',
        },
        'release': {
            'allowed': {'paid', 'released', 'billed'},
            'doc_type': 'release_doc',
            'next_status': 'released',
            'from_status': 'paid',
            'title': 'Release Documents Available',
            'label': 'release / delivery document',
            'message': 'Release or delivery documents have been uploaded for your shipment.',
        },
        'billing': {
            'allowed': {'released', 'billed'},
            'doc_type': 'billing_doc',
            'next_status': 'billed',
            'from_status': 'released',
            'title': 'Final Billing Documents Available',
            'label': 'final billing document',
            'message': 'Final billing or completion documents have been uploaded for your shipment.',
        },
    }
    config = stages.get(stage)
    if not config:
        messages.error(request, 'Invalid document upload stage.')
        return redirect('declarant:process', shipment_id=shipment_id)

    if shipment.status not in config['allowed']:
        messages.error(request, f'{config["label"].title()} uploads are not available for the current shipment status.')
        return redirect('declarant:process', shipment_id=shipment_id)

    files = request.FILES.getlist('support_files') or request.FILES.getlist('receipt_files')
    if not files:
        messages.error(request, 'Please select at least one file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    for f in files:
        ShipmentDocument.objects.create(
            shipment=shipment,
            document_type=config['doc_type'],
            file=f,
        )

    old_status = shipment.status
    new_status = config['next_status']
    if old_status == config['from_status']:
        shipment.status = new_status
        shipment.save(update_fields=['status', 'updated_at'])
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status=new_status,
            notes=f'{config["label"].title()} uploaded by declarant.',
        )
        if new_status == 'billed':
            send_billed_email(shipment)

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'{config["title"]} - {shipment.hawb_number}',
        message=(
            f'{config["message"]} '
            'Please review your shipment details to view the uploaded documents.'
        ),
    )

    messages.success(request, f'{len(files)} {config["label"]}(s) uploaded. The consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def upload_receipt(request, shipment_id):
    """Declarant uploads billing receipts / payment proof when billing a shipment."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status != 'billed':
        messages.error(request, 'Billing receipts can only be uploaded once the shipment is billed.')
        return redirect('declarant:process', shipment_id=shipment_id)

    files = request.FILES.getlist('receipt_files')
    if not files:
        messages.error(request, 'Please select at least one file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    for f in files:
        ShipmentDocument.objects.create(
            shipment=shipment,
            document_type='receipt',
            file=f,
        )

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'Billing Documents Available — {shipment.hawb_number}',
        message=(
            'Your declarant has uploaded billing receipts for your shipment. '
            'Please review your shipment details to view and confirm the billing documents.'
        ),
    )

    messages.success(request, f'{len(files)} billing receipt(s) uploaded. The consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Flag Document Deficiency ────────────────────────────────────────────────

@login_required
@declarant_required
def flag_deficiency(request, shipment_id):
    """Flag a document deficiency and notify the consignee."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    deficiency_type  = request.POST.get('deficiency_type', '').strip()
    deficiency_notes = request.POST.get('deficiency_notes', '').strip()

    if not deficiency_type:
        messages.error(request, 'Please select a deficiency type.')
        return redirect('declarant:process', shipment_id=shipment_id)

    type_labels = {
        'missing_invoice': 'Missing Commercial Invoice',
        'missing_packing': 'Missing Packing List',
        'missing_awb':     'Missing Airway Bill / Bill of Lading',
        'incorrect_values':'Incorrect Declared Values',
        'illegible_doc':   'Illegible / Poor Quality Document',
        'missing_other':   'Missing Supporting Document',
        'other':           'Document Deficiency',
    }
    type_label = type_labels.get(deficiency_type, deficiency_type)
    note_text  = f'{type_label}. {deficiency_notes}'.strip('. ') if deficiency_notes else type_label

    # Save deficiency flag to shipment
    shipment.has_deficiency    = True
    shipment.deficiency_type   = deficiency_type
    shipment.deficiency_notes  = note_text
    shipment.deficiency_flagged_at = timezone.now()
    shipment.save(update_fields=['has_deficiency', 'deficiency_type', 'deficiency_notes', 'deficiency_flagged_at'])

    # Audit trail — same status, just record the flag
    StatusLog.objects.create(
        shipment=shipment,
        changed_by=request.user,
        old_status=shipment.status,
        new_status=shipment.status,
        notes=f'Deficiency flagged: {note_text}',
    )

    # Notify the consignee
    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'Document Deficiency — {shipment.hawb_number}',
        message=f'A deficiency has been flagged on your shipment: {note_text}. Please resubmit the corrected documents.',
    )

    messages.success(request, f'Deficiency flagged — consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Save Selected HS Codes → ECDT Guide ─────────────────────────────────────

@login_required
@declarant_required
def save_ocr_items(request, shipment_id):
    """
    Accept the declarant's verified OCR item rows and selected HS code IDs.
    Confirmed OCR rows are staged for the ECDT workspace, while selected HS
    codes remain available as a guide panel.
    """
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    hs_code_ids = [
        _id for _id in request.POST.getlist('hs_code_id[]')
        if _id and str(_id).strip().isdigit()
    ]

    guide_codes = []
    for hs_id in hs_code_ids:
        try:
            hs = HSCode.objects.get(id=int(hs_id), is_active=True)
            guide_codes.append({
                'id':          hs.id,
                'code':        hs.code,
                'description': hs.description,
                'duty_rate':   float(hs.duty_rate),
            })
        except HSCode.DoesNotExist:
            pass

    def _decimal(value):
        cleaned = re.sub(r'[^\d.\-]', '', str(value or ''))
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _integer(value):
        dec = _decimal(value)
        if dec is None:
            return None
        return int(dec)

    def _confidence(value):
        dec = _decimal(value)
        if dec is None:
            return Decimal('0.0')
        if dec > 1:
            dec = dec / Decimal('100')
        return max(Decimal('0.0'), min(dec, Decimal('1.0')))

    def _hs_from_post(hs_id, doc_code):
        if hs_id and str(hs_id).strip().isdigit():
            hs = HSCode.objects.filter(id=int(hs_id), is_active=True).first()
            if hs:
                return hs
        normalized = re.sub(r'\D', '', str(doc_code or ''))
        if not normalized:
            return None
        for hs in HSCode.objects.filter(is_active=True).only('id', 'code', 'duty_rate'):
            if re.sub(r'\D', '', hs.code or '') == normalized:
                return hs
        return None

    descriptions = request.POST.getlist('description[]')
    if descriptions:
        values = request.POST.getlist('total_value[]')
        quantities = request.POST.getlist('quantity[]')
        units = request.POST.getlist('unit[]')
        gross_weights = request.POST.getlist('gross_weight[]')
        net_weights = request.POST.getlist('net_weight[]')
        packages = request.POST.getlist('packages[]')
        confidences = request.POST.getlist('confidence[]')
        item_hs_ids = request.POST.getlist('item_hs_code_id[]')
        doc_hs_codes = request.POST.getlist('doc_hs_code[]')

        ShipmentLineItem.objects.filter(shipment=shipment, source='ocr').delete()
        saved_rows = 0
        for idx, description in enumerate(descriptions):
            description = (description or '').strip()
            if not description:
                continue
            hs = _hs_from_post(
                item_hs_ids[idx] if idx < len(item_hs_ids) else '',
                doc_hs_codes[idx] if idx < len(doc_hs_codes) else '',
            )
            ShipmentLineItem.objects.create(
                shipment=shipment,
                description=description,
                quantity=_decimal(quantities[idx] if idx < len(quantities) else ''),
                unit=(units[idx] if idx < len(units) else '').strip()[:30],
                total_val_usd=_decimal(values[idx] if idx < len(values) else ''),
                hs_code=hs,
                duty_rate=hs.duty_rate if hs else None,
                gross_weight=_decimal(gross_weights[idx] if idx < len(gross_weights) else ''),
                net_weight=_decimal(net_weights[idx] if idx < len(net_weights) else ''),
                packages=_integer(packages[idx] if idx < len(packages) else ''),
                confidence=_confidence(confidences[idx] if idx < len(confidences) else ''),
                is_confirmed=True,
                source='ocr',
                row_order=idx + 1,
            )
            saved_rows += 1

        if saved_rows:
            messages.success(request, f'Saved {saved_rows} verified OCR item row(s) for the ECDT workspace.')

    # Store in session — compute_shipment reads these to show the guide panel
    request.session['guide_hs_codes']    = guide_codes
    request.session['guide_shipment_id'] = str(shipment_id)
    request.session.modified = True

    from django.http import HttpResponseRedirect
    from django.urls import reverse
    return HttpResponseRedirect(
        reverse('computation:compute', kwargs={'shipment_id': shipment_id})
    )




