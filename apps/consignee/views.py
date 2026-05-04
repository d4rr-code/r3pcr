from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument
from apps.accounts.models import User
from apps.notifications.utils import create_notification


# ─── Auto-generate HAWB ───────────────────────────────────────────────────────

def generate_hawb():
    year = timezone.now().year
    prefix = f'R3PCR-{year}-'
    last = (
        Shipment.objects
        .filter(hawb_number__startswith=prefix)
        .order_by('hawb_number')
        .last()
    )
    if last:
        try:
            seq = int(last.hawb_number[len(prefix):])
            next_seq = seq + 1
        except (ValueError, IndexError):
            next_seq = 1
    else:
        next_seq = 1

    hawb = f'{prefix}{str(next_seq).zfill(6)}'
    # Race-condition guard
    while Shipment.objects.filter(hawb_number=hawb).exists():
        next_seq += 1
        hawb = f'{prefix}{str(next_seq).zfill(6)}'
    return hawb


# ─── Views ────────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    shipments = Shipment.objects.filter(consignee=request.user)
    context = {
        'total': shipments.count(),
        'pending': shipments.filter(status='pending').count(),
        'in_review': shipments.filter(status='in_review').count(),
        'approved': shipments.filter(status='approved').count(),
        'rejected': shipments.filter(status='rejected').count(),
        'recent_shipments': shipments[:5],
    }
    return render(request, 'consignee/dashboard.html', context)


@login_required
def submit_shipment(request):
    if request.method == 'POST':
        import_type  = request.POST.get('import_type')
        urgency      = request.POST.get('urgency')
        description  = request.POST.get('description', '').strip()

        # Auto-generate shipment reference
        hawb_number = generate_hawb()

        shipment = Shipment.objects.create(
            hawb_number=hawb_number,
            consignee=request.user,
            import_type=import_type,
            urgency=urgency,
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

        # Notify all active declarants
        declarants = User.objects.filter(role='declarant', is_active=True)
        for declarant in declarants:
            create_notification(
                recipient=declarant,
                shipment=shipment,
                notification_type='submission',
                title=f'New Shipment — {hawb_number}',
                message=(
                    f'{request.user.get_full_name() or request.user.username} '
                    f'submitted {hawb_number} for pre-clearance.'
                ),
            )

        messages.success(
            request,
            f'Shipment submitted! Your Shipment Reference No. is '
            f'<strong>{hawb_number}</strong>.'
        )
        return redirect('consignee:my_submissions')

    return render(request, 'consignee/submit.html')


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

    return render(request, 'consignee/my_submissions.html', {
        'shipments':     shipments,
        'status_filter': status_filter,
        'q':             q,
        'date_from':     date_from,
        'date_to':       date_to,
    })


@login_required
def cancel_submission(request, shipment_id):
    shipment = get_object_or_404(
        Shipment, id=shipment_id, consignee=request.user
    )

    if request.method == 'POST':
        # Only allow cancel if pending AND submitted < 1 hour ago
        age = timezone.now() - shipment.submitted_at
        if shipment.status != 'pending':
            messages.error(
                request,
                'Cannot cancel — this shipment is already being processed.'
            )
        elif age.total_seconds() > 3600:
            messages.error(
                request,
                'Cannot cancel — the 1-hour cancellation window has passed.'
            )
        else:
            shipment.delete()
            messages.success(request, 'Shipment cancelled and removed.')
            return redirect('consignee:my_submissions')

    return redirect('consignee:my_submissions')
