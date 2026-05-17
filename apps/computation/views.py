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
    return Decimal('6000')


def get_ipf(taxable_value):
    tv = float(taxable_value)
    if tv <= 25000:  return Decimal('250')
    if tv <= 50000:  return Decimal('500')
    if tv <= 250000: return Decimal('750')
    if tv <= 500000: return Decimal('1000')
    if tv <= 750000: return Decimal('1500')
    return Decimal('2000')


# ─── Per-Item ECDT Formula ────────────────────────────────────────────────────

def compute_ecdt(items_data, total_freight, total_insurance, exchange_rate):
    total_exw = sum(Decimal(str(item['exw_usd'])) for item in items_data)
    if total_exw <= 0:
        total_exw = Decimal('0')

    computed_items = []
    total_dv_php   = Decimal('0')
    total_cud      = Decimal('0')

    for i, item in enumerate(items_data):
        exw        = Decimal(str(item['exw_usd']))
        duty_rate  = Decimal(str(item.get('duty_rate', 0) or 0))
        if total_exw > 0:
            item_freight   = (exw / total_exw) * total_freight
            item_insurance = (exw / total_exw) * total_insurance
        else:
            item_freight = item_insurance = Decimal('0')

        other_charges = exw * Decimal('0.03')
        dv_usd        = exw + item_freight + item_insurance + other_charges
        dv_php        = dv_usd * exchange_rate
        cud           = dv_php * (duty_rate / Decimal('100'))
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
            'other_charges':  float(round(other_charges, 2)),
            'dv_usd':         float(round(dv_usd, 2)),
            'dv_php':         float(round(dv_php, 2)),
            'cud':            float(round(cud, 2)),
        })

    taxable_value  = round(total_dv_php, 2)
    customs_duties = round(total_cud, 2)
    vat_base       = taxable_value + customs_duties
    vat            = round(vat_base * Decimal('0.12'), 2)
    brokerage_fee  = get_brokerage_fee(taxable_value)
    cds            = Decimal('130')
    ipf            = get_ipf(taxable_value)
    total_landed_cost = round(taxable_value + customs_duties + vat + brokerage_fee + cds + ipf, 2)

    summary = {
        'taxable_value':    taxable_value,
        'customs_duties':   customs_duties,
        'vat_base':         vat_base,
        'vat':              vat,
        'brokerage_fee':    brokerage_fee,
        'cds':              cds,
        'ipf':              ipf,
        'total_landed_cost': total_landed_cost,
    }
    return computed_items, summary


