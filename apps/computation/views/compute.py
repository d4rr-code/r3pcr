import json
import logging
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from apps.shipments.models import Shipment, HSCode, ShipmentHSCode, StatusLog
from apps.notifications.utils import notify_shipment_status_change
from ..models import DutyComputation, ShipmentLineItem, ShippingAdvisory

logger = logging.getLogger('r3pcr.computation')

from .ecdt import (
    compute_ecdt, _load_currency_rates, normalize_charge_mode,
    apply_transport_charges, _lookup_distance_from_country, _country_distance_options,
    _RATE_KEYS, _RATE_DEFAULTS,
)
from .advisory import compute_wmcda
from .hs_codes import suggest_hs_codes
from ..wmcda import wmcda_weight_rows
from apps.supervisor.models import SystemConfig

def _wmcda_history(shipment):
    """Most-common historical WMCDA recommendation for this shipment's type.

    Returns a summary dict (total, top_mode, top_pct, mode_label, ship_type) or
    None when the shipment has no type or there is no prior advisory history.
    """
    if not shipment.shipment_type:
        return None
    past = (
        ShippingAdvisory.objects
        .filter(shipment__shipment_type=shipment.shipment_type,
                recommended_type__isnull=False)
        .exclude(shipment=shipment)
        .values_list('recommended_type', flat=True)
    )
    if not past:
        return None
    from collections import Counter
    counts   = Counter(past)
    top_mode = counts.most_common(1)[0]
    pct      = round(top_mode[1] / len(past) * 100)
    return {
        'total':       len(past),
        'top_mode':    top_mode[0],
        'top_pct':     pct,
        'mode_label':  {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL'}.get(top_mode[0], top_mode[0].upper()),
        'ship_type':   shipment.get_shipment_type_display(),
    }


def _run_wmcda_and_advisory(request, shipment, total_exw):
    """Compute the WMCDA scores for this POST and upsert the ShippingAdvisory.

    Best-effort: any failure is caught so a WMCDA problem never blocks the
    (already-saved) ECDT computation. On a POST the result is only persisted —
    the page redirects, so the scores are not rendered here.
    """
    try:
        wmcda_weight   = float(shipment.gross_weight or 0)
        wmcda_volume   = float(request.POST.get('cargo_volume', 0) or 0)
        wmcda_value    = float(total_exw)
        wmcda_urgency  = shipment.urgency or 'normal'
        wmcda_distance = float(request.POST.get('distance_km') or 2600)

        wmcda_scores, wmcda_recommended, _breakdown, _explanation = compute_wmcda(
            wmcda_weight, wmcda_volume, wmcda_value, wmcda_urgency, wmcda_distance
        )

        ShippingAdvisory.objects.update_or_create(
            shipment=shipment,
            defaults={
                'gross_weight':     wmcda_weight,
                'cargo_volume':     wmcda_volume,
                'declared_value':   wmcda_value,
                'urgency_level':    wmcda_urgency,
                'distance_km':      wmcda_distance,
                'lcl_score':        wmcda_scores['lcl'],
                'fcl_score':        wmcda_scores['fcl'],
                'air_score':        wmcda_scores['air'],
                'recommended_type': wmcda_recommended,
                'computed_by':      request.user,
            }
        )
    except Exception as wmcda_err:
        logger.warning('WMCDA auto-compute error: %s', wmcda_err)


def _apply_port_fee_defaults(shipment, container_type, arrastre, wharfage,
                             csf_usd_val, csf_php_val, usd_exchange_rate):
    """Apply server-side LCL/FCL port-fee defaults when the declarant left BOTH
    arrastre and wharfage at 0.

    Mirrors the client-side JS auto-fill so no-JS submissions still get the
    right terminal charges. AIR/LAND keep 0. Returns the (possibly updated)
    (arrastre, wharfage, csf_usd_val, csf_php_val) tuple.
    """
    if arrastre != Decimal('0') or wharfage != Decimal('0'):
        return arrastre, wharfage, csf_usd_val, csf_php_val

    _stype = (shipment.shipment_type or '').lower()
    _csize = container_type.lower() if container_type else ''
    if _stype in ('lcl', 'sea'):
        arrastre    = Decimal('5496.00')
        wharfage    = Decimal('519.35')
        csf_usd_val = Decimal('0.00')
        csf_php_val = Decimal('0.00')
    elif _stype == 'fcl':
        if '40' in _csize:
            arrastre    = Decimal('12608.00')
            wharfage    = Decimal('779.05')
            csf_usd_val = Decimal('10.00')
        else:                              # default: 20FT
            arrastre    = Decimal('5496.00')
            wharfage    = Decimal('519.35')
            csf_usd_val = Decimal('5.00')
        csf_php_val = csf_usd_val * usd_exchange_rate
    # AIR / LAND: leave at 0
    return arrastre, wharfage, csf_usd_val, csf_php_val


def _distribute_freight_insurance(items_data, request):
    """Spread a global total_freight / total_insurance across items by EXW share.

    Only applies when the declarant entered a positive global total but left ALL
    per-item values at 0 (otherwise per-item values are respected). Mutates
    items_data in place and returns it.
    """
    total_exw_for_dist = sum(Decimal(str(it['exw_usd'])) for it in items_data) or Decimal('1')
    total_freight_global   = Decimal(request.POST.get('total_freight',   '0') or '0')
    total_insurance_global = Decimal(request.POST.get('total_insurance', '0') or '0')
    all_fr_zero  = all(Decimal(str(it.get('freight_usd',   0) or 0)) == 0 for it in items_data)
    all_ins_zero = all(Decimal(str(it.get('insurance_usd', 0) or 0)) == 0 for it in items_data)
    if all_fr_zero and total_freight_global > 0:
        for it in items_data:
            prop = Decimal(str(it['exw_usd'])) / total_exw_for_dist
            it['freight_usd'] = float(round(total_freight_global * prop, 4))
    if all_ins_zero and total_insurance_global > 0:
        for it in items_data:
            prop = Decimal(str(it['exw_usd'])) / total_exw_for_dist
            it['insurance_usd'] = float(round(total_insurance_global * prop, 4))
    return items_data


def _parse_posted_line_items(request):
    """Parse the repeated item-row fields of a compute POST into items_data.

    Reads the parallel description[] / exw_value[] / ... lists, pads them all to
    the length of description[], resolves each row's HS-code string, and keeps
    only rows with a positive EXW. Freight / insurance / duty default to '0' so
    downstream Decimal() parsing is safe.
    """
    descriptions  = request.POST.getlist('description[]')
    exw_values    = request.POST.getlist('exw_value[]')
    freights_list = request.POST.getlist('item_freight[]')
    ins_list      = request.POST.getlist('item_insurance[]')
    quantities    = request.POST.getlist('quantity[]')
    units         = request.POST.getlist('unit[]')
    unit_prices   = request.POST.getlist('unit_price[]')
    hs_code_ids   = request.POST.getlist('hs_code_id[]')
    duty_rates    = request.POST.getlist('item_duty_rate[]')
    gws           = request.POST.getlist('gw[]')
    nws           = request.POST.getlist('nw[]')
    pkgs_list     = request.POST.getlist('pkgs[]')

    # Pad all lists to same length as descriptions
    n = len(descriptions)
    def _pad(lst, default=''):
        return (lst + [default] * n)[:n]
    freights_list = _pad(freights_list, '0')
    ins_list      = _pad(ins_list,      '0')
    hs_code_ids   = _pad(hs_code_ids,   '')
    duty_rates    = _pad(duty_rates,     '0')
    gws           = _pad(gws,            '')
    nws           = _pad(nws,            '')
    pkgs_list     = _pad(pkgs_list,      '')
    quantities    = _pad(quantities,     '')
    units         = _pad(units,          '')
    unit_prices   = _pad(unit_prices,    '')

    # Build HS code string lookup map (id → code string)
    valid_hs_ids = [int(h) for h in hs_code_ids if h and h.strip().isdigit()]
    hs_code_map  = {
        str(obj.id): obj.code
        for obj in HSCode.objects.filter(id__in=valid_hs_ids).only('id', 'code')
    } if valid_hs_ids else {}

    return [
        {
            'description':    d.strip(),
            'exw_usd':        e,
            'freight_usd':    f  or '0',
            'insurance_usd':  ins or '0',
            'quantity':       q,
            'unit':           unit,
            'unit_price':     unit_price,
            'hs_code_id':     h,
            'hs_code':        hs_code_map.get(str(h).strip(), ''),
            'duty_rate':      dr or '0',
            'gw':             gw,
            'nw':             nw,
            'pkgs':           pk,
        }
        for d, e, f, ins, q, unit, unit_price, h, dr, gw, nw, pk
        in zip(descriptions, exw_values, freights_list, ins_list,
               quantities, units, unit_prices, hs_code_ids, duty_rates, gws, nws, pkgs_list)
        if e and float(e) > 0
    ]


def _load_items_for_get(request, shipment, existing, shipment_id):
    """Build the item rows that pre-fill the compute form on GET.

    Returns the first non-empty source, in priority order:
      1. a saved DutyComputation,
      2. persisted ShipmentLineItem drafts,
      3. OCR-persisted line items (?ocr=1),
      4. session OCR items / merged fields (?ocr=1).
    Returns None when nothing is available.
    """
    # 1. Saved computation
    items = existing.get_items() if existing else None

    # 2. ShipmentLineItem drafts
    if not items:
        draft_rows = ShipmentLineItem.objects.filter(
            shipment=shipment
        ).select_related('hs_code').order_by('row_order')
        if draft_rows.exists():
            items = []
            for i, li in enumerate(draft_rows, 1):
                items.append({
                    'no':             i,
                    'line_item_id':   li.id,
                    'description':    li.description if li.description != '(draft)' else '',
                    'exw':            float(li.total_val_usd) if li.total_val_usd else '',
                    'quantity':       float(li.quantity) if li.quantity else '',
                    'unit':           li.unit or '',
                    'unit_price':     float(li.unit_price) if li.unit_price else '',
                    'hs_code_id':     li.hs_code_id or '',
                    'hs_code':        li.hs_code.code if li.hs_code else '',
                    'duty_rate':      float(li.duty_rate) if li.duty_rate else '',
                    'item_freight':   float(li.freight) if li.freight else '',
                    'item_insurance': float(li.insurance) if li.insurance else '',
                    'gw':             float(li.gross_weight) if li.gross_weight else '',
                    'nw':             float(li.net_weight) if li.net_weight else '',
                    'pkgs':           li.packages if li.packages else '',
                    'dv_php':         None,
                    'dv_usd':         None,
                    'cud':            None,
                    'is_extracted':   li.source == 'ocr',
                    'confidence':     float(li.confidence),
                })

    # 3. OCR-persisted line items
    if not items and request.GET.get('ocr') == '1':
        db_items = ShipmentLineItem.objects.filter(
            shipment=shipment, source='ocr'
        ).select_related('hs_code').order_by('row_order')
        if db_items.exists():
            items = []
            for i, li in enumerate(db_items, 1):
                hs_id   = li.hs_code_id or ''
                hs_rate = float(li.hs_code.duty_rate) if li.hs_code else 0
                items.append({
                    'no':           i,
                    'line_item_id': li.id,
                    'description':  li.description,
                    'exw':          float(li.total_val_usd) if li.total_val_usd else '',
                    'quantity':     float(li.quantity) if li.quantity else '1',
                    'unit':         li.unit or '',
                    'unit_price':   float(li.unit_price) if li.unit_price else '',
                    'hs_code_id':   hs_id,
                    'duty_rate':    hs_rate,
                    'dv_php':       None,
                    'cud':          None,
                    'item_freight': None,
                    'item_insurance': None,
                    'dv_usd':       None,
                    'gw': '', 'nw': '', 'pkgs': '',
                    'is_extracted': True,
                    'confidence':   float(li.confidence),
                })

    # 4. Session OCR items / merged fields
    if not items and request.GET.get('ocr') == '1':
        _ocr_sid   = request.session.get('ocr_shipment_id')
        _raw_items = request.session.get('ocr_items',  []) if _ocr_sid == shipment_id else []
        _ocr_flds  = request.session.get('ocr_fields', {}) if _ocr_sid == shipment_id else {}

        if _raw_items:
            # Multi-item path: one row per extracted line item
            items = [
                {
                    'no':             i,
                    'line_item_id':   '',
                    'description':    it.get('description', ''),
                    'exw':            it.get('total_value', '') or '',
                    'quantity':       it.get('quantity', '') or '1',
                    'unit':           it.get('unit', ''),
                    'unit_price':     it.get('unit_price', ''),
                    'hs_code_id':     it.get('hs_code_id', ''),
                    'duty_rate':      it.get('duty_rate', 0),
                    'dv_php':         None,
                    'cud':            None,
                    'item_freight':   None,
                    'item_insurance': None,
                    'other_charges':  None,
                    'dv_usd':         None,
                    'gw':             it.get('gross_weight', ''),
                    'nw':             it.get('net_weight', ''),
                    'pkgs':           it.get('num_packages', ''),
                    'is_extracted':   True,
                    'confidence':     it.get('confidence', 0.0),
                }
                for i, it in enumerate(_raw_items, 1)
            ]
        elif _ocr_flds:
            # Single-total fallback: one row from merged OCR totals
            def _val(k):
                v = _ocr_flds.get(k, {})
                return v.get('value', '') if isinstance(v, dict) else v
            items = [{
                'no': 1, 'description': _val('description'),
                'exw': _val('declared_value'),
                'quantity': _val('total_quantity') or '1',
                'unit': '', 'unit_price': '',
                'hs_code_id': '', 'duty_rate': 0,
                'dv_php': None, 'cud': None,
                'item_freight': None, 'item_insurance': None,
                'other_charges': None, 'dv_usd': None,
                'is_extracted': True,
                'confidence': _ocr_flds.get('description', {}).get('confidence', 0.0) if isinstance(_ocr_flds.get('description', {}), dict) else 0.0,
            }]

    return items


def _resolve_prefill_distance_volume(shipment, advisory_ex, ocr_fields):
    """Resolve WMCDA distance and volume defaults for the compute form."""
    def _ocr_field_value(fields, key):
        raw = (fields or {}).get(key, '')
        if isinstance(raw, dict):
            return str(raw.get('value', '') or '').strip()
        return str(raw or '').strip()

    # Priority:
    #   1. Existing saved advisory from a previous computation
    #   2. Session OCR fields from the process shipment page
    #   3. Database-stored OCR fields on uploaded documents
    #   4. Manual/default values
    ocr_country = (
        _ocr_field_value(ocr_fields, 'country_of_origin') or
        _ocr_field_value(ocr_fields, 'origin')
    ).strip()
    ocr_volume = _ocr_field_value(ocr_fields, 'volume_cbm')
    ocr_dimensions = _ocr_field_value(ocr_fields, 'dimensions')

    if not ocr_country or not ocr_volume:
        for doc in shipment.documents.all():
            if not getattr(doc, 'ocr_fields_json', None):
                continue
            try:
                stored = json.loads(doc.ocr_fields_json)
            except (ValueError, TypeError):
                continue

            if not ocr_country:
                ocr_country = (
                    _ocr_field_value(stored, 'country_of_origin') or
                    _ocr_field_value(stored, 'origin')
                ).strip()
            if not ocr_volume:
                ocr_volume = _ocr_field_value(stored, 'volume_cbm')
            if not ocr_dimensions:
                ocr_dimensions = _ocr_field_value(stored, 'dimensions')
            if ocr_country and ocr_volume:
                break

    ocr_distance = _lookup_distance_from_country(ocr_country)

    if advisory_ex and advisory_ex.distance_km:
        prefill_distance = int(advisory_ex.distance_km)
        prefill_distance_src = 'saved'
    elif ocr_distance:
        prefill_distance = ocr_distance
        prefill_distance_src = f'auto — {ocr_country.title()}'
    else:
        prefill_distance = 2600
        prefill_distance_src = 'default'

    if advisory_ex and advisory_ex.cargo_volume:
        prefill_volume = advisory_ex.cargo_volume
        prefill_volume_src = 'saved'
    elif ocr_volume:
        prefill_volume = ocr_volume
        prefill_volume_src = f'auto from OCR: {ocr_dimensions}' if ocr_dimensions else 'auto from OCR'
    else:
        prefill_volume = '0'
        prefill_volume_src = 'manual'

    country_distance_options = _country_distance_options()
    country_distance_map = {
        opt['name']: opt['distance']
        for opt in country_distance_options
    }

    return {
        'prefill_distance': prefill_distance,
        'prefill_distance_src': prefill_distance_src,
        'prefill_origin_country': ocr_country.title() if ocr_country else '',
        'country_distance_options': country_distance_options,
        'country_distance_map': country_distance_map,
        'prefill_volume': prefill_volume,
        'prefill_volume_src': prefill_volume_src,
    }


def _collect_hs_suggestions(shipment, ocr_items, existing):
    """Collect and persist HS code suggestions for the compute workspace."""
    suggestion_parts = []
    if shipment.description:
        suggestion_parts.append(shipment.description)

    for item in (ocr_items or [])[:3]:
        if item.get('description'):
            suggestion_parts.append(item['description'])

    if not suggestion_parts and existing:
        for item in (existing.get_items() or [])[:3]:
            if item.get('description'):
                suggestion_parts.append(item['description'])

    combined_text = ' '.join(suggestion_parts).strip()
    if not combined_text:
        return []

    suggestions = suggest_hs_codes(combined_text, top_n=10)
    for hs in suggestions:
        ShipmentHSCode.objects.get_or_create(
            shipment=shipment,
            hs_code=hs,
            defaults={'is_suggested': True, 'is_confirmed': False},
        )
    return suggestions


@login_required
def compute_shipment(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may compute duties for a shipment
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    hs_codes    = HSCode.objects.filter(is_active=True)
    existing    = DutyComputation.objects.filter(shipment=shipment).first()
    advisory_ex = getattr(shipment, 'shipping_advisory', None)
    documents   = shipment.documents.all()

    result      = None
    items       = None
    wmcda_scores      = None
    wmcda_recommended = None
    wmcda_breakdown   = None
    wmcda_explanation = None
    wmcda_history     = None

    # ── All currency rates from SystemConfig (for JS auto-fill) ──────────────
    all_currency_rates = _load_currency_rates()

    # Current invoice currency (consignee-set); declarant can override on POST
    invoice_currency = (shipment.invoice_currency or 'USD').upper()
    if invoice_currency not in _RATE_KEYS:
        invoice_currency = 'USD'
    default_rate = all_currency_rates.get(invoice_currency, '59.1480')
    usd_exchange_rate = Decimal(str(all_currency_rates.get('USD', _RATE_DEFAULTS['USD'])))

    if request.method == 'POST':
        posted_currency = (request.POST.get('invoice_currency', '') or '').strip().upper()
        if posted_currency in _RATE_KEYS:
            invoice_currency = posted_currency
            shipment.invoice_currency = invoice_currency
            shipment.save(update_fields=['invoice_currency'])
            default_rate = all_currency_rates.get(invoice_currency, default_rate)
        try:
            exchange_rate   = Decimal(request.POST.get('exchange_rate', default_rate) or default_rate)
            arrastre        = Decimal(request.POST.get('arrastre',   '0') or '0')
            wharfage        = Decimal(request.POST.get('wharfage',   '0') or '0')
            csf_usd_val     = Decimal(request.POST.get('csf_usd',   '0') or '0')
            bank_charges    = Decimal(request.POST.get('bank_charges', '0') or '0')
            container_type  = (request.POST.get('container_type', '') or '').strip()
            charge_mode     = normalize_charge_mode(request.POST.get('charge_mode'), shipment.shipment_type)
            cargo_volume    = Decimal(request.POST.get('cargo_volume', '0') or '0')
            gross_weight    = Decimal(str(shipment.gross_weight or 0))
            arrastre, wharfage, revenue_ton = apply_transport_charges(
                charge_mode, arrastre, wharfage,
                gross_weight=gross_weight,
                volume_cbm=cargo_volume,
            )
            csf_php_val     = csf_usd_val * usd_exchange_rate

            arrastre, wharfage, csf_usd_val, csf_php_val = _apply_port_fee_defaults(
                shipment, container_type, arrastre, wharfage,
                csf_usd_val, csf_php_val, usd_exchange_rate,
            )

            items_data = _parse_posted_line_items(request)
            if not items_data:
                messages.error(request, 'Add at least one item with a value.')
                raise ValueError('no items')

            _distribute_freight_insurance(items_data, request)

            items, summary = compute_ecdt(
                items_data, exchange_rate, usd_exchange_rate=usd_exchange_rate,
                arrastre=arrastre, wharfage=wharfage, csf_php=csf_php_val,
                bank_charges=bank_charges
            )

            # Totals for model storage
            total_freight   = sum(Decimal(str(it.get('freight_usd',   0) or 0)) for it in items_data)
            total_insurance = sum(Decimal(str(it.get('insurance_usd', 0) or 0)) for it in items_data)
            result = summary

            # Use first item's HS code + duty rate for model-level fields
            first_hs_id   = next((it['hs_code_id'] for it in items_data if it.get('hs_code_id')), None)
            first_dr      = Decimal(str(items_data[0].get('duty_rate', 0) or 0)) if items_data else Decimal('0')
            hs_code = None
            if first_hs_id:
                try:
                    hs_code = HSCode.objects.get(id=first_hs_id)
                except HSCode.DoesNotExist:
                    pass

            total_exw = sum(Decimal(str(it['exw_usd'])) for it in items_data)

            DutyComputation.objects.update_or_create(
                shipment=shipment,
                defaults={
                    'hs_code':           hs_code,
                    'total_freight':     total_freight,
                    'total_insurance':   total_insurance,
                    'exchange_rate':     exchange_rate,
                    'duty_rate':         first_dr,
                    'declared_value':    total_exw,
                    'items_json':        json.dumps(items),
                    'dutiable_value':    summary['taxable_value'],
                    'customs_duty':      summary['customs_duties'],
                    'vat_base':          summary['vat_base'],
                    'vat_amount':        summary['vat'],
                    'brokerage_fee':     summary['brokerage_fee'],
                    'ipf':               summary['ipf'],
                    'bank_charges':      bank_charges,
                    'arrastre':          arrastre,
                    'wharfage':          wharfage,
                    'csf_usd':           csf_usd_val,
                    'container_type':    container_type or charge_mode,
                    'total_landed_cost': summary['total_landed_cost'],
                    'computed_by':       request.user,
                }
            )

            if shipment.status == 'arrived':
                old_status = shipment.status
                shipment.status = 'computed'
                if not shipment.processed_at:
                    shipment.processed_at = timezone.now()
                shipment.save(update_fields=['status', 'processed_at', 'updated_at'])
                StatusLog.objects.create(
                    shipment=shipment,
                    changed_by=request.user,
                    old_status=old_status,
                    new_status='computed',
                    notes='Duties and taxes computation completed.',
                )
                transaction.on_commit(
                    lambda shipment=shipment, old_status=old_status, changed_by=request.user: notify_shipment_status_change(
                        shipment=shipment,
                        old_status=old_status,
                        new_status='computed',
                        changed_by=changed_by,
                        notes='Duties and taxes computation completed.',
                    )
                )

            # ── Auto-run WMCDA alongside ECDT (best-effort; persisted only) ────
            _run_wmcda_and_advisory(request, shipment, total_exw)

            # Consignee notification is sent by notify_shipment_status_change above
            # when the status transitions to 'computed'. No duplicate needed here.

            messages.success(request, 'Computation & shipping analysis saved!')
            return redirect('computation:compute', shipment_id=shipment.id)

        except ValueError:
            pass
        except Exception as e:
            messages.error(request, f'Computation error: {e}')
            items = result = None

    else:
        # ── GET: pre-load saved data ───────────────────────────────────────────
        items = _load_items_for_get(request, shipment, existing, shipment_id)

        if advisory_ex:
            wmcda_scores = {
                'lcl':  float(advisory_ex.lcl_score  or 0),
                'fcl':  float(advisory_ex.fcl_score  or 0),
                'air':  float(advisory_ex.air_score  or 0),
            }
            wmcda_recommended = advisory_ex.recommended_type
            try:
                _, _, wmcda_breakdown, wmcda_explanation = compute_wmcda(
                    float(advisory_ex.gross_weight),
                    float(advisory_ex.cargo_volume),
                    float(advisory_ex.declared_value),
                    advisory_ex.urgency_level,
                    float(advisory_ex.distance_km),
                )
            except Exception as e:
                logger.debug('WMCDA breakdown re-derive (GET) failed: %s', e)

        # Historical on load
        wmcda_history = _wmcda_history(shipment)

    ocr_fields = request.session.get('ocr_fields', {}) if request.session.get('ocr_shipment_id') == shipment_id else {}
    ocr_items  = request.session.get('ocr_items',  []) if request.session.get('ocr_shipment_id') == shipment_id else []

    prefill_context = _resolve_prefill_distance_volume(shipment, advisory_ex, ocr_fields)
    prefill_distance = prefill_context['prefill_distance']
    prefill_distance_src = prefill_context['prefill_distance_src']
    prefill_origin_country = prefill_context['prefill_origin_country']
    country_distance_options = prefill_context['country_distance_options']
    country_distance_map = prefill_context['country_distance_map']
    prefill_volume = prefill_context['prefill_volume']
    prefill_volume_src = prefill_context['prefill_volume_src']

    hs_suggestions = _collect_hs_suggestions(shipment, ocr_items, existing)

    # ── Declared mode focused breakdown ──────────────────────────────────────────
    declared_score     = None
    declared_breakdown = None
    declared_rating    = None
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

    # Pre-fill freight / insurance from consignee-provided values when no existing computation
    if existing:
        prefill_freight   = float(existing.total_freight   or 0)
        prefill_insurance = float(existing.total_insurance or 0)
    else:
        prefill_freight   = float(shipment.freight_cost   or 0)
        prefill_insurance = float(shipment.insurance_cost or 0)
    # ── Determine initial charge mode for template (drives section visibility) ──
    if existing:
        _ct = (existing.container_type or '').lower()
        if _ct in ('fcl', '20ft', '40ft'):
            computed_mode = 'fcl'
        elif _ct == 'air':
            computed_mode = 'air'
        else:
            computed_mode = 'lcl'
    else:
        _st = (shipment.shipment_type or 'lcl').lower()
        computed_mode = 'lcl' if _st in ('lcl', 'sea', '') else _st

    # ── Guide HS codes — set in session by save_ocr_items on the process page ──
    guide_hs_codes = []
    if str(request.session.get('guide_shipment_id', '')) == str(shipment_id):
        guide_hs_codes = request.session.get('guide_hs_codes', [])

    context = {
        'shipment':           shipment,
        'hs_codes':           hs_codes,
        'existing':           existing,
        'advisory_existing':  advisory_ex,
        'result':             result,
        'items':              items,
        'documents':          documents,
        'ocr_fields':         ocr_fields,
        'ocr_items':          ocr_items,
        'wmcda_scores':       wmcda_scores,
        'wmcda_recommended':  wmcda_recommended,
        'wmcda_breakdown':    wmcda_breakdown,
        'wmcda_explanation':  wmcda_explanation,
        'wmcda_history':      wmcda_history,
        'wmcda_weights':      wmcda_weight_rows(SystemConfig.get),
        'declared_score':     declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':    declared_rating,
        'hs_suggestions':     hs_suggestions,
        'guide_hs_codes':       guide_hs_codes,
        'computed_mode':        computed_mode,
        'prefill_freight':        prefill_freight,
        'prefill_insurance':      prefill_insurance,
        'prefill_volume':         prefill_volume,
        'prefill_volume_src':     prefill_volume_src,
        'prefill_distance':       prefill_distance,
        'prefill_distance_src':   prefill_distance_src,
        'prefill_origin_country': prefill_origin_country,
        'country_distance_options': country_distance_options,
        'country_distance_map':   json.dumps(country_distance_map),
        'invoice_currency':       invoice_currency,
        'all_currency_rates':   json.dumps(all_currency_rates),
        'usd_exchange_rate':    usd_exchange_rate,
        'default_rate':         default_rate,
    }
    # All saved line items (OCR + manual drafts) ordered for ECDT table restore
    context['confirmed_items'] = ShipmentLineItem.objects.filter(
        shipment=shipment
    ).select_related('hs_code').order_by('source', 'row_order')
    return render(request, 'computation/compute.html', context)


# ─── HS Code Suggestion Engine ───────────────────────────────────────────────
