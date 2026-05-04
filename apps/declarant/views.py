from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.notifications.utils import create_notification


@login_required
def dashboard(request):
    shipments = Shipment.objects.all()
    context = {
        'queue': shipments.filter(status='pending').count(),
        'in_progress': shipments.filter(status='in_review', declarant=request.user).count(),
        'completed': shipments.filter(status='approved', declarant=request.user).count(),
        'rejected': shipments.filter(status='rejected', declarant=request.user).count(),
        'pending_shipments': shipments.filter(status='pending')[:5],
    }
    return render(request, 'declarant/dashboard.html', context)


@login_required
def queue_manager(request):
    pending = Shipment.objects.filter(status='pending')
    in_review = Shipment.objects.filter(status='in_review', declarant=request.user)
    context = {
        'pending': pending,
        'in_review': in_review,
    }
    return render(request, 'declarant/queue.html', context)


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
    else:
        messages.error(request, 'Shipment is no longer available.')
    return redirect('declarant:queue')


@login_required
def process_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)
    documents = shipment.documents.all()
    status_logs = shipment.status_logs.order_by('-changed_at')[:5]

    # Read OCR fields from session (only if they belong to this shipment)
    ocr_fields = None
    if request.session.get('ocr_shipment_id') == shipment_id:
        ocr_fields = request.session.get('ocr_fields')

    context = {
        'shipment': shipment,
        'documents': documents,
        'status_logs': status_logs,
        'ocr_fields': ocr_fields,
    }
    return render(request, 'declarant/process.html', context)


@login_required
def update_status(request, shipment_id):
    if request.method != 'POST':
        return redirect('declarant:queue')

    shipment = get_object_or_404(Shipment, id=shipment_id)
    new_status = request.POST.get('new_status', '').strip()
    notes = request.POST.get('notes', '').strip()

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


@login_required
def payment_confirmation(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.method == 'POST':
        payment_ref = request.POST.get('payment_reference', '').strip()
        notes = request.POST.get('notes', '').strip()

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


@login_required
def boc_tracking(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.method == 'POST':
        boc_reference = request.POST.get('boc_reference', '').strip()
        boc_status = request.POST.get('boc_status', '').strip()
        notes = request.POST.get('notes', '').strip()

        if not boc_reference or not boc_status:
            messages.error(request, 'BOC Reference and Status are required.')
            return redirect('declarant:boc', shipment_id=shipment_id)

        old_status = shipment.status
        shipment.boc_reference = boc_reference
        shipment.boc_status = boc_status

        if boc_status == 'Accepted':
            shipment.status = 'approved'
            notif_type = 'approved'
            notif_title = f'Shipment Approved — {shipment.hawb_number}'
            notif_msg = (
                f'Great news! Your shipment has been accepted by the Bureau of Customs. '
                f'BOC Reference: {boc_reference}.'
            )
        elif boc_status == 'Rejected':
            shipment.status = 'rejected'
            notif_type = 'rejected'
            notif_title = f'Shipment Rejected — {shipment.hawb_number}'
            notif_msg = (
                f'Your shipment was rejected by the Bureau of Customs. '
                f'BOC Reference: {boc_reference}. Notes: {notes}'
            )
        else:
            notif_type = 'status_update'
            notif_title = f'BOC Update — {shipment.hawb_number}'
            notif_msg = f'BOC Status: {boc_status}. Reference: {boc_reference}. {notes}'.strip()

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
