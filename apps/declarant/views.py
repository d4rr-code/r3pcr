import datetime
import json
import os
import tempfile
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_shipment_status_change
from apps.computation.ocr import process_document


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

    context = {
        'queue':               queue_count,
        'in_progress':         in_progress,
        'completed':           completed,
        'rejected':            rejected_count,
        'avg_processing_days': avg_processing_days,
        'completion_rate':     completion_rate,
        'pending_shipments':   pending_list,
    }
    return render(request, 'declarant/dashboard.html', context)


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

    # Arrived: my claimed shipments
    in_review = Shipment.objects.filter(
        status='arrived', declarant=request.user
    ).select_related('consignee')

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
    _pending_ocr = [
        doc for doc in documents
        if doc.document_type in ('invoice', 'airway_bill', 'packing_list') and not doc.ocr_ran_at
    ]
    if _pending_ocr:
        def _run_ocr_background(docs):
            for doc in docs:
                try:
                    _run_and_store_document_ocr(doc)
                    print(f'[OCR-AUTO] Completed for document {doc.id} ({doc.document_type})')
                except Exception as e:
                    print(f'[OCR-AUTO] Failed for document {doc.id}: {e}')
        t = threading.Thread(target=_run_ocr_background, args=(_pending_ocr,), daemon=True)
        t.start()

    documents = shipment.documents.all()
    status_logs = shipment.status_logs.order_by('-changed_at')[:5]

    ocr_fields = None
    if request.session.get('ocr_shipment_id') == shipment_id:
        ocr_fields = request.session.get('ocr_fields')

    # OCR toast survives fetch→reload cycle (Django messages don't)
    ocr_toast = request.session.pop('ocr_toast', None)

    has_pending_ocr = bool(_pending_ocr)

    context = {
        'shipment':        shipment,
        'documents':       documents,
        'status_logs':     status_logs,
        'ocr_fields':      ocr_fields,
        'ocr_documents':   _ocr_display_documents(documents),
        'ocr_toast':       ocr_toast,
        'has_pending_ocr': has_pending_ocr,
        'manual_status_choices': Shipment.MANUAL_STATUS_CHOICES,
        'status_steps': build_status_progress(shipment.status, 'declarant'),
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


# ─── Payment Confirmation ─────────────────────────────────────────────────────

@login_required
@declarant_required
def payment_confirmation(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may confirm payment
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if request.method == 'POST':
        payment_ref = request.POST.get('payment_reference', '').strip()
        notes       = request.POST.get('notes', '').strip()

        if not payment_ref:
            messages.error(request, 'Payment reference is required.')
            return redirect('declarant:payment', shipment_id=shipment_id)

        old_status = shipment.status
        shipment.status = 'lodgement'
        shipment.save()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='lodgement',
            notes=f'Payment confirmed. Ref: {payment_ref}. {notes}'.strip('. '),
        )

        create_notification(
            recipient=shipment.consignee,
            shipment=shipment,
            notification_type='payment',
            title=f'Payment Confirmed — {shipment.hawb_number}',
            message=(
                f'Payment confirmed (Ref: {payment_ref}). '
                f'Your shipment has been lodged with the Bureau of Customs.'
            ),
        )

        messages.success(request, 'Payment confirmed. Shipment moved to lodgement.')
        return redirect('declarant:process', shipment_id=shipment_id)

    context = {'shipment': shipment}
    return render(request, 'declarant/payment.html', context)


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
        message=f'A deficiency has been flagged on your shipment: {note_text}. Please check your submissions and contact your declarant.',
    )

    messages.success(request, f'Deficiency flagged — consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


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
