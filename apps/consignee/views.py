from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument
from apps.accounts.models import User
from apps.notifications.utils import create_notification
from .models import Feedback


# ─── Auto-generate HAWB ───────────────────────────────────────────────────────

def generate_hawb():
    year   = timezone.now().year
    prefix = f'R3PCR-{year}-'
    last   = (
        Shipment.objects
        .filter(hawb_number__startswith=prefix)
        .order_by('hawb_number')
        .last()
    )
    if last:
        try:
            seq      = int(last.hawb_number[len(prefix):])
            next_seq = seq + 1
        except (ValueError, IndexError):
            next_seq = 1
    else:
        next_seq = 1

    hawb = f'{prefix}{str(next_seq).zfill(6)}'
    while Shipment.objects.filter(hawb_number=hawb).exists():
        next_seq += 1
        hawb = f'{prefix}{str(next_seq).zfill(6)}'
    return hawb


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    shipments = Shipment.objects.filter(consignee=request.user)
    context = {
        'total':            shipments.count(),
        'pending':          shipments.filter(status='pending').count(),
        'in_review':        shipments.filter(status='in_review').count(),
        'approved':         shipments.filter(status='approved').count(),
        'rejected':         shipments.filter(status='rejected').count(),
        'recent_shipments': shipments[:5],
    }
    return render(request, 'consignee/dashboard.html', context)


# ─── Submit Shipment ──────────────────────────────────────────────────────────

@login_required
def submit_shipment(request):
    if request.method == 'POST':
        import_type   = request.POST.get('import_type')
        urgency       = request.POST.get('urgency')
        shipment_type = request.POST.get('shipment_type', '').strip()
        description   = request.POST.get('description', '').strip()

        hawb_number = generate_hawb()

        shipment = Shipment.objects.create(
            hawb_number=hawb_number,
            consignee=request.user,
            import_type=import_type,
            urgency=urgency,
            shipment_type=shipment_type or None,
            description=description,
            status='pending',
        )

        for doc_type in ['invoice', 'packing_list', 'airway_bill']:
            file = request.FILES.get(doc_type)
            if file:
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type=doc_type,
                    file=file,
                )

        # Other supporting documents (multiple)
        for file in request.FILES.getlist('other_docs'):
            ShipmentDocument.objects.create(
                shipment=shipment,
                document_type='other',
                file=file,
            )

        declarants = User.objects.filter(role='declarant', is_active=True)
        for declarant in declarants:
            create_notification(
                recipient=declarant,
                shipment=shipment,
                notification_type='submission',
                title=f'New Shipment Ready to Claim — {hawb_number}',
                message=(
                    f'A new shipment ({hawb_number}) is in the pending queue and '
                    f'available for any declarant to claim and process.'
                ),
            )

        messages.success(
            request,
            f'Shipment submitted! Your Shipment Reference No. is '
            f'<strong>{hawb_number}</strong>.'
        )
        return redirect('consignee:my_submissions')

    return render(request, 'consignee/submit.html')


# ─── My Submissions ───────────────────────────────────────────────────────────

@login_required
def my_submissions(request):
    shipments = Shipment.objects.filter(consignee=request.user).order_by('-submitted_at')

    status_filter = request.GET.get('status', '').strip()
    q             = request.GET.get('q', '').strip()
    date_from     = request.GET.get('date_from', '').strip()
    date_to       = request.GET.get('date_to', '').strip()

    if status_filter:
        shipments = shipments.filter(status=status_filter)
    if q:
        shipments = shipments.filter(hawb_number__icontains=q)
    if date_from:
        shipments = shipments.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments = shipments.filter(submitted_at__date__lte=date_to)

    now            = timezone.now()
    shipments_list = list(shipments)
    for s in shipments_list:
        age_seconds  = (now - s.submitted_at).total_seconds()
        s.can_cancel     = s.status == 'pending' and age_seconds <= 3600
        s.cancel_expired = s.status == 'pending' and age_seconds > 3600

    return render(request, 'consignee/my_submissions.html', {
        'shipments':     shipments_list,
        'status_filter': status_filter,
        'q':             q,
        'date_from':     date_from,
        'date_to':       date_to,
    })


# ─── Shipment Detail ──────────────────────────────────────────────────────────

