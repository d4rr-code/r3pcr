import json
import logging
import re
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from apps.shipments.models import Shipment, StatusLog
from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_shipment_status_change, send_assessed_email, send_billed_email
from apps.computation.ocr import _extract_line_items, _extract_hs_anchored_items

logger = logging.getLogger('r3pcr.declarant')

from .common import *  # noqa: F401,F403

STATUS_DOCUMENT_FILTERS = {
    'ongoing': ('sad',),
    'assessed': ('sad',),
    'paid': ('payment_proof',),
    'released': ('release_doc',),
    'billed': ('billing_doc', 'receipt'),
}


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
        'container_number': shipment.container_number or '',
        'job_order_reference': shipment.job_order_reference or '',
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
    status_filter = request.GET.get('status', '').strip()
    valid_dashboard_filters = {'incoming', 'in_progress', 'approved', 'billed'}
    if status_filter not in valid_dashboard_filters:
        status_filter = ''

    def page_url(param_name, page_number):
        params = request.GET.copy()
        params[param_name] = page_number
        return f'?{params.urlencode()}'

    # Incoming queue with optional filters
    pending_qs = Shipment.objects.filter(status='incoming').select_related('consignee')
    if status_filter and status_filter != 'incoming':
        pending_qs = pending_qs.none()

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
    in_review_statuses = [
        'arrived', 'computed', 'approved', 'rejected', 'for_revision',
        'lodgement', 'ongoing', 'assessed', 'paid', 'released',
    ]
    if status_filter == 'in_progress':
        in_review_statuses = ['arrived', 'computed', 'for_revision', 'lodgement', 'ongoing', 'assessed']
    elif status_filter == 'approved':
        in_review_statuses = ['approved']
    elif status_filter in {'incoming', 'billed'}:
        in_review_statuses = []

    in_review_qs = Shipment.objects.filter(
        declarant=request.user,
        status__in=in_review_statuses,
    ).select_related('consignee').prefetch_related('computation').order_by('-updated_at')
    in_review = Paginator(in_review_qs, 10).get_page(request.GET.get('review_page', 1))

    # Processed: only fully billed shipments
    history_qs = Shipment.objects.filter(
        declarant=request.user,
        status='billed',
    ).select_related('consignee').order_by('-updated_at')
    if status_filter and status_filter != 'billed':
        history_qs = history_qs.none()
    history = Paginator(history_qs, 10).get_page(request.GET.get('history_page', 1))

    context = {
        'pending':        pending_page,   # now a Page object; templates use pending.object_list
        'paginator':      paginator,
        'in_review':      in_review,
        'history':        history,
        'review_prev_url': page_url('review_page', in_review.previous_page_number()) if in_review.has_previous() else '',
        'review_next_url': page_url('review_page', in_review.next_page_number()) if in_review.has_next() else '',
        'history_prev_url': page_url('history_page', history.previous_page_number()) if history.has_previous() else '',
        'history_next_url': page_url('history_page', history.next_page_number()) if history.has_next() else '',
        'urgency_filter': urgency_filter,
        'due_filter':     due_filter,
        'status_filter':  status_filter,
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
    """Kick off OCR on all unscanned documents in a BACKGROUND thread and return
    immediately, so the request (and gunicorn worker) is never blocked while
    Tesseract runs. The client polls `ocr_status` for progress.
    Returns {started: true, total: N} or {already_done: true}.
    """
    import threading

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)

    doc_ids = list(shipment.documents.filter(
        document_type__in=['invoice', 'airway_bill', 'packing_list'],
        ocr_ran_at__isnull=True,
    ).values_list('id', flat=True))

    if not doc_ids:
        return JsonResponse({'done': True, 'scanned': 0, 'total': 0, 'already_done': True})

    threading.Thread(
        target=_ocr_scan_in_background, args=(doc_ids,), daemon=True
    ).start()
    return JsonResponse({'started': True, 'total': len(doc_ids)})


@login_required
@declarant_required
def ocr_status(request, shipment_id):
    """Report OCR progress for a shipment's documents (DB-backed, so it works
    no matter which worker ran the OCR). Polled by the loading overlay."""
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)

    qs = shipment.documents.filter(
        document_type__in=['invoice', 'airway_bill', 'packing_list'])
    total   = qs.count()
    scanned = qs.filter(ocr_ran_at__isnull=False).count()
    return JsonResponse({'done': scanned >= total, 'scanned': scanned, 'total': total})


# ─── Process Shipment ─────────────────────────────────────────────────────────

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


def _priority_docs_by_type(documents):
    """Group OCR-completed priority documents by type for line-item extraction."""
    priority_doc_types = ['invoice', 'packing_list', 'airway_bill']
    docs_by_type = {}
    for d in documents:
        if d.document_type in priority_doc_types and d.ocr_ran_at and (
            d.ocr_fields_json or getattr(d, 'ocr_text', None)
        ):
            docs_by_type.setdefault(d.document_type, []).append(d)
    return docs_by_type


