from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from decimal import Decimal
from apps.shipments.models import Shipment, HSCode
from .models import DutyComputation

def compute_duties(declared_value, freight, insurance, exchange_rate, duty_rate, vat_rate=Decimal('0.12')):
    """
    R3-PCR Duty Computation Formula:
    1. CIF Value (PHP) = (Declared Value + Freight + Insurance) × Exchange Rate
    2. Customs Duty = CIF Value × Duty Rate
    3. VAT Base = CIF Value + Customs Duty
    4. VAT Amount = VAT Base × VAT Rate (12%)
    5. Total Landed Cost = CIF Value + Customs Duty + VAT Amount
    """
    # Step 1: CIF Value in PHP
    cif_value = (declared_value + freight + insurance) * exchange_rate

    # Step 2: Customs Duty
    customs_duty = cif_value * (duty_rate / Decimal('100'))

    # Step 3: VAT Base
    vat_base = cif_value + customs_duty

    # Step 4: VAT Amount
    vat_amount = vat_base * vat_rate

    # Step 5: Total Landed Cost
    total_landed_cost = cif_value + customs_duty + vat_amount

    return {
        'dutiable_value': round(cif_value, 2),
        'customs_duty': round(customs_duty, 2),
        'vat_base': round(vat_base, 2),
        'vat_amount': round(vat_amount, 2),
        'total_landed_cost': round(total_landed_cost, 2),
    }

@login_required
def compute_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)
    hs_codes = HSCode.objects.filter(is_active=True)
    
    # Get existing computation if any
    existing = DutyComputation.objects.filter(shipment=shipment).first()
    result = None

    if request.method == 'POST':
        try:
            declared_value = Decimal(request.POST.get('declared_value', '0'))
            freight = Decimal(request.POST.get('freight_cost', '0'))
            insurance = Decimal(request.POST.get('insurance_cost', '0'))
            exchange_rate = Decimal(request.POST.get('exchange_rate', '0'))
            duty_rate = Decimal(request.POST.get('duty_rate', '0'))
            hs_code_id = request.POST.get('hs_code')

            # Run computation
            result = compute_duties(
                declared_value, freight, 
                insurance, exchange_rate, duty_rate
            )

            # Get HS Code
            hs_code = None
            if hs_code_id:
                hs_code = HSCode.objects.get(id=hs_code_id)

            # Save or update computation
            computation, created = DutyComputation.objects.update_or_create(
                shipment=shipment,
                defaults={
                    'hs_code': hs_code,
                    'declared_value': declared_value,
                    'freight_cost': freight,
                    'insurance_cost': insurance,
                    'exchange_rate': exchange_rate,
                    'duty_rate': duty_rate,
                    'dutiable_value': result['dutiable_value'],
                    'customs_duty': result['customs_duty'],
                    'vat_base': result['vat_base'],
                    'vat_amount': result['vat_amount'],
                    'total_landed_cost': result['total_landed_cost'],
                    'computed_by': request.user,
                }
            )

            messages.success(request, 'Computation saved successfully!')

        except Exception as e:
            messages.error(request, f'Computation error: {e}')

    context = {
        'shipment': shipment,
        'hs_codes': hs_codes,
        'existing': existing,
        'result': result,
    }
    return render(request, 'computation/compute.html', context)

@login_required
def hs_code_search(request):
    query = request.GET.get('q', '')
    results = []
    if query:
        results = HSCode.objects.filter(
            description__icontains=query,
            is_active=True
        )[:10]
    
    context = {
        'query': query,
        'results': results,
    }
    return render(request, 'computation/hs_search.html', context)