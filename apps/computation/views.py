import json
import os
import tempfile
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from apps.shipments.models import Shipment, ShipmentDocument, HSCode
from apps.supervisor.models import SystemConfig
from .models import DutyComputation, ShippingAdvisory
from .ocr import process_document


# ─── Lookup Tables ────────────────────────────────────────────────────────────

def get_brokerage_fee(taxable_value):
    tv = float(taxable_value)
    if tv <= 10000:   return Decimal('1300')
    if tv <= 20000:   return Decimal('2000')
    if tv <= 30000:   return Decimal('2700')
    if tv <= 40000:   return Decimal('3300')
    if tv <= 50000:   return Decimal('3600')
    if tv <= 60000:   return Decimal('4000')
    if tv <= 100000:  return Decimal('4700')
    if tv <= 200000:  return Decimal('5300')
    # ₱5,300 + 0.125% of excess above ₱200,000
    excess = Decimal(str(round(tv - 200000, 2)))
    return Decimal('5300') + round(excess * Decimal('0.00125'), 2)


def get_ipf(taxable_value):
    tv = float(taxable_value)
    if tv <= 25000:  return Decimal('250')
    if tv <= 50000:  return Decimal('500')
    if tv <= 250000: return Decimal('750')
    if tv <= 500000: return Decimal('1000')
    if tv <= 750000: return Decimal('1500')
    return Decimal('2000')


# ─── Per-Item ECDT Formula ────────────────────────────────────────────────────

def compute_ecdt(items_data, exchange_rate,
                 arrastre=0, wharfage=0, csf_php=0, bank_charges=0):
    """
    items_data keys: exw_usd, freight_usd, insurance_usd, duty_rate,
                     description, quantity, hs_code_id, gw, nw, pkgs
    D/V = EXW + Freight + Insurance  (no auto-3% O/C — matches client CDT tool)
    Total Landed Cost excludes VAT; VAT = 12% of Total Landed Cost
    Brokerage Fee: tiered table up to ₱200,000, then +0.125% of excess
    """
    computed_items = []
    total_dv_php   = Decimal('0')
    total_cud      = Decimal('0')

    for i, item in enumerate(items_data):
        exw            = Decimal(str(item['exw_usd']))
        item_freight   = Decimal(str(item.get('freight_usd',   0) or 0))
        item_insurance = Decimal(str(item.get('insurance_usd', 0) or 0))
        duty_rate      = Decimal(str(item.get('duty_rate',     0) or 0))

        # D/V = EXW + Freight + Insurance
        dv_usd  = exw + item_freight + item_insurance
        dv_php  = dv_usd * exchange_rate
        cud     = dv_php * (duty_rate / Decimal('100'))
        total_dv_php += dv_php
        total_cud    += cud

        computed_items.append({
            'no':             i + 1,
            'description':    item.get('description', ''),
            'quantity':       item.get('quantity', ''),
            'hs_code_id':     item.get('hs_code_id', ''),
            'duty_rate':      float(duty_rate),
            'exw':            float(round(exw, 2)),
            'item_freight':   float(round(item_freight, 2)),
            'item_insurance': float(round(item_insurance, 2)),
            'dv_usd':         float(round(dv_usd, 2)),
            'dv_php':         float(round(dv_php, 2)),
            'cud':            float(round(cud, 2)),
            'gw':             item.get('gw', ''),
            'nw':             item.get('nw', ''),
            'pkgs':           item.get('pkgs', ''),
        })

    taxable_value   = round(total_dv_php, 2)
    customs_duties  = round(total_cud, 2)
    brokerage_fee   = get_brokerage_fee(taxable_value)
    cds             = Decimal('130')
    ipf             = get_ipf(taxable_value)

    arrastre_d      = Decimal(str(arrastre     or 0))
    wharfage_d      = Decimal(str(wharfage     or 0))
    csf_d           = Decimal(str(csf_php      or 0))
    bank_charges_d  = Decimal(str(bank_charges or 0))

    # Total Landed Cost = DV + Bank Charges + CUD + BF + Arrastre + Wharfage + CDS + IPF
    # NOTE: CSF is NOT included in TLC — it appears only in the BOC fees total (FCL)
    total_landed_cost = round(
        taxable_value + bank_charges_d + customs_duties + brokerage_fee
        + cds + ipf + arrastre_d + wharfage_d, 2
    )

    # VAT = 12% of Total Landed Cost (matches client CDT Excel convention)
    vat = round(total_landed_cost * Decimal('0.12'), 2)

    # BOC total = CUD + VAT + CDS + IPF only.
    # CSF is a separate port terminal charge — displayed in the summary but NOT
    # counted in TLC or BOC (matches existing client CDT Excel format).
    boc_total = round(customs_duties + vat + cds + ipf, 2)

    summary = {
        'taxable_value':    taxable_value,
        'bank_charges':     bank_charges_d,
        'customs_duties':   customs_duties,
        'brokerage_fee':    brokerage_fee,
        'cds':              cds,
        'ipf':              ipf,
        'arrastre':         arrastre_d,
        'wharfage':         wharfage_d,
        'csf_php':          csf_d,
        'total_landed_cost': total_landed_cost,
        'vat_base':         total_landed_cost,   # stored as vat_base in model
        'vat':              vat,
        'boc_total':        boc_total,
    }
    return computed_items, summary


# ─── OCR Merge Helpers ───────────────────────────────────────────────────────

# Priority order per field: which document type to prefer when the same field
# appears in more than one document.
_OCR_FIELD_PRIORITY = {
    'declared_value':    ['invoice', 'packing_list'],
    'description':       ['invoice', 'packing_list', 'airway_bill'],
    'total_quantity':    ['packing_list', 'invoice'],
    'gross_weight':      ['airway_bill', 'packing_list'],
    'volume_cbm':        ['airway_bill', 'packing_list'],
    'hawb_number':       ['airway_bill'],
    'invoice_number':    ['invoice'],
    'invoice_date':      ['invoice'],
    'hs_code':           ['invoice', 'airway_bill'],
    'flight_number':     ['airway_bill'],
    'flight_date':       ['airway_bill'],
    'port_loading':      ['airway_bill'],
    'port_discharge':    ['airway_bill'],
    'origin':            ['airway_bill', 'invoice'],
    'destination':       ['airway_bill', 'invoice'],
    'consignee_name':    ['invoice'],
    'consignee_address': ['invoice'],
    'currency':          ['invoice'],
    'net_weight':        ['packing_list'],
    'num_packages':      ['packing_list'],
}