def _collect_ocr_items_from_docs(docs_by_type):
    """Extract + fuzzy-merge OCR line items across the priority documents.

    Primary pass re-extracts from each document's stored raw OCR text with
    _extract_line_items (avoids stale cached items). If nothing matches, falls
    back to the HS-code-anchored extractor for the first doc type that yields
    results.
    """
    priority_doc_types = ['invoice', 'packing_list', 'airway_bill']
    ocr_items_from_docs = []

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

    for doc_type in priority_doc_types:
        for doc in docs_by_type.get(doc_type, []):
            items_from_text = []
            if getattr(doc, 'ocr_text', None):
                items_from_text = _extract_line_items(doc.ocr_text)
            for item in items_from_text:
                _add_ocr_item(item, doc_type)

    if not ocr_items_from_docs:
        for doc_type in priority_doc_types:
            for doc in docs_by_type.get(doc_type, []):
                if not getattr(doc, 'ocr_text', None):
                    continue
                for item in _extract_hs_anchored_items(doc.ocr_text):
                    _add_ocr_item(item, doc_type)
            if ocr_items_from_docs:
                break  # stop at first document type that yields results

    return ocr_items_from_docs


def _collect_ocr_hs_suggestions(docs_by_type):
    """Two-pass HS-code suggestions from raw OCR text.

    Pass 1 pins HS codes explicitly printed in the documents (looked up in the
    tariff table); pass 2 fills remaining slots with keyword matches.
    """
    raw_parts = []
    for doc_type in ['invoice', 'packing_list', 'airway_bill']:
        for doc in docs_by_type.get(doc_type, []):
            rt = getattr(doc, 'ocr_text', None)
            if rt:
                raw_parts.append(rt[:3000])
    if not raw_parts:
        return []

    try:
        from apps.computation.views import (
            extract_document_hs_codes as _extract_document_hs_codes,
            find_hs_by_document_code as _find_hs_by_document_code,
            suggest_hs_codes as _suggest_hs_codes,
        )
        combined = ' '.join(raw_parts)[:5000]
        seen_ids = set()
        pinned = []
        for raw in _extract_document_hs_codes(combined):
            hs_obj = _find_hs_by_document_code(raw)
            if hs_obj and hs_obj.id not in seen_ids:
                pinned.append(hs_obj)
                seen_ids.add(hs_obj.id)
        for hs in _suggest_hs_codes(combined, top_n=8):
            if hs.id not in seen_ids:
                pinned.append(hs)
                seen_ids.add(hs.id)
        return pinned[:10]
    except Exception as e:
        logger.warning('HS-OCR suggestion error: %s', e)
        return []


@login_required
@declarant_required
def process_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may access the process page
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    documents = shipment.documents.all()
    document_filter_status = request.GET.get('doc_status', '').strip()
    if document_filter_status not in STATUS_DOCUMENT_FILTERS:
        document_filter_status = ''
    document_filter_types = STATUS_DOCUMENT_FILTERS.get(document_filter_status)
    if document_filter_types:
        visible_documents = documents.filter(document_type__in=document_filter_types)
    else:
        visible_documents = documents.exclude(document_type='sad')
    # Check if any docs still need OCR (e.g. declarant navigated directly, skipping the queue flow)
    _pending_ocr = [
        doc for doc in documents
        if doc.document_type in ('invoice', 'airway_bill', 'packing_list') and not doc.ocr_ran_at
    ]
    has_pending_ocr = bool(_pending_ocr)  # kept for template auto-reload fallback

    status_logs = shipment.status_logs.order_by('-changed_at')[:5]

    # ── Extract OCR line items + HS suggestions from scanned documents ──────────
    docs_by_type        = _priority_docs_by_type(documents)
    ocr_items_from_docs = _collect_ocr_items_from_docs(docs_by_type)
    ocr_hs_suggestions  = _collect_ocr_hs_suggestions(docs_by_type)

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
        'visible_documents':   visible_documents,
        'document_filter_status': document_filter_status,
        'document_filter_label': dict(Shipment.STATUS_CHOICES).get(document_filter_status, ''),
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


@login_required
@declarant_required
def update_tracking_fields(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    update_fields = ['updated_at']

    if 'job_order_reference' in request.POST:
        shipment.job_order_reference = request.POST.get('job_order_reference', '').strip() or None
        update_fields.append('job_order_reference')

    if 'container_number' in request.POST:
        shipment.container_number = request.POST.get('container_number', '').strip() or None
        update_fields.append('container_number')

    if len(update_fields) == 1:
        messages.error(request, 'No tracking fields were submitted.')
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment.save(update_fields=update_fields)

    messages.success(request, 'Shipment tracking details updated.')
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

    if new_status == 'lodgement' and shipment.status != 'approved':
        messages.error(request, 'ECDT must be approved before proceeding to lodgement.')
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
