import datetime
import json
import os
import re
import tempfile
import threading
from collections import defaultdict
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from apps.shipments.models import HSCode, Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_shipment_status_change
from apps.computation.ocr import process_document, _extract_line_items, _extract_hs_anchored_items
from apps.computation.models import ShipmentLineItem
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

def _add_business_days(start_dt, n):
    """Return date that is n business days after start_dt."""
    d = start_dt.date() if hasattr(start_dt, 'date') else start_dt
    added = 0
    while added < n:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            added += 1
    return d


def _annotate_due(shipments, today):
    """Attach due_date, due_days_left, due_color to each shipment in-place."""
    for s in shipments:
        s.due_date = _add_business_days(s.submitted_at, 3)
        s.due_days_left = (s.due_date - today).days
        if s.due_days_left < 0:
            s.due_color = 'red'
        elif s.due_days_left <= 1:
            s.due_color = 'orange'
        else:
            s.due_color = 'green'


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

    queue_count    = shipments.filter(status='incoming').count()
    in_progress    = shipments.filter(status='arrived', **my).count()
    completed      = shipments.filter(status='approved', **my).count()
    rejected_count = shipments.filter(status='rejected', **my).count()

    # Avg processing time (approved + rejected, using updated_at as proxy)
    done_qs = list(shipments.filter(status__in=['approved', 'rejected'], **my))
    avg_processing_days = None
    if done_qs:
        total_secs = sum(
            (s.updated_at - s.submitted_at).total_seconds()
            for s in done_qs
        )
        avg_processing_days = round(total_secs / len(done_qs) / 86400, 1)

    # My completion rate: done / (done + arrived)
    total_handled = len(done_qs) + in_progress
    completion_rate = round(len(done_qs) / total_handled * 100, 1) if total_handled > 0 else 0

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
        ('land', 'Land', '#20B86F'),
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

    urgency_days_map = {'rush': 3, 'urgent': 7, 'priority': 14, 'standard': 30, 'normal': 30}
    due_buckets = {'one_day': 0, 'three_days': 0, 'five_days': 0, 'over_five': 0}
    for shipment in my_shipments.exclude(status__in=terminal_statuses):
        alloc = urgency_days_map.get(shipment.urgency or 'standard', 30)
        deadline = shipment.submitted_at + datetime.timedelta(days=alloc)
        remaining = (deadline - now).total_seconds() / 86400
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
        'queue':               queue_count,
        'in_progress':         in_progress,
        'completed':           completed,
        'rejected':            rejected_count,
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

    # Arrived: my claimed shipments (prefetch computation for inline status cards)
    in_review = Shipment.objects.filter(
        status='arrived', declarant=request.user
    ).select_related('consignee').prefetch_related('computation')

    # History: shipments I processed that are past the arrived stage
    history = Shipment.objects.filter(
        declarant=request.user,
        status__in=['computed', 'approved', 'rejected', 'for_revision', 'lodgement', 'ongoing', 'assessed', 'paid', 'released', 'billed'],
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
    _seen_ocr_desc = set()
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
                _desc = (_item.get('description') or '').strip()
                if _desc and _desc not in _seen_ocr_desc:
                    _seen_ocr_desc.add(_desc)
                    ocr_items_from_docs.append(dict(
                        _item,
                        source_doc=_doc_type,
                        raw_extracted_text=(
                            _item.get('raw_extracted_text')
                            or _item.get('raw_text')
                            or _item.get('description')
                            or ''
                        ),
                        confidence_pct=round(float(_item.get('confidence', 0)) * 100, 1),
                    ))

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
                    _desc = (_item.get('description') or '').strip()
                    if _desc and _desc not in _seen_ocr_desc:
                        _seen_ocr_desc.add(_desc)
                        ocr_items_from_docs.append(dict(
                            _item,
                            source_doc=_doc_type,
                            raw_extracted_text=_desc,
                            confidence_pct=round(float(_item.get('confidence', 0)) * 100, 1),
                        ))
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
            from apps.computation.views import suggest_hs_codes as _suggest_hs_codes
            _combined_ocr = ' '.join(_ocr_raw_parts)[:5000]
            _seen_hs_ids = set()
            _pinned = []

            # ── Pass 1: explicit HS code patterns in the document ────────────
            # Matches:  "HS CODE: 49111010"  /  "HS CODE: 4911.10.10"
            #           standalone dotted    "4911.10.00"
            #           bare 8-digit         "49111010"
            _hs_from_text = re.findall(
                r'HS\s*(?:CODE)?\s*[:\-]?\s*([\d\.]{6,14})',
                _combined_ocr, re.IGNORECASE
            )
            # Also catch bare 8–10 digit runs not already captured
            _hs_from_text += re.findall(r'\b(\d{8,10})\b', _combined_ocr)

            for _raw in _hs_from_text:
                _digits = re.sub(r'[\.\s]', '', _raw.strip())
                if len(_digits) < 6:
                    continue
                # Normalise to dotted format: 49111010 → 4911.10.10
                if len(_digits) == 8:
                    _norm = f'{_digits[:4]}.{_digits[4:6]}.{_digits[6:]}'
                elif len(_digits) == 10:
                    _norm = f'{_digits[:4]}.{_digits[4:6]}.{_digits[6:8]}.{_digits[8:]}'
                else:
                    _norm = _raw.strip()

                _hs_obj = (
                    HSCode.objects.filter(code=_norm, is_active=True).first()
                    or HSCode.objects.filter(code__startswith=_norm[:7], is_active=True).first()
                    or HSCode.objects.filter(code__startswith=_digits[:4], is_active=True).first()
                )
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
    vasp_url     = SystemConfig.get('vasp_url', 'https://ictsi-vasp.com.ph')
    sad_document = shipment.documents.filter(document_type='sad').first()

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
        'sad_document':        sad_document,
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

    messages.success(request, f'Status updated to "{shipment.get_status_display()}".')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Payment Confirmation (legacy — redirects to process page) ───────────────

@login_required
@declarant_required
def payment_confirmation(request, shipment_id):
    """Payment happens outside the system. Declarant uses update_status to mark paid."""
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Upload SAD Document ──────────────────────────────────────────────────────

@login_required
@declarant_required
def upload_sad(request, shipment_id):
    """Declarant uploads the Single Administrative Document at assessed stage."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status not in ('assessed', 'paid', 'released', 'billed'):
        messages.error(request, 'SAD can only be uploaded once shipment is assessed.')
        return redirect('declarant:process', shipment_id=shipment_id)

    file = request.FILES.get('sad_file')
    if not file:
        messages.error(request, 'Please select a file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    # Replace any existing SAD document
    shipment.documents.filter(document_type='sad').delete()
    ShipmentDocument.objects.create(
        shipment=shipment,
        document_type='sad',
        file=file,
    )

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'SAD Document Available — {shipment.hawb_number}',
        message=(
            'The Single Administrative Document (SAD) has been uploaded by your declarant. '
            'Please check your shipment details for the official BOC assessment amount.'
        ),
    )

    messages.success(request, 'SAD document uploaded. The consignee has been notified.')
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


# ─── Save Confirmed OCR Items → ECDT ─────────────────────────────────────────

@login_required
@declarant_required
def save_ocr_items(request, shipment_id):
    """Accept declarant-reviewed OCR item rows, store in session, redirect to ECDT."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    descriptions = request.POST.getlist('description[]')
    quantities   = request.POST.getlist('quantity[]')
    units        = request.POST.getlist('unit[]')
    total_values = request.POST.getlist('total_value[]')
    hs_code_ids  = request.POST.getlist('hs_code_id[]')
    hs_rates     = request.POST.getlist('hs_duty_rate[]')
    confidences  = request.POST.getlist('confidence_pct[]')

    n = len(descriptions)
    def _pad(lst): return (lst + [''] * n)[:n]
    quantities   = _pad(quantities)
    units        = _pad(units)
    total_values = _pad(total_values)
    hs_code_ids  = _pad(hs_code_ids)
    hs_rates     = _pad(hs_rates)
    confidences  = _pad(confidences)

    items = []
    for desc, qty, unit, val, hs_id, hs_rate, confidence_pct in zip(
        descriptions, quantities, units, total_values, hs_code_ids, hs_rates, confidences
    ):
        desc = (desc or '').strip()
        if not desc:
            continue
        try:
            total_value = float(val) if str(val).strip() else 0.0
        except (ValueError, TypeError):
            total_value = 0.0
        try:
            hs_rate_val = float(hs_rate) if str(hs_rate).strip() else 0.0
        except (ValueError, TypeError):
            hs_rate_val = 0.0
        try:
            confidence_val = max(0.0, min(float(confidence_pct) / 100, 1.0))
        except (ValueError, TypeError):
            confidence_val = 0.0
        hs_id_clean = str(hs_id).strip() if hs_id and str(hs_id).strip().isdigit() else ''
        items.append({
            'description':  desc,
            'quantity':     (qty or '').strip() or '1',
            'unit':         (unit or '').strip().upper(),
            'total_value':  total_value,
            'unit_price':   '',
            'gross_weight': '',
            'net_weight':   '',
            'num_packages': '',
            'source':       'ocr_confirmed',
            'confidence':   confidence_val,
            'hs_code_id':   hs_id_clean,
            'duty_rate':    hs_rate_val,
        })

    if not items:
        messages.warning(request, 'No items to load — add at least one row with a description.')
        return redirect('declarant:process', shipment_id=shipment_id)

    # ── Persist to ShipmentLineItem (DB) so items survive browser close ───────
    from apps.shipments.models import HSCode
    ShipmentLineItem.objects.filter(shipment=shipment, source='ocr').delete()
    for order, it in enumerate(items):
        hs_obj = None
        if it.get('hs_code_id'):
            try:
                hs_obj = HSCode.objects.get(id=int(it['hs_code_id']), is_active=True)
            except (HSCode.DoesNotExist, ValueError):
                pass
        try:
            qty_d = __import__('decimal').Decimal(str(it['quantity'])) if it.get('quantity') else None
        except Exception:
            qty_d = None
        try:
            val_d = __import__('decimal').Decimal(str(it['total_value'])) if it.get('total_value') else None
        except Exception:
            val_d = None
        ShipmentLineItem.objects.create(
            shipment      = shipment,
            description   = it['description'],
            quantity      = qty_d,
            unit          = it.get('unit', ''),
            unit_price    = None,
            total_val_usd = val_d,
            hs_code       = hs_obj,
            is_confirmed  = bool(hs_obj),
            source        = 'ocr',
            confidence    = __import__('decimal').Decimal(str(it.get('confidence', 0.0))),
            row_order     = order,
        )

    # Keep session bridge for backward-compatibility with compute.html OCR pre-fill
    request.session['ocr_items']       = items
    request.session['ocr_shipment_id'] = shipment_id
    request.session.modified = True

    from django.http import HttpResponseRedirect
    from django.urls import reverse
    return HttpResponseRedirect(
        reverse('computation:compute', kwargs={'shipment_id': shipment_id}) + '?ocr=1'
    )


# ─── BOC Tracking ─────────────────────────────────────────────────────────────

@login_required
@declarant_required
def boc_tracking(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may record BOC updates
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if request.method == 'POST':
        boc_reference = request.POST.get('boc_reference', '').strip()
        boc_status    = request.POST.get('boc_status', '').strip()
        notes         = request.POST.get('notes', '').strip()

        if not boc_reference or not boc_status:
            messages.error(request, 'BOC Reference and Status are required.')
            return redirect('declarant:boc', shipment_id=shipment_id)

        old_status = shipment.status
        shipment.boc_reference = boc_reference
        shipment.boc_status    = boc_status

        if boc_status == 'Accepted':
            shipment.status = 'approved'
            # Record final processing timestamp
            if not shipment.processed_at:
                shipment.processed_at = timezone.now()
            notif_type  = 'approved'
            notif_title = f'Shipment Approved — {shipment.hawb_number}'
            notif_msg   = (
                f'Great news! Your shipment has been accepted by the Bureau of Customs. '
                f'BOC Reference: {boc_reference}.'
            )
        elif boc_status == 'Rejected':
            shipment.status = 'rejected'
            # Record final processing timestamp
            if not shipment.processed_at:
                shipment.processed_at = timezone.now()
            notif_type  = 'rejected'
            notif_title = f'Shipment Rejected — {shipment.hawb_number}'
            notif_msg   = (
                f'Your shipment was rejected by the Bureau of Customs. '
                f'BOC Reference: {boc_reference}. Notes: {notes}'
            )
        else:
            notif_type  = 'status_update'
            notif_title = f'BOC Update — {shipment.hawb_number}'
            notif_msg   = f'BOC Status: {boc_status}. Reference: {boc_reference}. {notes}'.strip()

        shipment.save()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status=shipment.status,
            notes=f'BOC {boc_status}. Ref: {boc_reference}. {notes}'.strip('. '),
        )

        notify_shipment_status_change(
            shipment=shipment,
            old_status=old_status,
            new_status=shipment.status,
            changed_by=request.user,
            notes=notif_msg,
        )

        messages.success(request, f'BOC status recorded: {boc_status}.')
        return redirect('declarant:process', shipment_id=shipment_id)

    context = {'shipment': shipment}
    return render(request, 'declarant/boc.html', context)
