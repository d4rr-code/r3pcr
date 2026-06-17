import json
import logging
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from apps.shipments.models import Shipment, HSCode
from ..models import DutyComputation, ShipmentLineItem, ShippingAdvisory
from apps.declarant.views import declarant_required

logger = logging.getLogger('r3pcr.computation')

@login_required
@declarant_required
def draft_item(request, shipment_id):
    """Create or update a single ShipmentLineItem (draft save)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    item_id   = data.get('item_id')   # None → create new
    row_order = int(data.get('row_order', 0))
    desc      = (data.get('description') or '').strip()

    def _dec(key):
        v = data.get(key)
        try:
            return Decimal(str(v)) if v not in (None, '', 'null') else None
        except Exception:
            return None

    defaults = {
        'description':   desc or '(draft)',
        'quantity':      _dec('quantity'),
        'unit':          (data.get('unit') or '').strip(),
        'unit_price':    _dec('unit_price'),
        'total_val_usd': _dec('exw_value'),
        'row_order':     row_order,
        'source':        'manual',
        'freight':       _dec('item_freight'),
        'insurance':     _dec('item_insurance'),
        'gross_weight':  _dec('gw'),
        'net_weight':    _dec('nw'),
        'packages':      int(data['pkgs']) if data.get('pkgs') not in (None, '', 'null') else None,
        'duty_rate':     _dec('duty_rate'),
    }
    hs_id = data.get('hs_code_id')
    if hs_id:
        try:
            defaults['hs_code'] = HSCode.objects.get(id=int(hs_id))
        except (HSCode.DoesNotExist, (ValueError, TypeError)):
            pass

    if item_id:
        obj = ShipmentLineItem.objects.filter(id=item_id, shipment=shipment).first()
        if obj:
            for k, v in defaults.items():
                setattr(obj, k, v)
            obj.save()
        else:
            obj = ShipmentLineItem.objects.create(shipment=shipment, **defaults)
    else:
        obj = ShipmentLineItem.objects.create(shipment=shipment, **defaults)

    return JsonResponse({'ok': True, 'item_id': obj.id})


@login_required
@declarant_required
def delete_draft_item(request, item_id):
    """Delete a ShipmentLineItem row."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    deleted, _ = ShipmentLineItem.objects.filter(
        id=item_id, shipment__declarant=request.user
    ).delete()
    return JsonResponse({'ok': True, 'deleted': deleted})


@login_required
@declarant_required
def draft_globals(request, shipment_id):
    """Save global computation inputs to DutyComputation without running the full formula."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    def _d(key, default='0'):
        try:
            return Decimal(str(data.get(key) or default))
        except Exception:
            return Decimal(default)

    DutyComputation.objects.update_or_create(
        shipment=shipment,
        defaults={
            'exchange_rate':    _d('exchange_rate', '59.1480'),
            'total_freight':    _d('total_freight'),
            'total_insurance':  _d('total_insurance'),
            'bank_charges':     _d('bank_charges'),
            'arrastre':         _d('arrastre'),
            'wharfage':         _d('wharfage'),
            'csf_usd':          _d('csf_usd'),
            'container_type':   (data.get('container_type') or '').strip(),
            'computed_by':      request.user,
        }
    )

    # Save cargo_volume and distance_km to ShippingAdvisory if provided
    cargo_volume = data.get('cargo_volume')
    distance_km  = data.get('distance_km')
    if cargo_volume is not None or distance_km is not None:
        try:
            gross_w = Decimal(str(shipment.gross_weight or 0))
            advisory_defaults = {}
            if cargo_volume is not None:
                try:
                    advisory_defaults['cargo_volume'] = Decimal(str(cargo_volume or 0))
                except (InvalidOperation, ValueError, TypeError):
                    pass
            if distance_km is not None:
                try:
                    advisory_defaults['distance_km'] = Decimal(str(distance_km or 0))
                except (InvalidOperation, ValueError, TypeError):
                    pass
            if advisory_defaults:
                ShippingAdvisory.objects.update_or_create(
                    shipment=shipment,
                    defaults={
                        'gross_weight':   gross_w,
                        'declared_value': Decimal('0'),
                        'urgency_level':  shipment.urgency or 'normal',
                        **advisory_defaults,
                    }
                )
        except Exception as e:
            logger.debug('Advisory pre-fill from draft globals failed: %s', e)

    return JsonResponse({'ok': True})


# ─── Computation ──────────────────────────────────────────────────────────────