@login_required
def shipment_detail(request, shipment_id):
    """Consignee-facing detail page: status, advisory results, computation summary."""
    shipment    = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)
    advisory    = getattr(shipment, 'shipping_advisory', None)
    computation = getattr(shipment, 'computation', None)
    status_logs = shipment.status_logs.order_by('-changed_at')

    # Rebuild full WMCDA data from saved advisory
    explanation       = None
    wmcda_scores      = None
    wmcda_breakdown   = None
    declared_score    = None
    declared_breakdown = None
    declared_rating   = None

    if advisory:
        try:
            from apps.computation.views import compute_wmcda
            wmcda_scores, _, wmcda_breakdown, explanation = compute_wmcda(
                float(advisory.gross_weight),
                float(advisory.cargo_volume),
                float(advisory.declared_value),
                advisory.urgency_level,
                float(advisory.distance_km),
            )
            if wmcda_scores and shipment.shipment_type:
                declared_score = wmcda_scores.get(shipment.shipment_type)
                if wmcda_breakdown:
                    declared_breakdown = wmcda_breakdown.get(shipment.shipment_type)
                if declared_score is not None:
                    if declared_score >= 0.80:
                        declared_rating = 'Excellent'
                    elif declared_score >= 0.65:
                        declared_rating = 'Good'
                    elif declared_score >= 0.50:
                        declared_rating = 'Fair'
                    else:
                        declared_rating = 'Poor'
        except Exception:
            pass

    context = {
        'shipment':          shipment,
        'advisory':          advisory,
        'computation':       computation,
        'status_logs':       status_logs,
        'explanation':       explanation,
        'wmcda_scores':      wmcda_scores,
        'wmcda_breakdown':   wmcda_breakdown,
        'declared_score':    declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':   declared_rating,
    }
    return render(request, 'consignee/shipment_detail.html', context)


# ─── Upload Payment Receipt ──────────────────────────────────────────────────

@login_required
def upload_receipt(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        file = request.FILES.get('payment_receipt')
        if not file:
            messages.error(request, 'Please select a file to upload.')
        elif shipment.status != 'for_payment':
            messages.error(request, 'Payment receipt can only be uploaded when shipment is marked For Payment.')
        else:
            shipment.payment_receipt = file
            shipment.payment_receipt_uploaded_at = timezone.now()
            shipment.save()

            # Notify declarant
            if shipment.declarant:
                create_notification(
                    recipient=shipment.declarant,
                    shipment=shipment,
                    notification_type='status_update',
                    title=f'Payment Receipt Uploaded — {shipment.hawb_number}',
                    message=f'{request.user.get_full_name() or request.user.username} uploaded a payment receipt.',
                )
            messages.success(request, 'Payment receipt uploaded successfully.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


# ─── Feedback ─────────────────────────────────────────────────────────────────

@login_required
def submit_feedback(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    # Only allow feedback on completed shipments
    if shipment.status not in ('approved', 'rejected'):
        messages.error(request, 'Feedback can only be submitted for completed shipments.')
        return redirect('consignee:shipment_detail', shipment_id=shipment_id)

    # One feedback per shipment
    if hasattr(shipment, 'feedback'):
        messages.info(request, 'You have already submitted feedback for this shipment.')
        return redirect('consignee:shipment_detail', shipment_id=shipment_id)

    if request.method == 'POST':
        rating  = request.POST.get('rating', '').strip()
        comment = request.POST.get('comment', '').strip()

        if not rating or not comment:
            messages.error(request, 'Please provide a rating and a comment.')
            return render(request, 'consignee/feedback.html', {'shipment': shipment})

        Feedback.objects.create(
            consignee=request.user,
            shipment=shipment,
            rating=int(rating),
            comment=comment,
        )
        messages.success(request, 'Thank you for your feedback! It will appear on our site once reviewed.')
        return redirect('consignee:shipment_detail', shipment_id=shipment_id)

    return render(request, 'consignee/feedback.html', {'shipment': shipment})


# ─── Cancel Submission ────────────────────────────────────────────────────────

@login_required
def cancel_submission(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        age = timezone.now() - shipment.submitted_at
        if shipment.status != 'pending':
            messages.error(request, 'Cannot cancel — this shipment is already being processed.')
        elif age.total_seconds() > 3600:
            messages.error(request, 'Cannot cancel — the 1-hour cancellation window has passed.')
        else:
            shipment.delete()
            messages.success(request, 'Shipment cancelled and removed.')
            return redirect('consignee:my_submissions')

    return redirect('consignee:my_submissions')