# ─── OCR Extract ─────────────────────────────────────────────────────────────

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
            total_freight   = Decimal(request.POST.get('total_freight',   '0') or '0')
            total_insurance = Decimal(request.POST.get('total_insurance', '0') or '0')
            exchange_rate   = Decimal(request.POST.get('exchange_rate',   default_rate) or default_rate)

            descriptions  = request.POST.getlist('description[]')
            exw_values    = request.POST.getlist('exw_value[]')
            quantities    = request.POST.getlist('quantity[]')
            hs_code_ids   = request.POST.getlist('hs_code_id[]')
            duty_rates    = request.POST.getlist('item_duty_rate[]')

            # Pad lists to same length as descriptions
            n = len(descriptions)
            hs_code_ids = (hs_code_ids + [''] * n)[:n]
            duty_rates  = (duty_rates  + ['0'] * n)[:n]

            items_data = [
                {
                    'description': d.strip(),
                    'exw_usd':     e,
                    'quantity':    q,
                    'hs_code_id':  h,
                    'duty_rate':   dr or '0',
                }
                for d, e, q, h, dr in zip(descriptions, exw_values, quantities, hs_code_ids, duty_rates)
                if e and float(e) > 0
            ]
            if not items_data:
                messages.error(request, 'Add at least one item with a value.')
                raise ValueError('no items')

            items, summary = compute_ecdt(
                items_data, total_freight, total_insurance, exchange_rate
            )
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
            ocr = request.session.get('ocr_fields', {})
            if ocr:
                def _val(k):
                    v = ocr.get(k, {})
                    return v.get('value', '') if isinstance(v, dict) else v
                items = [{
                    'no': 1, 'description': _val('description'),
                    'exw': _val('declared_value'),
                    'quantity': _val('total_quantity') or '1',
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

    wb = Workbook()
    ws = wb.active
    ws.title = 'ECDT Summary'

    header_fill  = PatternFill('solid', fgColor='1E3A5F')
    summary_fill = PatternFill('solid', fgColor='0F172A')
    total_fill   = PatternFill('solid', fgColor='172554')
    hdr_font     = Font(bold=True, color='FFFFFF', size=10)
    title_font   = Font(bold=True, color='3B82F6', size=14)
    bold_white   = Font(bold=True, color='FFFFFF', size=10)
    blue_font    = Font(bold=True, color='3B82F6', size=12)
    thin         = Side(style='thin', color='334155')
    border       = Border(left=thin, right=thin, top=thin, bottom=thin)
    center       = Alignment(horizontal='center', vertical='center', wrap_text=True)
    right_align  = Alignment(horizontal='right', vertical='center')

    ws.merge_cells('A1:L1')
    ws['A1'] = 'R3-PCR — ECDT Pre-Clearance Computation Summary'
    ws['A1'].font = title_font
    ws['A1'].alignment = center

    ws.merge_cells('A2:L2')
    ws['A2'] = (
        f'Shipment Ref: {shipment.hawb_number}  |  '
        f'Consignee: {shipment.consignee.get_full_name() or shipment.consignee.username}  |  '
        f'Exchange Rate: {float(computation.exchange_rate):.4f}  |  '
        f'Duty Rate: {float(computation.duty_rate):.2f}%'
    )
    ws['A2'].font = Font(color='94A3B8', size=9)
    ws['A2'].alignment = center

    headers = [
        '#', 'Description', 'EXW (USD)', 'Freight\n(USD)',
        'Insurance\n(USD)', 'O/C (USD)', 'D/V (USD)', 'D/V (PHP)',
        'HS Code', 'Rate %', 'CUD (PHP)', 'Quantity',
    ]
    hs_code_display = computation.hs_code.code if computation.hs_code else '—'
    duty_rate_pct   = float(computation.duty_rate)

    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=col, value=h)
        cell.font      = hdr_font
        cell.fill      = header_fill
        cell.border    = border
        cell.alignment = center

    ws.row_dimensions[4].height = 30

    for i, item in enumerate(items, 1):
        row  = 4 + i
        data = [
            item.get('no', i), item.get('description', ''),
            item.get('exw', 0),          item.get('item_freight', 0),
            item.get('item_insurance', 0), item.get('other_charges', 0),
            item.get('dv_usd', 0),       item.get('dv_php', 0),
            hs_code_display,             duty_rate_pct,
            item.get('cud', 0),          item.get('quantity', ''),
        ]
        for col, val in enumerate(data, 1):
            cell = ws.cell(row=row, column=col, value=val)
            cell.border    = border
            cell.alignment = right_align if col > 2 else Alignment(vertical='center')
            if col in (3, 4, 5, 6, 7): cell.number_format = '#,##0.00'
            elif col in (8, 11):        cell.number_format = '₱#,##0.00'
            elif col == 10:             cell.number_format = '0.00"%"'

    summary_row = 4 + len(items) + 2
    summaries = [
        ('Taxable Value (Sum of D/V PHP)',      computation.dutiable_value,    False),
        ('Customs Duties (Total CUD)',          computation.customs_duty,      False),
        ('VAT Base (Taxable Value + CUD)',      computation.vat_base,          False),
        ('VAT (12%)',                           computation.vat_amount,        False),
        ('Brokerage Fee',                       computation.brokerage_fee,     False),
        ('Customs Documentary Stamp (CDS)',     130,                           False),
        ('Import Processing Fee (IPF)',         computation.ipf,               False),
        ('TOTAL LANDED COST',                   computation.total_landed_cost, True),
    ]

    ws.cell(row=summary_row - 1, column=8, value='SUMMARY').font = bold_white

    for r_offset, (label, value, is_total) in enumerate(summaries):
        row        = summary_row + r_offset
        label_cell = ws.cell(row=row, column=8, value=label)
        value_cell = ws.cell(row=row, column=12, value=float(value) if value else 0)
        label_cell.border = value_cell.border = border
        value_cell.number_format = '₱#,##0.00'
        value_cell.alignment     = right_align
        if is_total:
            label_cell.font = value_cell.font = blue_font
            label_cell.fill = value_cell.fill = total_fill
        else:
            label_cell.font  = Font(color='94A3B8', size=10)
            label_cell.fill  = summary_fill
            value_cell.font  = Font(color='F1F5F9', size=10)
            value_cell.fill  = summary_fill

    from openpyxl.utils import get_column_letter
    col_widths = [5, 35, 12, 12, 12, 12, 12, 14, 12, 8, 14, 10]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    note_row = summary_row + len(summaries) + 2
    ws.merge_cells(f'A{note_row}:L{note_row}')
    ws[f'A{note_row}'] = (
        'ESTIMATED COMPUTATION ONLY. Final assessment will be based on BOC/Customs findings. '
        'Generated by R3-PCR Pre-Clearance DSS.'
    )
    ws[f'A{note_row}'].font = Font(color='64748B', italic=True, size=8)

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
        results = HSCode.objects.filter(description__icontains=query, is_active=True)[:10]
    return render(request, 'computation/hs_search.html', {
        'query': query, 'results': results,
    })


# ─── Graduated WMCDA ─────────────────────────────────────────────────────────

def _lerp(x, x0, x1, y0, y1):
    if x <= x0: return y0
    if x >= x1: return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def compute_wmcda(weight, volume, value, urgency, distance):
    # Urgency factor: 0.0 = standard, 0.5 = priority, 1.0 = urgent, 1.3 = rush
    _uf = {'standard': 0.0, 'normal': 0.0, 'priority': 0.5, 'urgent': 1.0, 'rush': 1.3}.get(urgency, 0.0)

    lcl_cost  = _lerp(value, 0, 30000, 0.90, 0.30)
    fcl_cost  = _lerp(value, 0, 30000, 0.30, 0.92)
    air_cost  = max(0.20, _lerp(value, 0, 50000, 0.48, 0.32))
    land_cost = max(0.20, _lerp(distance, 0, 2000, 0.90, 0.28))

    # Time scores: air benefits most from urgency, sea (LCL/FCL) penalised
    _base_lcl_time  = max(0.30, _lerp(distance, 0, 2000, 0.72, 0.50))
    _base_fcl_time  = max(0.35, _lerp(distance, 0, 2000, 0.78, 0.55))
    _base_air_time  = 0.62
    _base_land_time = max(0.30, _lerp(distance, 0, 2000, 0.88, 0.35))

    lcl_time  = max(0.20, _base_lcl_time  - 0.37 * _uf)   # worse under urgency
    fcl_time  = max(0.25, _base_fcl_time  - 0.30 * _uf)   # worse under urgency
    air_time  = min(0.99, _base_air_time  + 0.34 * _uf)   # better under urgency
    land_time = max(0.25, _base_land_time - 0.20 * _uf)   # slightly worse

    lcl_weight  = _lerp(weight, 0, 2000, 0.92, 0.28)
    fcl_weight  = _lerp(weight, 0, 2000, 0.18, 0.95)
    air_weight  = max(0.10, _lerp(weight, 0, 300, 0.95, 0.15))
    land_weight = _lerp(weight, 0, 2000, 0.70, 0.90)               # trucks handle moderate-heavy well

    lcl_risk  = _lerp(value, 0, 20000, 0.82, 0.40)
    fcl_risk  = 0.70
    air_risk  = _lerp(value, 0, 20000, 0.62, 0.92)
    land_risk = max(0.40, _lerp(distance, 0, 2000, 0.72, 0.45))    # road risk grows with distance

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

    weight_label  = f'{weight:.0f}kg'
    value_label   = f'${value:,.0f}'
    dist_label    = f'{distance:.0f}km'

    _is_time_critical = urgency in ('urgent', 'rush')
    _urgency_label    = {'standard': 'standard', 'normal': 'standard',
                         'priority': 'priority', 'urgent': 'urgent', 'rush': 'rush'}.get(urgency, urgency)
    explanations = {
        'lcl': (
            f'LCL is cost-efficient for moderate cargo ({weight_label}, {value_label}). '
            f'{"Not recommended — slower sea transit conflicts with {_urgency_label} urgency." if _is_time_critical else "Suitable transit time for this urgency level."}'
        ),
        'fcl': (
            f'FCL is optimal for heavy or high-value cargo. '
            f'Cargo weight of {weight_label} and value of {value_label} '
            f'{"justify the container cost." if value > 10000 or weight > 500 else "may underutilize a full container."}'
            f'{" Sea transit may be too slow for {_urgency_label} urgency." if _is_time_critical else ""}'
        ),
        'air': (
            f'{"🚨 Rush — Air Freight only viable option for immediate delivery. " if urgency == "rush" else ""}'
            f'{"⚡ Air Freight recommended — urgency requires fastest transit. " if urgency == "urgent" else ""}'
            f'{"⏩ Air Freight ideal for priority delivery at " + value_label + "." if urgency == "priority" else ""}'
            f'{"Air Freight offers best security and tracking for high-value goods at " + value_label + "." if value > 10000 and not _is_time_critical else ""}'
            f'{"Air Freight is competitive for this shipment profile." if not _is_time_critical and value <= 10000 else ""}'
        ),
        'land': (
            f'{"🚛 Land Freight is the fastest option for this nearby route (" + dist_label + ")." if distance <= 500 else "Land Freight is cost-effective for this route (" + dist_label + ")."} '
            f'Cargo weight of {weight_label} is well-suited for road transport. '
            f'{"Short-haul land routes can accommodate {_urgency_label} urgency." if _is_time_critical and distance <= 500 else "Suitable for this urgency level." if not _is_time_critical else "Road transit may be too slow for time-critical urgency on this route."}'
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
