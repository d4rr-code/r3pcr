import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.notifications.utils import create_notification


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


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    shipments = Shipment.objects.all()
    my = {'declarant': request.user}

    queue_count    = shipments.filter(status='pending').count()
    in_progress    = shipments.filter(status='in_review', **my).count()
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

    # My completion rate: done / (done + in_review)
    total_handled = len(done_qs) + in_progress
    completion_rate = round(len(done_qs) / total_handled * 100, 1) if total_handled > 0 else 0

    # Pending queue for dashboard table (up to 20, annotated with due dates)
    today = timezone.localdate()
    pending_list = list(shipments.filter(status='pending').select_related('consignee')[:20])
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


# ─── Queue Manager ────────────────────────────────────────────────────────────

@login_required
def queue_manager(request):
    today = timezone.localdate()

    # Pending queue with optional filters
    pending_qs = Shipment.objects.filter(status='pending').select_related('consignee')

    urgency_filter = request.GET.get('urgency', '')
    if urgency_filter in ('urgent', 'normal'):
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

    # In-review: my claimed shipments
    in_review = Shipment.objects.filter(
        status='in_review', declarant=request.user
    ).select_related('consignee')

    context = {
        'pending':        pending,
        'in_review':      in_review,
        'urgency_filter': urgency_filter,
        'due_filter':     due_filter,
    }
    return render(request, 'declarant/queue.html', context)


# ─── Claim Shipment ───────────────────────────────────────────────────────────

@login_required
def claim_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.status == 'pending':
        shipment.declarant = request.user
        shipment.status = 'in_review'
        shipment.save()
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status='pending',
            new_status='in_review',
            notes='Claimed by declarant',
        )
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
def process_shipment(request, shipment_id):
    shipment   = get_object_or_404(Shipment, id=shipment_id)
    documents  = shipment.documents.all()
    status_logs = shipment.status_logs.order_by('-changed_at')[:5]

    ocr_fields = None
    if request.session.get('ocr_shipment_id') == shipment_id:
        ocr_fields = request.session.get('ocr_fields')

    # OCR toast survives fetch→reload cycle (Django messages don't)
    ocr_toast = request.session.pop('ocr_toast', None)

    context = {
        'shipment':    shipment,
        'documents':   documents,
        'status_logs': status_logs,
        'ocr_fields':  ocr_fields,
        'ocr_toast':   ocr_toast,
    }
    return render(request, 'declarant/process.html', context)


# ─── Update Status ────────────────────────────────────────────────────────────

@login_required
def update_status(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:queue')

    shipment   = get_object_or_404(Shipment, id=shipment_id)
    new_status = request.POST.get('new_status', '').strip()
    notes      = request.POST.get('notes', '').strip()

    if not new_status:
        messages.error(request, 'Please select a status.')
        return redirect('declarant:process', shipment_id=shipment_id)

    old_status = shipment.status
    shipment.status = new_status
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
def payment_confirmation(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.method == 'POST':
        payment_ref = request.POST.get('payment_reference', '').strip()
        notes       = request.POST.get('notes', '').strip()

        if not payment_ref:
            messages.error(request, 'Payment reference is required.')
            return redirect('declarant:payment', shipment_id=shipment_id)

        old_status = shipment.status
        shipment.status = 'submitted'
        shipment.save()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='submitted',
            notes=f'Payment confirmed. Ref: {payment_ref}. {notes}'.strip('. '),
        )

        create_notification(
            recipient=shipment.consignee,
            shipment=shipment,
            notification_type='payment',
            title=f'Payment Confirmed — {shipment.hawb_number}',
            message=(
                f'Payment confirmed (Ref: {payment_ref}). '
                f'Your shipment has been submitted to the Bureau of Customs.'
            ),
        )

        messages.success(request, 'Payment confirmed. Shipment submitted to BOC.')
        return redirect('declarant:process', shipment_id=shipment_id)

    context = {'shipment': shipment}
    return render(request, 'declarant/payment.html', context)


# ─── BOC Tracking ─────────────────────────────────────────────────────────────

@login_required
def boc_tracking(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

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
            notif_type  = 'approved'
            notif_title = f'Shipment Approved — {shipment.hawb_number}'
            notif_msg   = (
                f'Great news! Your shipment has been accepted by the Bureau of Customs. '
                f'BOC Reference: {boc_reference}.'
            )
        elif boc_status == 'Rejected':
            shipment.status = 'rejected'
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

        create_notification(
            recipient=shipment.consignee,
            shipment=shipment,
            notification_type=notif_type,
            title=notif_title,
            message=notif_msg,
        )

        messages.success(request, f'BOC status recorded: {boc_status}.')
        return redirect('declarant:process', shipment_id=shipment_id)

    context = {'shipment': shipment}
    return render(request, 'declarant/boc.html', context)
