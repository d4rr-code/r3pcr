from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_incoming_shipment
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
    from django.db.models import Count
    shipments = Shipment.objects.filter(consignee=request.user)
    total = shipments.count()

    status_counts = {
        'incoming':     shipments.filter(status='incoming').count(),
        'arrived':      shipments.filter(status='arrived').count(),
        'computed':     shipments.filter(status='computed').count(),
        'approved':     shipments.filter(status='approved').count(),
        'rejected':     shipments.filter(status='rejected').count(),
        'for_revision': shipments.filter(status='for_revision').count(),
        'lodgement':    shipments.filter(status='lodgement').count(),
        'ongoing':      shipments.filter(status='ongoing').count(),
        'assessed':     shipments.filter(status='assessed').count(),
        'paid':         shipments.filter(status='paid').count(),
        'released':     shipments.filter(status='released').count(),
        'billed':       shipments.filter(status='billed').count(),
    }

    import_breakdown = list(
        shipments.values('import_type')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    import_labels = dict(Shipment.IMPORT_TYPE_CHOICES)
    for item in import_breakdown:
        item['label'] = import_labels.get(item['import_type'], item['import_type'])

    mode_breakdown = list(
        shipments.values('shipment_type')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    mode_labels = dict(Shipment.SHIPMENT_TYPE_CHOICES)
    for item in mode_breakdown:
        item['label'] = mode_labels.get(item['shipment_type'], item['shipment_type'] or 'Not specified')

    urgency_breakdown = list(
        shipments.values('urgency')
                 .annotate(count=Count('id'))
                 .order_by('-count')
    )
    urgency_labels = dict(Shipment.URGENCY_CHOICES)
    for item in urgency_breakdown:
        item['label'] = urgency_labels.get(item['urgency'], item['urgency'] or 'Unknown')

    context = {
        'total': total,
        **status_counts,
        'import_breakdown':   import_breakdown,
        'mode_breakdown':     mode_breakdown,
        'urgency_breakdown':  urgency_breakdown,
        'recent_shipments':   shipments.order_by('-submitted_at'),
    }

    from apps.supervisor.models import Announcement
    recent_announcements = Announcement.objects.filter(is_active=True).order_by('-created_at')[:3]
    context['recent_announcements'] = recent_announcements

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
            status='incoming',
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

        for declarant in []:
            create_notification(
                recipient=declarant,
                shipment=shipment,
                notification_type='submission',
                title=f'New Shipment Ready to Claim — {hawb_number}',
                message=(
                    f'A new shipment ({hawb_number}) is in the incoming queue and '
                    f'available for any declarant to claim and process.'
                ),
            )
        notify_incoming_shipment(shipment)

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
        s.can_cancel     = s.status == 'incoming' and age_seconds <= 3600
        s.cancel_expired = s.status == 'incoming' and age_seconds > 3600

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
        'status_steps':      build_status_progress(shipment.status, 'consignee'),
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
        elif shipment.status != 'paid':
            messages.error(request, 'Payment receipt can only be uploaded when shipment is marked Paid.')
        else:
            shipment.payment_receipt = file
            shipment.payment_receipt_uploaded_at = timezone.now()
            shipment.save()

            # Audit trail — record receipt upload in status log
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=request.user,
                old_status=shipment.status,
                new_status=shipment.status,
                notes='Payment receipt uploaded by consignee.',
            )

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


# ─── Approve Computation ─────────────────────────────────────────────────────

@login_required
def approve_computation(request, shipment_id):
    """Consignee approves the ECDT+WMCDA computation, advancing status to approved."""
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        if shipment.status != 'computed':
            messages.error(request, 'This shipment is not awaiting your approval.')
        else:
            old_status = shipment.status
            shipment.status = 'approved'
            shipment.save()
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=request.user,
                old_status=old_status,
                new_status='approved',
                notes='Consignee approved the computation.',
            )
            messages.success(request, 'Computation approved. Your shipment will proceed to customs lodgement.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


# ─── Download Computation Results ────────────────────────────────────────────

@login_required
def download_computation(request, shipment_id):
    """Download ECDT + WMCDA results as Excel (.xlsx)."""
    import io
    from decimal import Decimal
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from django.http import HttpResponse

    shipment    = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)
    computation = getattr(shipment, 'computation', None)
    advisory    = getattr(shipment, 'shipping_advisory', None)

    wb = openpyxl.Workbook()

    # ── Styles ────────────────────────────────────────────────────────────────
    hdr_fill   = PatternFill('solid', fgColor='0F172A')
    sub_fill   = PatternFill('solid', fgColor='1E293B')
    grn_fill   = PatternFill('solid', fgColor='052E16')
    blu_fill   = PatternFill('solid', fgColor='172554')
    hdr_font   = Font(bold=True, color='22C55E', size=11)
    sub_font   = Font(bold=True, color='94A3B8', size=10)
    val_font   = Font(color='F1F5F9', size=10)
    grn_font   = Font(bold=True, color='22C55E', size=12)
    blu_font   = Font(bold=True, color='3B82F6', size=11)
    title_font = Font(bold=True, color='F1F5F9', size=13)
    thin_side  = Side(style='thin', color='334155')
    thin_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
    center     = Alignment(horizontal='center', vertical='center')
    right_a    = Alignment(horizontal='right',  vertical='center')
    left_a     = Alignment(horizontal='left',   vertical='center')

    def styled(ws, row, col, value, font=None, fill=None, align=None, border=None, num_fmt=None):
        cell = ws.cell(row=row, column=col, value=value)
        if font:   cell.font      = font
        if fill:   cell.fill      = fill
        if align:  cell.alignment = align
        if border: cell.border    = border
        if num_fmt: cell.number_format = num_fmt
        return cell

    # ════════════════════════════════════════════════════════════════════════
    # Sheet 1: ECDT Summary
    # ════════════════════════════════════════════════════════════════════════
    ws = wb.active
    ws.title = 'ECDT Summary'
    ws.sheet_view.showGridLines = False

    # Column widths
    for col, w in enumerate([28, 16, 16, 18, 10, 18, 18, 20], start=1):
        ws.column_dimensions[get_column_letter(col)].width = w

    r = 1
    # Title block
    ws.merge_cells(f'A{r}:H{r}')
    styled(ws, r, 1, 'ECDT — DUTIES AND TAX COMPUTATION REPORT',
           font=Font(bold=True, color='22C55E', size=14), fill=hdr_fill, align=center)
    ws.row_dimensions[r].height = 24
    r += 1
    ws.merge_cells(f'A{r}:H{r}')
    styled(ws, r, 1, f'Shipment:  {shipment.hawb_number}   |   Consignee: {request.user.get_full_name() or request.user.username}',
           font=Font(color='94A3B8', size=10), fill=hdr_fill, align=center)
    r += 1
    if computation:
        ws.merge_cells(f'A{r}:H{r}')
        styled(ws, r, 1,
               f'Computed: {computation.computed_at.strftime("%b %d, %Y %I:%M %p")}   |   '
               f'Exchange Rate: ₱{computation.exchange_rate}',
               font=Font(color='64748B', size=9), fill=hdr_fill, align=center)
    r += 2

    if computation:
        # ── Item Breakdown ────────────────────────────────────────────────
        for col, label in enumerate(
            ['DESCRIPTION', 'QTY / UNIT', 'EXW (USD)', 'HS CODE', 'DUTY %', 'D/V (₱)', 'CUD (₱)'], start=1
        ):
            styled(ws, r, col, label, font=sub_font, fill=sub_fill, align=center, border=thin_border)
        ws.row_dimensions[r].height = 18
        r += 1

        items = computation.get_items()
        for it in items:
            qty_unit = str(it.get('quantity', ''))
            if it.get('unit'):
                qty_unit += f" {it.get('unit')}"
            row_data = [
                it.get('description', ''),
                qty_unit,
                float(it.get('exw', 0) or 0),
                it.get('hs_code', '—') or '—',
                f"{float(it.get('duty_rate', 0) or 0):.2f}%",
                float(it.get('dv_php', 0) or 0),
                float(it.get('cud', 0) or 0),
            ]
            for col, val in enumerate(row_data, start=1):
                cell = ws.cell(row=r, column=col, value=val)
                cell.font   = val_font
                cell.fill   = PatternFill('solid', fgColor='0C1420')
                cell.border = thin_border
                cell.alignment = right_a if col > 2 else left_a
                if col in (3, 6, 7):
                    cell.number_format = '#,##0.00'
            r += 1
        r += 1

        # ── Fee Breakdown ─────────────────────────────────────────────────
        ws.merge_cells(f'A{r}:F{r}')
        styled(ws, r, 1, 'FEE BREAKDOWN', font=sub_font, fill=sub_fill, align=left_a)
        r += 1

        def fee_row(label, amount, font=None, fill=None):
            nonlocal r
            ws.merge_cells(f'A{r}:F{r}')
            styled(ws, r, 1, label,
                   font=font or Font(color='94A3B8', size=10), fill=fill or PatternFill('solid', fgColor='0F172A'),
                   align=left_a)
            cell = ws.cell(row=r, column=7, value=float(amount or 0))
            cell.font   = font or val_font
            cell.fill   = fill or PatternFill('solid', fgColor='0F172A')
            cell.number_format = '₱#,##0.00'
            cell.alignment = right_a
            r += 1

        fee_row('Customs Duty (CUD)',          computation.customs_duty)
        fee_row('VAT (12%)',                   computation.vat_amount)
        fee_row('Import Processing Fee (IPF)', computation.ipf)
        fee_row('Documentary Stamp (CDS)',     130)

        # BOC Total
        ws.merge_cells(f'A{r}:F{r}')
        styled(ws, r, 1, 'BOC Total', font=blu_font, fill=blu_fill, align=left_a)
        cell = ws.cell(row=r, column=7, value=float(computation.boc_payable or 0))
        cell.font = blu_font; cell.fill = blu_fill
        cell.number_format = '₱#,##0.00'; cell.alignment = right_a
        r += 1

        fee_row('Brokerage Fee', computation.brokerage_fee)
        if computation.arrastre:    fee_row('Arrastre',              computation.arrastre)
        if computation.wharfage:    fee_row('Wharfage',              computation.wharfage)
        if computation.bank_charges: fee_row('Bank Charges',         computation.bank_charges)
        if computation.csf_php:     fee_row('Container Service Fee', computation.csf_php)

        # Total Landed Cost
        ws.merge_cells(f'A{r}:F{r}')
        styled(ws, r, 1, 'TOTAL LANDED COST', font=grn_font, fill=grn_fill, align=left_a)
        cell = ws.cell(row=r, column=7, value=float(computation.total_landed_cost or 0))
        cell.font = grn_font; cell.fill = grn_fill
        cell.number_format = '₱#,##0.00'; cell.alignment = right_a
        ws.row_dimensions[r].height = 20
        r += 1
    else:
        ws.merge_cells('A5:H5')
        styled(ws, 5, 1, 'No computation on file for this shipment.',
               font=Font(color='64748B', size=11), align=center)

    # ════════════════════════════════════════════════════════════════════════
    # Sheet 2: WMCDA Advisory
    # ════════════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet('WMCDA Advisory')
    ws2.sheet_view.showGridLines = False
    for col, w in enumerate([30, 20, 20], start=1):
        ws2.column_dimensions[get_column_letter(col)].width = w

    r2 = 1
    ws2.merge_cells(f'A{r2}:C{r2}')
    styled(ws2, r2, 1, 'WMCDA — SHIPPING MODE ADVISORY',
           font=Font(bold=True, color='22C55E', size=14), fill=hdr_fill, align=center)
    ws2.row_dimensions[r2].height = 24
    r2 += 2

    if advisory:
        # Declared mode
        ws2.merge_cells(f'A{r2}:C{r2}')
        styled(ws2, r2, 1, f'Your Declared Mode: {shipment.get_shipment_type_display() if shipment.shipment_type else "—"}',
               font=Font(bold=True, color='F1F5F9', size=11), fill=sub_fill, align=left_a)
        r2 += 1

        # Scores table
        for col, hdr in enumerate(['Mode', 'Score', 'Recommendation'], start=1):
            styled(ws2, r2, col, hdr, font=sub_font, fill=sub_fill, align=center, border=thin_border)
        r2 += 1

        mode_scores = [
            ('Air Freight', advisory.air_score),
            ('LCL — Less Container Load', advisory.lcl_score),
            ('FCL — Full Container Load', advisory.fcl_score),
            ('Land Freight', advisory.land_score),
        ]
        mode_keys = {'Air Freight': 'air', 'LCL — Less Container Load': 'lcl',
                     'FCL — Full Container Load': 'fcl', 'Land Freight': 'land'}

        for mode_label, score in sorted(mode_scores, key=lambda x: (x[1] or 0), reverse=True):
            is_rec  = (mode_keys.get(mode_label) == advisory.recommended_type)
            tag     = '★ RECOMMENDED' if is_rec else ''
            f_color = '22C55E' if is_rec else 'F1F5F9'
            bg      = '052E16' if is_rec else '0F172A'
            row_fill = PatternFill('solid', fgColor=bg)
            row_font = Font(bold=is_rec, color=f_color, size=10)
            for col, val in enumerate([mode_label, f'{float(score or 0):.4f}', tag], start=1):
                cell = ws2.cell(row=r2, column=col, value=val)
                cell.font = row_font; cell.fill = row_fill
                cell.border = thin_border
                cell.alignment = center if col > 1 else left_a
            r2 += 1

        r2 += 1
        # Declarant's recommendation
        if advisory.declarant_recommendation:
            ws2.merge_cells(f'A{r2}:C{r2}')
            styled(ws2, r2, 1,
                   f"Declarant's Recommendation: {advisory.declarant_recommendation.upper()}",
                   font=Font(bold=True, color='93C5FD', size=11), fill=blu_fill, align=left_a)
            r2 += 1
        if advisory.declarant_note:
            ws2.merge_cells(f'A{r2}:C{r2}')
            styled(ws2, r2, 1, f'Note: "{advisory.declarant_note}"',
                   font=Font(italic=True, color='94A3B8', size=10),
                   fill=PatternFill('solid', fgColor='0D2237'), align=left_a)
            ws2.row_dimensions[r2].height = 30
            ws2.cell(row=r2, column=1).alignment = Alignment(
                horizontal='left', vertical='center', wrap_text=True)
    else:
        ws2.merge_cells('A3:C3')
        styled(ws2, 3, 1, 'No WMCDA advisory on file.', font=Font(color='64748B', size=11), align=center)

    # ── Serve file ────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"R3PCR_{shipment.hawb_number}_ECDT_WMCDA.xlsx"
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ─── Cancel Submission ────────────────────────────────────────────────────────

@login_required
def cancel_submission(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        age = timezone.now() - shipment.submitted_at
        if shipment.status != 'incoming':
            messages.error(request, 'Cannot cancel — this shipment is already being processed.')
        elif age.total_seconds() > 3600:
            messages.error(request, 'Cannot cancel — the 1-hour cancellation window has passed.')
        else:
            shipment.delete()
            messages.success(request, 'Shipment cancelled and removed.')
            return redirect('consignee:my_submissions')

    return redirect('consignee:my_submissions')
