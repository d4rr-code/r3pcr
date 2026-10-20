import json
import logging
from functools import wraps
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db import transaction
from apps.shipments.workflow import DRAFT_STATUSES
from ..validation import decimal_value
from apps.shipments.models import Shipment, HSCode
from ..models import DutyComputation, ShipmentLineItem, ShippingAdvisory
from apps.declarant.views import declarant_required

logger = logging.getLogger('r3pcr.computation')


def draft_write(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            with transaction.atomic():
                return view(request, *args, **kwargs)
        except (ValueError, TypeError, InvalidOperation) as exc:
            return JsonResponse({'error': str(exc) or 'Invalid draft inputs.'}, status=400)
    return wrapped

@login_required
@declarant_required
@draft_write
def draft_item(request, shipment_id):
    """Create or update a single ShipmentLineItem (draft save)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    shipment = get_object_or_404(Shipment.objects.select_for_update(), id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)
    if shipment.status not in DRAFT_STATUSES:
        return JsonResponse({'error': 'Drafts are locked during client review and after approval. Submit a full recomputation to request renewed approval.'}, status=409)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError('A JSON object is required.')
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    item_id   = data.get('item_id')   # None → create new
    row_order = int(data.get('row_order', 0))
    desc      = (data.get('description') or '').strip()

    def _dec(key):
        v = data.get(key)
        return decimal_value(v, key, maximum=Decimal('100') if key == 'duty_rate' else Decimal('999999999999.99')) if v not in (None, '', 'null') else None

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
        except (HSCode.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'error': 'Invalid HS code.'}, status=400)

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
@draft_write
def delete_draft_item(request, item_id):
    """Delete a ShipmentLineItem row."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    item = get_object_or_404(ShipmentLineItem, id=item_id, shipment__declarant=request.user)
    shipment = Shipment.objects.select_for_update().get(pk=item.shipment_id)
    if shipment.status not in DRAFT_STATUSES:
        return JsonResponse({'error': 'This estimate is locked.'}, status=409)
    deleted, _ = item.delete()
    return JsonResponse({'ok': True, 'deleted': deleted})


@login_required
@declarant_required
@draft_write
def draft_globals(request, shipment_id):
    """Save global computation inputs to DutyComputation without running the full formula."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    shipment = get_object_or_404(Shipment.objects.select_for_update(), id=shipment_id)
    if shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied'}, status=403)
    if shipment.status not in DRAFT_STATUSES:
        return JsonResponse({'error': 'This estimate is locked. Submit a full recomputation to request renewed approval.'}, status=409)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError('A JSON object is required.')
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    def _d(key, default='0'):
        return decimal_value(data.get(key) or default, key,
            minimum=Decimal('0.000001') if key == 'exchange_rate' else Decimal('0'))

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

    # Save MCDA advisory pre-fill inputs when provided.
    cargo_volume = data.get('cargo_volume')
    distance_km  = data.get('distance_km')
    gross_weight = data.get('gross_weight_kg')
    if cargo_volume is not None or distance_km is not None or gross_weight is not None:
        try:
            gross_w = Decimal(str(shipment.gross_weight or 0))
            if gross_weight is not None:
                try:
                    posted_weight = Decimal(str(gross_weight or 0))
                    if posted_weight > 0:
                        gross_w = posted_weight
                        if shipment.gross_weight != posted_weight:
                            shipment.gross_weight = posted_weight
                            shipment.save(update_fields=['gross_weight'])
                except (InvalidOperation, ValueError, TypeError):
                    pass
            existing_advisory = getattr(shipment, 'shipping_advisory', None)
            advisory_defaults = {
                'gross_weight': gross_w,
                'cargo_volume': existing_advisory.cargo_volume if existing_advisory else Decimal('0'),
                'distance_km': existing_advisory.distance_km if existing_advisory else Decimal('2600'),
            }
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
                        'declared_value': Decimal('0'),
                        'urgency_level':  shipment.urgency or 'normal',
                        **advisory_defaults,
                    }
                )
        except Exception as e:
            logger.debug('Advisory pre-fill from draft globals failed: %s', e)

    return JsonResponse({'ok': True})


# ─── Computation ──────────────────────────────────────────────────────────────