_DOC_LABEL = {
    'invoice':      'Invoice',
    'airway_bill':  'Airway Bill',
    'packing_list': 'Packing List',
}


def merge_ocr_results(results):
    """
    Merge OCR results from multiple documents into one dict.
    Each merged field: {'value': ..., 'confidence': ..., 'source': doc_type}
    Priority per field is defined in _OCR_FIELD_PRIORITY.
    """
    merged = {}
    all_fields = set()
    for doc_data in results.values():
        all_fields.update(doc_data.get('fields', {}).keys())

    for field in all_fields:
        priority = _OCR_FIELD_PRIORITY.get(field, list(results.keys()))
        # Try priority order first, then any remaining doc
        search_order = priority + [d for d in results if d not in priority]
        for doc_type in search_order:
            if doc_type not in results:
                continue
            fdata = results[doc_type].get('fields', {}).get(field)
            if fdata and isinstance(fdata, dict) and fdata.get('value'):
                merged[field] = {
                    'value':      fdata['value'],
                    'confidence': fdata.get('confidence', 0.0),
                    'source':     doc_type,
                }
                break

    return merged


# ─── OCR Extract (single document — kept for fallback) ───────────────────────

@login_required
def ocr_extract(request, shipment_id, doc_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may run OCR on a shipment's documents
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    doc = get_object_or_404(ShipmentDocument, id=doc_id, shipment=shipment)
    try:
        # Download file to a temp path (works for both local and S3/Supabase storage)
        ext = os.path.splitext(doc.file.name)[1] or '.pdf'
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            doc.file.open('rb')
            tmp.write(doc.file.read())
            doc.file.close()
            tmp_path = tmp.name
        try:
            print(f'[OCR] Starting: {doc.file.name} | type={doc.document_type}')
            fields, raw_text = process_document(tmp_path, doc.document_type)
            print(f'[OCR] Raw text length: {len(raw_text) if raw_text else 0} chars')
            print(f'[OCR] Fields returned: {list(fields.keys()) if fields else None}')

            if fields:
                line_items = fields.pop('__items__', [])
                request.session['ocr_fields']      = fields
                request.session['ocr_items']       = line_items
                request.session['ocr_shipment_id'] = shipment_id
                found    = sum(1 for v in fields.values() if isinstance(v, dict) and v.get('value'))
                item_msg = f', {len(line_items)} line items detected' if line_items else ''
                request.session['ocr_toast'] = ('success', f'OCR complete — {found} fields extracted{item_msg}.')
                print(f'[OCR] Success: {found} fields, {len(line_items)} items')
            else:
                request.session['ocr_toast'] = ('warning', 'OCR ran but found no structured fields. Fill in manually.')
                print(f'[OCR] No fields extracted. Raw text snippet: {repr(raw_text[:200]) if raw_text else "EMPTY"}')
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        import traceback
        print(f'[OCR] Exception: {e}')
        traceback.print_exc()
        request.session['ocr_toast'] = ('error', f'OCR failed: {e}')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── OCR Extract All (single button — merges all documents) ──────────────────

@login_required
def ocr_extract_all(request, shipment_id):
    """Run OCR on every invoice/airway_bill/packing_list document at once,
    then merge results into a single unified field set stored in the session."""
    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    documents = shipment.documents.filter(
        document_type__in=['invoice', 'airway_bill', 'packing_list']
    )
    if not documents.exists():
        request.session['ocr_toast'] = ('warning', 'No supported documents uploaded yet.')
        return redirect('declarant:process', shipment_id=shipment_id)

    results  = {}   # { doc_type: { 'fields': {...}, 'items': [...] } }
    failed   = []
    n_fields = 0

    for doc in documents:
        doc_type = doc.document_type
        # If multiple docs of same type, last one wins (edge case)
        try:
            ext = os.path.splitext(doc.file.name)[1] or '.pdf'
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                doc.file.open('rb')
                tmp.write(doc.file.read())
                doc.file.close()
                tmp_path = tmp.name
            try:
                print(f'[OCR-ALL] Processing {doc_type}: {doc.file.name}')
                fields, raw_text = process_document(tmp_path, doc_type)
                if fields:
                    items = fields.pop('__items__', [])
                    results[doc_type] = {'fields': fields, 'items': items}
                    found = sum(1 for v in fields.values()
                                if isinstance(v, dict) and v.get('value'))
                    n_fields += found
                    print(f'[OCR-ALL] {doc_type}: {found} fields, {len(items)} items')
                else:
                    print(f'[OCR-ALL] {doc_type}: no fields extracted')
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            failed.append(_DOC_LABEL.get(doc_type, doc_type))
            print(f'[OCR-ALL] Failed on {doc_type}: {e}')

    if results:
        merged = merge_ocr_results(results)

        # Best items list: invoice first, packing list as fallback
        best_items = (
            results.get('invoice',      {}).get('items') or
            results.get('packing_list', {}).get('items') or
            []
        )

        # Persist in session
        request.session['ocr_results']     = results          # per-doc (for debugging / future use)
        request.session['ocr_merged']      = merged           # merged with source tags
        request.session['ocr_fields']      = merged           # backward-compat key used by compute page
        request.session['ocr_items']       = best_items
        request.session['ocr_shipment_id'] = shipment_id

        n_docs  = len(results)
        n_items = len(best_items)
        msg = (
            f'OCR complete — {n_docs} document{"s" if n_docs != 1 else ""} scanned, '
            f'{n_fields} field{"s" if n_fields != 1 else ""} extracted'
        )
        if n_items:
            msg += f', {n_items} line item{"s" if n_items != 1 else ""} detected'
        if failed:
            msg += f'. Could not read: {", ".join(failed)}'
        request.session['ocr_toast'] = ('success', msg)
    else:
        request.session['ocr_toast'] = (
            'warning',
            'OCR ran but could not extract any fields. '
            'Check document quality or fill in values manually.'
        )

    # Redirect back to process page (default) or compute page
    next_page = request.GET.get('next', 'process')
    if next_page == 'compute':
        return redirect(f'/computation/compute/{shipment_id}/?ocr=1')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Computation ─────────────────────────────────────────────────────────────

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

    # ── Pull default exchange rate from SystemConfig ──
    try:
        default_rate = SystemConfig.objects.get(key='exchange_rate').value
    except SystemConfig.DoesNotExist:
        default_rate = '59.1480'

    if request.method == 'POST':
        try:
            exchange_rate   = Decimal(request.POST.get('exchange_rate', default_rate) or default_rate)
            arrastre        = Decimal(request.POST.get('arrastre',   '0') or '0')
            wharfage        = Decimal(request.POST.get('wharfage',   '0') or '0')
            csf_usd_val     = Decimal(request.POST.get('csf_usd',   '0') or '0')
            bank_charges    = Decimal(request.POST.get('bank_charges', '0') or '0')
            container_type  = (request.POST.get('container_type', '') or '').strip()
            csf_php_val     = csf_usd_val * exchange_rate

            descriptions  = request.POST.getlist('description[]')
            exw_values    = request.POST.getlist('exw_value[]')
            freights_list = request.POST.getlist('item_freight[]')
            ins_list      = request.POST.getlist('item_insurance[]')
            quantities    = request.POST.getlist('quantity[]')
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

            items_data = [
                {
                    'description':    d.strip(),
                    'exw_usd':        e,
                    'freight_usd':    f  or '0',
                    'insurance_usd':  ins or '0',
                    'quantity':       q,
                    'hs_code_id':     h,
                    'duty_rate':      dr or '0',
                    'gw':             gw,
                    'nw':             nw,
                    'pkgs':           pk,
                }
                for d, e, f, ins, q, h, dr, gw, nw, pk
                in zip(descriptions, exw_values, freights_list, ins_list,
                       quantities, hs_code_ids, duty_rates, gws, nws, pkgs_list)
                if e and float(e) > 0
            ]
            if not items_data:
                messages.error(request, 'Add at least one item with a value.')
                raise ValueError('no items')

            items, summary = compute_ecdt(
                items_data, exchange_rate,
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
                    'container_type':    container_type,
                    'total_landed_cost': summary['total_landed_cost'],
                    'computed_by':       request.user,
                }
            )

            # ── Auto-run WMCDA alongside ECDT ──────────────────────────────────
            try:
                wmcda_weight   = float(shipment.gross_weight or 0)
                wmcda_volume   = float(request.POST.get('cargo_volume', 0) or 0)
                wmcda_value    = float(total_exw)
                wmcda_urgency  = shipment.urgency or 'normal'
                wmcda_distance = float(request.POST.get('distance_km', 2600) or 2600)

                wmcda_scores, wmcda_recommended, wmcda_breakdown, wmcda_explanation = compute_wmcda(
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
                        'land_score':       wmcda_scores['land'],
                        'recommended_type': wmcda_recommended,
                        'computed_by':      request.user,
                    }
                )

                # ── Historical recommendation ──────────────────────────────────
                if shipment.shipment_type:
                    past = (
                        ShippingAdvisory.objects
                        .filter(shipment__shipment_type=shipment.shipment_type,
                                recommended_type__isnull=False)
                        .exclude(shipment=shipment)
                        .values_list('recommended_type', flat=True)
                    )
                    if past:
                        from collections import Counter
                        counts   = Counter(past)
                        top_mode = counts.most_common(1)[0]
                        pct      = round(top_mode[1] / len(past) * 100)
                        wmcda_history = {
                            'total':       len(past),
                            'top_mode':    top_mode[0],
                            'top_pct':     pct,
                            'mode_label':  {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL', 'land': 'Land Freight'}.get(top_mode[0], top_mode[0].upper()),
                            'ship_type':   shipment.get_shipment_type_display(),
                        }
            except Exception as wmcda_err:
                print(f'WMCDA auto-compute error: {wmcda_err}')

            # ── Notify consignee ───────────────────────────────────────────────
            try:
                from apps.notifications.utils import create_notification
                create_notification(
                    recipient=shipment.consignee, shipment=shipment,
                    notification_type='computation',
                    title=f'Computation Complete — {shipment.hawb_number}',
                    message=f'Estimated Total Landed Cost: ₱{summary["total_landed_cost"]:,.2f}',
                )
            except Exception:
                pass

            messages.success(request, 'Computation & shipping analysis saved!')

        except ValueError:
            pass
        except Exception as e:
            messages.error(request, f'Computation error: {e}')
            items = result = None

    else:
        # ── GET: pre-load saved data ───────────────────────────────────────────
        if existing:
            items = existing.get_items()

        if advisory_ex:
            wmcda_scores = {
                'lcl':  float(advisory_ex.lcl_score  or 0),
                'fcl':  float(advisory_ex.fcl_score  or 0),
                'air':  float(advisory_ex.air_score  or 0),
                'land': float(advisory_ex.land_score or 0),
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
            except Exception:
                pass

        # OCR pre-fill
        if not items and request.GET.get('ocr') == '1':
            _ocr_sid   = request.session.get('ocr_shipment_id')
            _raw_items = request.session.get('ocr_items',  []) if _ocr_sid == shipment_id else []
            _ocr_flds  = request.session.get('ocr_fields', {}) if _ocr_sid == shipment_id else {}

            if _raw_items:
                # ── Multi-item path: one row per extracted line item ──────────
                items = [
                    {
                        'no':             i,
                        'description':    it.get('description', ''),
                        'exw':            it.get('total_value', ''),
                        'quantity':       it.get('quantity', '') or '1',
                        'hs_code_id':     '',
                        'duty_rate':      0,
                        'dv_php':         None,
                        'cud':            None,
                        'item_freight':   None,
                        'item_insurance': None,
                        'other_charges':  None,
                        'dv_usd':         None,
                    }
                    for i, it in enumerate(_raw_items, 1)
                ]
            elif _ocr_flds:
                # ── Single-total fallback: one row from merged OCR totals ─────
                def _val(k):
                    v = _ocr_flds.get(k, {})
                    return v.get('value', '') if isinstance(v, dict) else v
                items = [{
                    'no': 1, 'description': _val('description'),
                    'exw': _val('declared_value'),
                    'quantity': _val('total_quantity') or '1',
                    'hs_code_id': '', 'duty_rate': 0,
                    'dv_php': None, 'cud': None,
                    'item_freight': None, 'item_insurance': None,
                    'other_charges': None, 'dv_usd': None,
                }]

        # Historical on load
        if shipment.shipment_type:
            past = (
                ShippingAdvisory.objects
                .filter(shipment__shipment_type=shipment.shipment_type,
                        recommended_type__isnull=False)
                .exclude(shipment=shipment)
                .values_list('recommended_type', flat=True)
            )
            if past:
                from collections import Counter
                counts   = Counter(past)
                top_mode = counts.most_common(1)[0]
                pct      = round(top_mode[1] / len(past) * 100)
                wmcda_history = {
                    'total':       len(past),
                    'top_mode':    top_mode[0],
                    'top_pct':     pct,
                    'mode_label':  {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL', 'land': 'Land Freight'}.get(top_mode[0], top_mode[0].upper()),
                    'ship_type':   shipment.get_shipment_type_display(),
                }

    ocr_fields = request.session.get('ocr_fields', {}) if request.session.get('ocr_shipment_id') == shipment_id else {}
    ocr_items  = request.session.get('ocr_items',  []) if request.session.get('ocr_shipment_id') == shipment_id else []

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
        'default_rate':       default_rate,
        'wmcda_scores':       wmcda_scores,
        'wmcda_recommended':  wmcda_recommended,
        'wmcda_breakdown':    wmcda_breakdown,
        'wmcda_explanation':  wmcda_explanation,
        'wmcda_history':      wmcda_history,
        'declared_score':     declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':    declared_rating,
    }
    return render(request, 'computation/compute.html', context)


# ─── Excel Download ───────────────────────────────────────────────────────────

@login_required
def download_computation(request, shipment_id):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        messages.error(request, 'openpyxl not installed. Run: pip install openpyxl')
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment    = get_object_or_404(Shipment, id=shipment_id)

    # Allow: assigned declarant, the shipment's consignee, or any supervisor
    is_assigned_declarant = request.user.role == 'declarant' and shipment.declarant == request.user
    is_consignee          = request.user.role == 'consignee' and shipment.consignee == request.user
    is_supervisor         = request.user.role == 'supervisor'
    if not (is_assigned_declarant or is_consignee or is_supervisor):
        messages.error(request, 'Access denied.')
        return redirect('accounts:login')

    computation = get_object_or_404(DutyComputation, shipment=shipment)
    items       = computation.get_items()

    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'ECDT Summary'
    ws.sheet_view.showGridLines = False

    # ── Style helpers ─────────────────────────────────────────────────────────
    NAVY   = PatternFill('solid', fgColor='1E3A5F')
    YELLOW = PatternFill('solid', fgColor='FFF9C4')
    YELL2  = PatternFill('solid', fgColor='FFFF99')

    def _sd(style='thin', color='BBBBBB'):
        return Side(style=style, color=color)

    T_BRD = Border(left=_sd(), right=_sd(), top=_sd(), bottom=_sd())
    DBL   = Border(top=_sd('thin', '000000'), bottom=_sd('double', '000000'))
    THCK  = Border(top=_sd('medium', '000000'), bottom=_sd('medium', '000000'),
                   left=_sd(), right=_sd())

    _c = Alignment(horizontal='center', vertical='center', wrap_text=True)
    _r = Alignment(horizontal='right',  vertical='center')
    _l = Alignment(horizontal='left',   vertical='center', wrap_text=True)

    # ── Column widths A–N ─────────────────────────────────────────────────────
    for i, w in enumerate([5, 40, 11, 10, 10, 11, 13, 13, 7, 13, 7, 7, 7, 6], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Data helpers ──────────────────────────────────────────────────────────
    _cn      = shipment.consignee
    _co_name = _cn.company_name or _cn.get_full_name() or _cn.username
    _date_s  = computation.updated_at.strftime('%B %d, %Y') if computation.updated_at else '—'
    _hbl     = shipment.hawb_number
    _usdphp  = float(computation.exchange_rate)
    _gw      = f'{float(shipment.gross_weight):.2f}' if shipment.gross_weight else '—'
    _desc    = shipment.description or '—'
    _reg     = shipment.boc_reference or '—'
    _csf_php = float((computation.csf_usd or 0) * (computation.exchange_rate or 0))
    _bank    = float(computation.bank_charges or 0)
    _prep_by = (
        computation.computed_by.get_full_name() or computation.computed_by.username
    ) if computation.computed_by else '—'

    def _lbl(r, c, txt):
        cell = ws.cell(row=r, column=c, value=txt)
        cell.font = Font(size=9, color='555555')
        cell.alignment = _l

    def _val(r, c, txt, bold=False, red=False):
        cell = ws.cell(row=r, column=c, value=txt)
        cell.font = Font(size=10, bold=bold, color='CC0000' if red else '000000')
        cell.alignment = _l
        return cell

    def _mval(r, c1, c2, txt, bold=False, red=False):
        ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
        return _val(r, c1, txt, bold=bold, red=red)

    # ── HEADER BLOCK rows 1–6 ─────────────────────────────────────────────────
    # Row 1: Consignee | Date | Invoice Number
    _lbl(1, 1, 'Consignee:')
    _mval(1, 2, 4, _co_name, bold=True)
    _lbl(1, 6, 'Date:')
    _val(1, 7, _date_s, bold=True)
    _lbl(1, 10, 'Invoice Number:')
    _mval(1, 11, 14, '—')

    # Row 2: Attention | HBL No. (red bold) | Invoice Date
    _lbl(2, 1, 'Attention:')
    _lbl(2, 6, 'HBL No.')
    ws.merge_cells(start_row=2, start_column=7, end_row=2, end_column=9)
    c = ws.cell(row=2, column=7, value=_hbl)
    c.font = Font(size=11, bold=True, color='CC0000')
    c.alignment = _l
    _lbl(2, 10, 'Invoice Date:')
    _mval(2, 11, 14, '—')

    # Row 3: CBM | Shipments of
    _lbl(3, 6, 'CBM')
    _val(3, 7, '—')
    _lbl(3, 10, 'Shipments of')
    _mval(3, 11, 14, _desc)

    # Row 4: ETA | Registry No. | Incoterms (red)
    _lbl(4, 1, 'ETA')
    _val(4, 2, '—')
    _lbl(4, 6, 'Registry No.')
    _val(4, 7, _reg, bold=(_reg != '—'))
    _lbl(4, 10, 'Incoterms:')
    c = ws.cell(row=4, column=11, value='FOB')
    c.font = Font(size=10, bold=True, color='CC0000')
    c.alignment = _l

    # Row 5: Port | USD/PHP (underlined) | Gross Weight
    _lbl(5, 1, 'Port')
    _val(5, 2, '—')
    _lbl(5, 6, 'USD/PHP:')
    c = ws.cell(row=5, column=7, value=_usdphp)
    c.font = Font(size=10, bold=True, underline='single')
    c.alignment = _l
    c.number_format = '0.0000'
    _lbl(5, 10, 'Gross Weight (Kgs):')
    _mval(5, 11, 14, _gw, bold=True)

    # Row 6: CFS (red)
    _lbl(6, 1, 'CFS')
    c = ws.cell(row=6, column=2, value='—')
    c.font = Font(size=10, bold=True, color='CC0000')
    c.alignment = _l

    ws.row_dimensions[7].height = 8   # blank separator

    # ── TABLE HEADERS row 8 ───────────────────────────────────────────────────
    HDR = 8
    for col, h in enumerate([
        '', 'Item Descriptions', 'EXW/FOB\n($)', 'Freight\n($)',
        'Insurance\n($)', 'D/V\n($)', 'D/V\n(PHP)',
        'HS Code', 'Rate', 'CUD\n(PHP)', 'GW', 'NW', 'QTY', 'PKGS',
    ], 1):
        cell = ws.cell(row=HDR, column=col, value=h)
        cell.font = Font(bold=True, color='FFFFFF', size=10)
        cell.fill = NAVY
        cell.border = T_BRD
        cell.alignment = _c
    ws.row_dimensions[HDR].height = 32

    # ── LINE ITEMS ────────────────────────────────────────────────────────────
    tot = dict(exw=0.0, fr=0.0, ins=0.0, dvusd=0.0, dvphp=0.0, cud=0.0)

    for i, item in enumerate(items, 1):
        row = HDR + i

        hs_d = item.get('hs_code_id', '')
        if hs_d:
            try:
                from apps.shipments.models import HSCode as _HSCode
                hs_d = _HSCode.objects.get(id=hs_d).code
            except Exception:
                hs_d = str(hs_d)
        else:
            hs_d = computation.hs_code.code if computation.hs_code else '—'

        row_data = [
            i,
            item.get('description', ''),
            float(item.get('exw',           0) or 0),
            float(item.get('item_freight',  0) or 0),
            float(item.get('item_insurance',0) or 0),
            float(item.get('dv_usd',        0) or 0),
            float(item.get('dv_php',        0) or 0),
            hs_d,
            float(item.get('duty_rate', float(computation.duty_rate)) or 0),
            float(item.get('cud',           0) or 0),
            item.get('gw',       '') or '',
            item.get('nw',       '') or '',
            item.get('quantity', '') or '',
            item.get('pkgs',     '') or '',
        ]

        tot['exw']   += row_data[2]
        tot['fr']    += row_data[3]
        tot['ins']   += row_data[4]
        tot['dvusd'] += row_data[5]
        tot['dvphp'] += row_data[6]
        tot['cud']   += row_data[9]

        for col, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border    = T_BRD
            cell.font      = Font(size=10)
            cell.alignment = _r if col > 2 else _l
            if col in (3, 4, 5, 6): cell.number_format = '#,##0.00'
            elif col in (7, 10):    cell.number_format = '#,##0.00'
            elif col == 9:          cell.number_format = '0"%"'

    # ── TOTAL ROW ─────────────────────────────────────────────────────────────
    TRW = HDR + len(items) + 1
    for col, val in enumerate([
        None, 'TOTAL',
        tot['exw'], tot['fr'], tot['ins'], tot['dvusd'], tot['dvphp'],
        None, None, tot['cud'],
        None, None, None, None,
    ], 1):
        cell = ws.cell(row=TRW, column=col, value=val)
        cell.font   = Font(bold=True, size=10)
        cell.border = THCK
        cell.alignment = _r if col > 2 else _l
        if col in (3, 4, 5, 6): cell.number_format = '#,##0.00'
        elif col in (7, 10):    cell.number_format = '#,##0.00'

    # "Estimated" italic note under total row
    ws.merge_cells(start_row=TRW+1, start_column=3, end_row=TRW+1, end_column=5)
    c = ws.cell(row=TRW+1, column=3, value='Estimated')
    c.font = Font(size=9, italic=True, color='CC0000')
    c.alignment = _c

    # ── SUMMARY SECTION ───────────────────────────────────────────────────────
    SR = TRW + 3

    _boc_total = (
        float(computation.customs_duty or 0) +
        float(computation.vat_amount   or 0) +
        130.0 +
        float(computation.ipf          or 0)
    )

    left_rows = [
        ('Taxable Value',         float(computation.dutiable_value    or 0), False),
        ('Bank Charges',          _bank or None,                             False),
        ('Customs Duties',        float(computation.customs_duty      or 0), False),
        ('Brokerage Fee',         float(computation.brokerage_fee     or 0), False),
        ('Arrastre',              float(computation.arrastre          or 0), False),
        ('Wharfage',              float(computation.wharfage          or 0), False),
    ]
    if _csf_php:
        left_rows.append(('CSF (Container Service Fee)', _csf_php, False))
    left_rows += [
        ('Customs Docs. Stamp',   130.0,                                     False),
        ('Import Processing Fee', float(computation.ipf               or 0), False),
        ('Total Landed Cost',     float(computation.total_landed_cost  or 0), True),
        ('VAT 12%',               float(computation.vat_amount        or 0), False),
    ]

    right_rows = [
        ('CUD',   float(computation.customs_duty or 0), False),
        ('VAT',   float(computation.vat_amount   or 0), False),
        ('CDS',   130.0,                               False),
        ('IPF',   float(computation.ipf          or 0), False),
        ('TOTAL', _boc_total,                          True),
    ]

    # "SUMMARY;" header label above the right box
    ws.merge_cells(start_row=SR-1, start_column=11, end_row=SR-1, end_column=14)
    c = ws.cell(row=SR-1, column=11, value='SUMMARY;')
    c.font = Font(bold=True, size=10)
    c.alignment = _l

    # Left: labels F-I merged, value at col J
    for r_off, (lbl, val, is_tlc) in enumerate(left_rows):
        r = SR + r_off
        ws.merge_cells(start_row=r, start_column=6, end_row=r, end_column=9)
        lc = ws.cell(row=r, column=6, value=lbl)
        lc.font = Font(bold=is_tlc, size=10)
        lc.alignment = _l

        disp = val if val is not None else '-'
        vc = ws.cell(row=r, column=10, value=disp)
        vc.font = Font(bold=is_tlc, size=10)
        vc.alignment = _r
        if isinstance(disp, float):
            vc.number_format = '#,##0.00'
        if is_tlc:
            lc.border = DBL
            vc.border = DBL

    # Right: labels K-L merged, value M-N merged
    for r_off, (lbl, val, is_total) in enumerate(right_rows):
        r = SR + r_off
        ws.merge_cells(start_row=r, start_column=11, end_row=r, end_column=12)
        lc = ws.cell(row=r, column=11, value=lbl)
        lc.font = Font(bold=is_total, size=10)
        lc.alignment = _l
        if is_total:
            lc.fill   = YELLOW
            lc.border = Border(
                top=_sd('medium', '000000'), bottom=_sd('medium', '000000'),
                left=_sd('medium', '000000'),
            )

        ws.merge_cells(start_row=r, start_column=13, end_row=r, end_column=14)
        vc = ws.cell(row=r, column=13, value=float(val))
        vc.number_format = '#,##0.00'
        vc.alignment = _r
        vc.font = Font(bold=is_total, size=10)
        if is_total:
            vc.fill   = YELLOW
            vc.border = Border(
                top=_sd('medium', '000000'), bottom=_sd('medium', '000000'),
                right=_sd('medium', '000000'),
            )

    # ── DISCLAIMER (yellow box, 2 lines) ─────────────────────────────────────
    DR = SR + max(len(left_rows), len(right_rows)) + 1
    for line_off, txt in enumerate([
        'ESTIMATED COMPUTATION ONLY.',
        'FINAL ASSESSMENT WILL BE BASED ON CUSTOMS FINDINGS.',
    ]):
        r = DR + line_off
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        c = ws.cell(row=r, column=1, value=txt)
        c.font = Font(bold=True, size=9)
        c.alignment = _l
        for col in range(1, 9):
            ws.cell(row=r, column=col).fill = YELL2

    # ── SIGNATURE LINE ────────────────────────────────────────────────────────
    SIGR = DR + 3
    ws.cell(row=SIGR, column=9, value='Prepared By:').font = Font(size=10)
    ws.cell(row=SIGR, column=9).alignment = _l
    ws.merge_cells(start_row=SIGR, start_column=10, end_row=SIGR, end_column=11)
    ws.cell(row=SIGR, column=10, value=_prep_by).font = Font(size=10, italic=True)
    ws.cell(row=SIGR, column=10).alignment = _l
    ws.cell(row=SIGR, column=13, value='Conforme:').font = Font(size=10)
    ws.cell(row=SIGR, column=13).alignment = _l

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename=ECDT_{shipment.hawb_number}.xlsx'
    )
    wb.save(response)
    return response


# ─── HS Code Search ───────────────────────────────────────────────────────────

@login_required
def hs_code_search(request):
    query   = request.GET.get('q', '')
    results = []
    if query:
        from django.db.models import Q
        results = HSCode.objects.filter(
            Q(code__icontains=query) | Q(description__icontains=query),
            is_active=True
        )[:10]
    return render(request, 'computation/hs_search.html', {
        'query': query, 'results': results,
    })


# ─── Graduated WMCDA ─────────────────────────────────────────────────────────

def _lerp(x, x0, x1, y0, y1):
    if x <= x0: return y0
    if x >= x1: return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def compute_wmcda(weight, volume, value, urgency, distance):
    # ── Urgency factor ────────────────────────────────────────────────────────
    # 0.0 = standard/normal, 0.5 = priority, 1.0 = urgent, 1.3 = rush
    _uf = {'standard': 0.0, 'normal': 0.0, 'priority': 0.5, 'urgent': 1.0, 'rush': 1.3}.get(urgency, 0.0)
    _is_time_critical = urgency in ('urgent', 'rush')
    _urgency_label    = {'standard': 'standard', 'normal': 'standard',
                         'priority': 'priority', 'urgent': 'urgent', 'rush': 'rush'}.get(urgency, urgency)

    # ── Land freight viability flag ───────────────────────────────────────────
    # Land freight is only practical for domestic PH routes or short ASEAN
    # cross-border routes. International sea/air routes (>1500 km) make land
    # freight impractical.
    _land_viable = distance <= 1500

    # ── Cost scores ───────────────────────────────────────────────────────────
    # LCL: cost scales with CBM. Use volume if provided, else weight as proxy.
    if volume > 0:
        lcl_cost = max(0.20, _lerp(volume, 0, 15, 0.92, 0.28))   # ideal <5 CBM, poor >15
        fcl_cost = min(0.95, _lerp(volume, 0, 15, 0.22, 0.90))   # ideal >15 CBM (fills container)
    else:
        lcl_cost = max(0.25, _lerp(weight, 0, 1000, 0.88, 0.35))
        fcl_cost = _lerp(value, 0, 30000, 0.30, 0.88)

    air_cost  = max(0.15, _lerp(weight, 0, 500, 0.55, 0.18))     # expensive per-kg above 100 kg
    land_cost = max(0.20, _lerp(distance, 0, 1500, 0.90, 0.30)) if _land_viable else 0.15

    # ── Time scores ───────────────────────────────────────────────────────────
    _base_lcl_time  = max(0.30, _lerp(distance, 0, 2000, 0.72, 0.50))
    _base_fcl_time  = max(0.35, _lerp(distance, 0, 2000, 0.78, 0.55))
    _base_air_time  = 0.62
    _base_land_time = max(0.20, _lerp(distance, 0, 1500, 0.88, 0.28)) if _land_viable else 0.20

    lcl_time  = max(0.20, _base_lcl_time  - 0.37 * _uf)   # worse under urgency
    fcl_time  = max(0.25, _base_fcl_time  - 0.30 * _uf)   # worse under urgency
    air_time  = min(0.99, _base_air_time  + 0.34 * _uf)   # better under urgency
    land_time = max(0.15, _base_land_time - 0.20 * _uf) if _land_viable else 0.15

    # ── Cargo suitability scores (weight + volume blended) ────────────────────
    # Physical weight component
    lcl_w  = _lerp(weight, 0, 2000, 0.92, 0.28)
    fcl_w  = _lerp(weight, 0, 2000, 0.18, 0.95)
    air_w  = max(0.10, _lerp(weight, 0, 300, 0.95, 0.15))
    land_w = _lerp(weight, 0, 2000, 0.70, 0.90)

    if volume > 0:
        # Volume (CBM) component — critical for LCL vs FCL decision
        # LCL sweet spot: <5 CBM. At 15 CBM (20ft container threshold) it's poor.
        # FCL sweet spot: >15 CBM. Below 5 CBM wastes the container.
        # Air: very harsh above 3 CBM (volumetric weight cost explodes).
        # Land: trucks are flexible; minimal volume penalty.
        lcl_v  = max(0.15, _lerp(volume, 0, 15, 0.95, 0.18))
        fcl_v  = min(0.95, _lerp(volume, 0, 15, 0.18, 0.95))
        air_v  = max(0.10, _lerp(volume, 0,  3, 0.95, 0.10))
        land_v = max(0.55, _lerp(volume, 0, 50, 0.90, 0.60))
        # Blend: 55% weight, 45% volume
        lcl_weight  = round(0.55 * lcl_w  + 0.45 * lcl_v,  3)
        fcl_weight  = round(0.55 * fcl_w  + 0.45 * fcl_v,  3)
        air_weight  = round(0.55 * air_w  + 0.45 * air_v,  3)
        land_weight = round(0.55 * land_w + 0.45 * land_v, 3) if _land_viable else 0.20
    else:
        # No volume data — weight only
        lcl_weight  = lcl_w
        fcl_weight  = fcl_w
        air_weight  = air_w
        land_weight = land_w if _land_viable else 0.20

    # ── Risk scores ───────────────────────────────────────────────────────────
    lcl_risk  = _lerp(value, 0, 20000, 0.82, 0.40)
    fcl_risk  = 0.70
    air_risk  = _lerp(value, 0, 20000, 0.62, 0.92)
    land_risk = max(0.30, _lerp(distance, 0, 1500, 0.72, 0.38)) if _land_viable else 0.25

    # ── Criterion weights from SystemConfig ───────────────────────────────────
    try:
        w_cost   = float(SystemConfig.get('wmcda_w_cost',   '35')) / 100
        w_time   = float(SystemConfig.get('wmcda_w_time',   '30')) / 100
        w_weight = float(SystemConfig.get('wmcda_w_weight', '20')) / 100
        w_risk   = float(SystemConfig.get('wmcda_w_risk',   '15')) / 100
    except Exception:
        w_cost, w_time, w_weight, w_risk = 0.35, 0.30, 0.20, 0.15

    def tws(c, t, wt, r):
        return round(c * w_cost + t * w_time + wt * w_weight + r * w_risk, 4)

    scores = {
        'lcl':  tws(lcl_cost,  lcl_time,  lcl_weight,  lcl_risk),
        'fcl':  tws(fcl_cost,  fcl_time,  fcl_weight,  fcl_risk),
        'air':  tws(air_cost,  air_time,  air_weight,  air_risk),
        'land': tws(land_cost, land_time, land_weight, land_risk),
    }
    recommended = max(scores, key=scores.get)

    breakdown = {
        'lcl':  {'cost': round(lcl_cost,  3), 'time': round(lcl_time,  3),
                 'weight': round(lcl_weight,  3), 'risk': round(lcl_risk,  3)},
        'fcl':  {'cost': round(fcl_cost,  3), 'time': round(fcl_time,  3),
                 'weight': round(fcl_weight,  3), 'risk': round(fcl_risk,  3)},
        'air':  {'cost': round(air_cost,  3), 'time': round(air_time,  3),
                 'weight': round(air_weight,  3), 'risk': round(air_risk,  3)},
        'land': {'cost': round(land_cost, 3), 'time': round(land_time, 3),
                 'weight': round(land_weight, 3), 'risk': round(land_risk, 3)},
    }

    weight_label = f'{weight:.0f} kg'
    value_label  = f'${value:,.0f}'
    dist_label   = f'{distance:.0f} km'
    vol_label    = f'{volume:.2f} CBM' if volume > 0 else ''

    cargo_desc = f'{weight_label}{", " + vol_label if vol_label else ""}'

    explanations = {
        'lcl': (
            f'LCL is cost-efficient for small-to-moderate cargo ({cargo_desc}). '
            f'{"Not recommended — slower sea transit conflicts with " + _urgency_label + " urgency." if _is_time_critical else "Suitable transit time for this urgency level."}'
        ),
        'fcl': (
            f'FCL is optimal for large or heavy cargo. '
            f'{"Volume of " + vol_label + " justifies a dedicated container. " if volume > 10 else ""}'
            f'{"Cargo of " + cargo_desc + " and value of " + value_label + " justify the container cost." if value > 10000 or weight > 500 else "May underutilize a full container for this cargo size."}'
            f'{" Sea transit may be too slow for " + _urgency_label + " urgency." if _is_time_critical else ""}'
        ),
        'air': (
            f'{"🚨 Rush — Air Freight only viable option for immediate delivery. " if urgency == "rush" else ""}'
            f'{"⚡ Air Freight recommended — urgency requires fastest transit. " if urgency == "urgent" else ""}'
            f'{"⏩ Air Freight ideal for priority delivery at " + value_label + ". " if urgency == "priority" else ""}'
            f'{"Air Freight offers best security and speed for high-value goods at " + value_label + "." if value > 10000 and not _is_time_critical else ""}'
            f'{"Air Freight is competitive for this shipment profile." if not _is_time_critical and value <= 10000 else ""}'
        ),
        'land': (
            f'{"🚛 Land Freight is viable for this regional route (" + dist_label + ")." if distance <= 1000 else "Land Freight suited for this shorter route (" + dist_label + ")."} '
            f'Cargo of {cargo_desc} is well-suited for road transport. '
            f'{"Short-haul land routes can accommodate " + _urgency_label + " urgency." if _is_time_critical and distance <= 500 else "Suitable for this urgency level." if not _is_time_critical else "Road transit may be too slow for time-critical urgency on this route."}'
        ),
    }
    explanation = explanations.get(recommended, '')

    return scores, recommended, breakdown, explanation


# ─── Shipping Advisory (auto-populated) ──────────────────────────────────────

@login_required
def shipping_advisory(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may access the shipping advisory
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    existing = ShippingAdvisory.objects.filter(shipment=shipment).first()
    result = breakdown = explanation = None
    scores = None

    # ── Auto-populate from shipment + computation data ──
    computation = getattr(shipment, 'computation', None)

    if existing:
        auto_weight   = float(existing.gross_weight)
        auto_volume   = float(existing.cargo_volume)
        auto_value    = float(existing.declared_value)
        auto_urgency  = existing.urgency_level
        auto_distance = float(existing.distance_km)
    else:
        # Pull weight from shipment model field
        auto_weight = float(shipment.gross_weight) if shipment.gross_weight else 0.0
        # Pull declared value from computation (USD) or shipment
        if computation and computation.declared_value:
            auto_value = float(computation.declared_value)
        elif shipment.declared_value:
            auto_value = float(shipment.declared_value)
        else:
            auto_value = 0.0
        auto_volume   = 0.0
        auto_urgency  = shipment.urgency
        auto_distance = 2600.0  # Default: Incheon, Korea → Manila, Philippines

    # Determine which fields were auto-populated vs missing
    missing_fields = []
    if not auto_weight:
        missing_fields.append('Gross Weight (kg)')
    if not auto_value:
        missing_fields.append('Declared Value (USD)')

    auto_data = {
        'gross_weight':   auto_weight,
        'cargo_volume':   auto_volume,
        'declared_value': auto_value,
        'urgency_level':  auto_urgency,
        'distance_km':    auto_distance,
    }
    auto_sources = {
        'gross_weight':   'shipment' if (not existing and shipment.gross_weight) else ('advisory' if existing else 'manual'),
        'declared_value': 'computation' if (not existing and computation and computation.declared_value) else ('advisory' if existing else 'manual'),
        'urgency_level':  'shipment' if not existing else 'advisory',
        'distance_km':    'default' if not existing else 'advisory',
        'cargo_volume':   'advisory' if existing else 'manual',
    }

    if request.method == 'POST':
        try:
            weight   = float(request.POST.get('gross_weight', 0))
            volume   = float(request.POST.get('cargo_volume', 0))
            value    = float(request.POST.get('declared_value', 0))
            urgency  = request.POST.get('urgency_level', 'normal')
            distance = float(request.POST.get('distance_km', 2600))

            scores, recommended, breakdown, explanation = compute_wmcda(
                weight, volume, value, urgency, distance
            )

            ShippingAdvisory.objects.update_or_create(
                shipment=shipment,
                defaults={
                    'gross_weight':     weight,
                    'cargo_volume':     volume,
                    'declared_value':   value,
                    'urgency_level':    urgency,
                    'distance_km':      distance,
                    'lcl_score':        scores['lcl'],
                    'fcl_score':        scores['fcl'],
                    'air_score':        scores['air'],
                    'recommended_type': recommended,
                    'computed_by':      request.user,
                }
            )
            result = recommended
            messages.success(request, f'Recommendation: {recommended.upper()}')

            # Notify consignee of the advisory result
            try:
                from apps.notifications.utils import create_notification
                label_map = {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL', 'land': 'Land Freight'}
                create_notification(
                    recipient=shipment.consignee,
                    shipment=shipment,
                    notification_type='status_update',
                    title=f'Shipping Advisory Ready — {shipment.hawb_number}',
                    message=(
                        f'WMCDA Recommendation: {label_map.get(recommended, recommended.upper())}. '
                        f'{explanation[:120] if explanation else ""}'
                    ),
                )
            except Exception:
                pass

        except Exception as e:
            messages.error(request, f'Error: {e}')

    context = {
        'shipment':      shipment,
        'existing':      existing,
        'result':        result,
        'scores':        scores,
        'breakdown':     breakdown,
        'explanation':   explanation,
        'auto_data':     auto_data,
        'auto_sources':  auto_sources,
        'missing_fields': missing_fields,
    }
    return render(request, 'computation/advisory.html', context)


# ─── Save Declarant Advisory ──────────────────────────────────────────────────

@login_required
def save_declarant_advisory(request, shipment_id):
    if request.method != 'POST':
        return redirect('computation:advisory', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    advisory = ShippingAdvisory.objects.filter(shipment=shipment).first()
    if not advisory:
        messages.error(request, 'Run the WMCDA computation first before saving an advisory.')
        return redirect('computation:advisory', shipment_id=shipment_id)

    recommendation = request.POST.get('declarant_recommendation', '').strip()
    note = request.POST.get('declarant_note', '').strip()

    valid_types = {'air', 'lcl', 'fcl', 'land', ''}
    if recommendation not in valid_types:
        messages.error(request, 'Invalid shipping type selected.')
        return redirect('computation:advisory', shipment_id=shipment_id)

    advisory.declarant_recommendation = recommendation or None
    advisory.declarant_note = note or None
    advisory.save(update_fields=['declarant_recommendation', 'declarant_note'])

    if recommendation:
        label_map = {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL', 'land': 'Land Freight'}
        mode_label = label_map.get(recommendation, recommendation.upper())
        try:
            from apps.notifications.utils import create_notification
            create_notification(
                recipient=shipment.consignee,
                shipment=shipment,
                notification_type='status_update',
                title=f'Declarant Advisory — {shipment.hawb_number}',
                message=(
                    f'Your declarant recommends {mode_label} for your shipment. '
                    f'{note}' if note else f'Your declarant recommends {mode_label} for your shipment.'
                ),
            )
        except Exception:
            pass
        messages.success(request, f'Advisory saved — {mode_label} recommended to consignee.')
    else:
        messages.success(request, 'Declarant advisory cleared.')

    return redirect('computation:advisory', shipment_id=shipment_id)
