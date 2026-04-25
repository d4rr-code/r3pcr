from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog

@login_required
def dashboard(request):
    shipments = Shipment.objects.all()
    context = {
        'queue': shipments.filter(status='pending').count(),
        'in_progress': shipments.filter(
            status='in_review', 
            declarant=request.user
        ).count(),
        'completed': shipments.filter(
            status='approved',
            declarant=request.user
        ).count(),
        'rejected': shipments.filter(
            status='rejected',
            declarant=request.user
        ).count(),
        'pending_shipments': shipments.filter(status='pending')[:5],
    }
    return render(request, 'declarant/dashboard.html', context)

@login_required
def queue_manager(request):
    pending = Shipment.objects.filter(status='pending')
    in_review = Shipment.objects.filter(
        status='in_review',
        declarant=request.user
    )
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
            notes='Claimed by declarant'
        )
        messages.success(request, f'Shipment {shipment.hawb_number} claimed successfully!')
    else:
        messages.error(request, 'This shipment is no longer available.')
    
    return redirect('declarant:queue')

@login_required
def process_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)
    documents = shipment.documents.all()
    context = {
        'shipment': shipment,
        'documents': documents,
    }
    return render(request, 'declarant/process.html', context)