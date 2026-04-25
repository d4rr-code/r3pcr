from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment, ShipmentDocument

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
        hawb_number = request.POST.get('hawb_number')
        import_type = request.POST.get('import_type')
        urgency = request.POST.get('urgency')
        description = request.POST.get('description')

        # Check if HAWB already exists
        if Shipment.objects.filter(hawb_number=hawb_number).exists():
            messages.error(request, 'A shipment with this HAWB number already exists.')
            return render(request, 'consignee/submit.html')

        # Create shipment
        shipment = Shipment.objects.create(
            hawb_number=hawb_number,
            consignee=request.user,
            import_type=import_type,
            urgency=urgency,
            description=description,
            status='pending'
        )

        # Handle document uploads
        for doc_type in ['invoice', 'packing_list', 'airway_bill']:
            file = request.FILES.get(doc_type)
            if file:
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type=doc_type,
                    file=file
                )

        messages.success(request, f'Shipment {hawb_number} submitted successfully!')
        return redirect('consignee:my_submissions')

    return render(request, 'consignee/submit.html')

@login_required
def my_submissions(request):
    shipments = Shipment.objects.filter(consignee=request.user)
    return render(request, 'consignee/my_submissions.html', {'shipments': shipments})