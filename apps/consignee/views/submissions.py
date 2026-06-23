import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
from apps.supervisor.models import SystemConfig
from apps.computation.wmcda import wmcda_weight_rows
from apps.notifications.utils import create_notification, notify_incoming_shipment, notify_shipment_status_change
from ..models import Feedback

logger = logging.getLogger('r3pcr.consignee')
from .common import consignee_required, generate_hawb

@login_required
@consignee_required
def submit_shipment(request):
    if request.method == 'POST':
        import_type   = request.POST.get('import_type')
        urgency       = request.POST.get('urgency')
        shipment_type = request.POST.get('shipment_type', '').strip()
        description   = request.POST.get('description', '').strip()

        # Optional shipment values — consignee-provided, unverified
        def _decimal_or_none(key):
            v = request.POST.get(key, '').strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None

        declared_value   = _decimal_or_none('declared_value')
        freight_cost     = _decimal_or_none('freight_cost')
        insurance_cost   = _decimal_or_none('insurance_cost')
        quantity         = _decimal_or_none('quantity')
        estimated_arrival_raw = request.POST.get('estimated_arrival_date', '').strip()
        estimated_arrival_date = parse_date(estimated_arrival_raw) if estimated_arrival_raw else None
        invoice_currency = (request.POST.get('invoice_currency', 'USD') or 'USD').strip().upper()
        # Validate against allowed currencies
        _allowed = {'USD', 'EUR', 'JPY', 'HKD', 'CNY', 'GBP', 'SGD'}
        if invoice_currency not in _allowed:
            invoice_currency = 'USD'

        hawb_number = generate_hawb()

        shipment = Shipment.objects.create(
            hawb_number=hawb_number,
            consignee=request.user,
            import_type=import_type,
            urgency=urgency,
            shipment_type=shipment_type or None,
            description=description,
            status='incoming',
            declared_value=declared_value,
            freight_cost=freight_cost,
            insurance_cost=insurance_cost,
            quantity=quantity,
            estimated_arrival_date=estimated_arrival_date,
            invoice_currency=invoice_currency,
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
            f'{hawb_number}.'
        )
        return redirect('consignee:my_submissions')

    from apps.supervisor.models import SystemConfig
    from django.templatetags.static import static
    invoice_template_url = (
        SystemConfig.get('invoice_template_url', '')
        or static('templates/RTripleJ_Commercial_Invoice.xlsx')
    )
    packing_list_template_url = (
        SystemConfig.get('packing_list_template_url', '')
        or static('templates/RTripleJ_Packing_List.xlsx')
    )
    return render(request, 'consignee/submit.html', {
        'invoice_template_url':      invoice_template_url,
        'packing_list_template_url': packing_list_template_url,
    })


@login_required
@consignee_required
def edit_submission(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)
    if shipment.status != 'incoming':
        messages.error(request, 'This shipment can no longer be edited.')
        return redirect('consignee:shipment_detail', shipment_id=shipment.id)

    if request.method == 'POST':
        shipment.import_type = request.POST.get('import_type') or shipment.import_type
        shipment.urgency = request.POST.get('urgency') or shipment.urgency
        shipment.shipment_type = (request.POST.get('shipment_type', '').strip() or None)
        shipment.description = request.POST.get('description', '').strip()
        invoice_currency = (request.POST.get('invoice_currency', shipment.invoice_currency) or 'USD').strip().upper()
        if invoice_currency in {'USD', 'EUR', 'JPY', 'HKD', 'CNY', 'GBP', 'SGD'}:
            shipment.invoice_currency = invoice_currency
        shipment.save(update_fields=[
            'import_type', 'urgency', 'shipment_type', 'description',
            'invoice_currency', 'updated_at',
        ])

        for doc_type in ['invoice', 'packing_list', 'airway_bill']:
            file = request.FILES.get(doc_type)
            if file:
                shipment.documents.filter(document_type=doc_type).delete()
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type=doc_type,
                    file=file,
                )

        for file in request.FILES.getlist('other_docs'):
            ShipmentDocument.objects.create(
                shipment=shipment,
                document_type='other',
                file=file,
            )

        messages.success(request, f'Shipment {shipment.hawb_number} updated.')
        return redirect('consignee:my_submissions')

    from django.templatetags.static import static
    invoice_template_url = (
        SystemConfig.get('invoice_template_url', '')
        or static('templates/RTripleJ_Commercial_Invoice.xlsx')
    )
    packing_list_template_url = (
        SystemConfig.get('packing_list_template_url', '')
        or static('templates/RTripleJ_Packing_List.xlsx')
    )
    return render(request, 'consignee/submit.html', {
        'is_edit': True,
        'shipment': shipment,
        'invoice_template_url': invoice_template_url,
        'packing_list_template_url': packing_list_template_url,
    })


# ─── My Submissions ───────────────────────────────────────────────────────────

@login_required
@consignee_required
def my_submissions(request):
    shipments = Shipment.objects.filter(consignee=request.user)

    status_filter = request.GET.get('status', '').strip()
    active_status_filter = request.GET.get('active_status', '').strip()
    active_import_type_filter = request.GET.get('active_import_type', '').strip()
    active_urgency_filter = request.GET.get('active_urgency', '').strip()
    active_shipment_type_filter = request.GET.get('active_shipment_type', '').strip()
    flagged_status_filter = request.GET.get('flagged_status', '').strip()
    flagged_import_type_filter = request.GET.get('flagged_import_type', '').strip()
    flagged_urgency_filter = request.GET.get('flagged_urgency', '').strip()
    flagged_shipment_type_filter = request.GET.get('flagged_shipment_type', '').strip()
    q             = request.GET.get('q', '').strip()
    date_from     = request.GET.get('date_from', '').strip()
    date_to       = request.GET.get('date_to', '').strip()
    sort          = request.GET.get('sort', '').strip()

    valid_statuses = {key for key, _label in Shipment.STATUS_CHOICES}
    valid_urgencies = {key for key, _label in Shipment.URGENCY_CHOICES}
    valid_shipment_types = {key for key, _label in Shipment.SHIPMENT_TYPE_CHOICES}

    if status_filter in valid_statuses:
        shipments = shipments.filter(status=status_filter)
    else:
        status_filter = ''
    if q:
        shipments = shipments.filter(
            Q(hawb_number__icontains=q)
            | Q(job_order_reference__icontains=q)
            | Q(container_number__icontains=q)
        )
    if date_from:
        shipments = shipments.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments = shipments.filter(submitted_at__date__lte=date_to)
    if sort == 'ref_asc':
        shipments = shipments.order_by('hawb_number')
    elif sort == 'ref_desc':
        shipments = shipments.order_by('-hawb_number')
    else:
        shipments = shipments.order_by('-submitted_at')

    shipments_list = list(shipments)

    flagged_shipments = [s for s in shipments_list if s.has_deficiency or s.status == 'for_revision']
    flagged_ids = {s.id for s in flagged_shipments}
    active_shipments = [s for s in shipments_list if s.id not in flagged_ids]

    if active_status_filter:
        active_shipments = [s for s in active_shipments if s.status == active_status_filter]
    if active_import_type_filter:
        active_shipments = [s for s in active_shipments if s.import_type == active_import_type_filter]
    if active_urgency_filter:
        active_shipments = [s for s in active_shipments if s.urgency == active_urgency_filter]
    if active_shipment_type_filter:
        active_shipments = [s for s in active_shipments if s.shipment_type == active_shipment_type_filter]

    if flagged_status_filter:
        flagged_shipments = [s for s in flagged_shipments if s.status == flagged_status_filter]
    if flagged_import_type_filter:
        flagged_shipments = [s for s in flagged_shipments if s.import_type == flagged_import_type_filter]
    if flagged_urgency_filter:
        flagged_shipments = [s for s in flagged_shipments if s.urgency == flagged_urgency_filter]
    if flagged_shipment_type_filter:
        flagged_shipments = [s for s in flagged_shipments if s.shipment_type == flagged_shipment_type_filter]
    flagged_paginator = Paginator(flagged_shipments, 10)
    flagged_page_obj = flagged_paginator.get_page(request.GET.get('flagged_page'))
    paginator = Paginator(active_shipments, 10)
    page_obj = paginator.get_page(request.GET.get('active_page'))

    def _page_window(obj, size=5):
        start = ((obj.number - 1) // size) * size + 1
        end = min(start + size - 1, obj.paginator.num_pages)
        return list(range(start, end + 1)), end < obj.paginator.num_pages

    flagged_page_numbers, flagged_has_page_gap = _page_window(flagged_page_obj)
    active_page_numbers, active_has_page_gap = _page_window(page_obj)
    query_params = request.GET.copy()
    active_query = query_params.copy()
    active_query.pop('active_page', None)
    flagged_query = query_params.copy()
    flagged_query.pop('flagged_page', None)
    filter_query = query_params.copy()
    filter_query.pop('sort', None)
    filter_query.pop('active_page', None)
    filter_query.pop('flagged_page', None)

    return render(request, 'consignee/my_submissions.html', {
        'shipments':     page_obj.object_list,
        'flagged_shipments': flagged_page_obj.object_list,
        'flagged_count': len(flagged_shipments),
        'active_count':  len(active_shipments),
        'page_obj':      page_obj,
        'flagged_page_obj': flagged_page_obj,
        'active_page_numbers': active_page_numbers,
        'active_has_page_gap': active_has_page_gap,
        'flagged_page_numbers': flagged_page_numbers,
        'flagged_has_page_gap': flagged_has_page_gap,
        'active_pagination_query': active_query.urlencode(),
        'flagged_pagination_query': flagged_query.urlencode(),
        'total_shipments': len(shipments_list),
        'status_filter': status_filter,
        'active_status_filter': active_status_filter,
        'active_import_type_filter': active_import_type_filter,
        'active_urgency_filter': active_urgency_filter,
        'active_shipment_type_filter': active_shipment_type_filter,
        'flagged_status_filter': flagged_status_filter,
        'flagged_import_type_filter': flagged_import_type_filter,
        'flagged_urgency_filter': flagged_urgency_filter,
        'flagged_shipment_type_filter': flagged_shipment_type_filter,
        'sort':          sort,
        'next_ref_sort': 'ref_desc' if sort == 'ref_asc' else 'ref_asc',
        'filter_query':  filter_query.urlencode(),
        'status_choices': Shipment.STATUS_CHOICES,
        'import_type_choices': Shipment.IMPORT_TYPE_CHOICES,
        'urgency_choices': Shipment.URGENCY_CHOICES,
        'shipment_type_choices': Shipment.SHIPMENT_TYPE_CHOICES,
        'q':             q,
        'date_from':     date_from,
        'date_to':       date_to,
    })


# ─── Shipment Detail ──────────────────────────────────────────────────────────

@login_required
@consignee_required
def shipment_detail(request, shipment_id):
    """Consignee-facing detail page: status, advisory results, computation summary."""
    shipment    = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)
    advisory    = getattr(shipment, 'shipping_advisory', None)
    computation = getattr(shipment, 'computation', None)
    status_logs = shipment.status_logs.order_by('-changed_at')

    # Rebuild full MCDA data from saved advisory
    explanation       = None
    wmcda_scores      = None
    wmcda_breakdown   = None
    recommendation_score = None
    recommendation_breakdown = None
    recommendation_label = None
    declared_score    = None
    declared_breakdown = None
    declared_rating   = None
    declared_label    = shipment.get_shipment_type_display() if shipment.shipment_type else None
    advisory_matches_declared = None
    advisory_summary = None

    mode_labels = {
        'air': 'Air Freight',
        'lcl': 'LCL - Less Container Load',
        'fcl': 'FCL - Full Container Load',
    }

    def _rating(score):
        if score is None:
            return None
        if score >= 0.80:
            return 'Excellent'
        if score >= 0.65:
            return 'Good'
        if score >= 0.50:
            return 'Fair'
        return 'Poor'

    if advisory:
        try:
            from apps.computation.views import compute_wmcda
            wmcda_scores, computed_recommendation, wmcda_breakdown, explanation = compute_wmcda(
                float(advisory.gross_weight),
                float(advisory.cargo_volume),
                float(advisory.declared_value),
                advisory.urgency_level,
                float(advisory.distance_km),
            )
            recommended_type = advisory.recommended_type or computed_recommendation
            recommendation_label = mode_labels.get(recommended_type, (recommended_type or '').upper())
            if wmcda_scores and recommended_type:
                recommendation_score = wmcda_scores.get(recommended_type)
                if wmcda_breakdown:
                    recommendation_breakdown = wmcda_breakdown.get(recommended_type)
            if wmcda_scores and shipment.shipment_type:
                declared_score = wmcda_scores.get(shipment.shipment_type)
                if wmcda_breakdown:
                    declared_breakdown = wmcda_breakdown.get(shipment.shipment_type)
                declared_rating = _rating(declared_score)
            if recommended_type and shipment.shipment_type:
                advisory_matches_declared = recommended_type == shipment.shipment_type
                if advisory_matches_declared:
                    advisory_summary = (
                        'Your selected shipping type matches the MCDA recommendation for this shipment profile.'
                    )
                else:
                    advisory_summary = (
                        f'For similar future shipments, MCDA suggests {recommendation_label} '
                        f'instead of {declared_label} based on cost, time, cargo size, and distance.'
                    )
        except Exception as e:
            logger.debug('Advisory summary build failed: %s', e)

    sad_document = shipment.documents.filter(document_type='sad').first()
    fan_rows = fan_assessment_rows(sad_document)

    # Current step sublabel for the status description box
    from apps.shipments.status_progress import CONSIGNEE_STATUS_SUBLABELS
    current_sublabel = CONSIGNEE_STATUS_SUBLABELS.get(shipment.status, '')

    context = {
        'shipment':          shipment,
        'advisory':          advisory,
        'computation':       computation,
        'status_logs':       status_logs,
        'explanation':       explanation,
        'wmcda_scores':      wmcda_scores,
        'wmcda_breakdown':   wmcda_breakdown,
        'recommendation_score': recommendation_score,
        'recommendation_breakdown': recommendation_breakdown,
        'recommendation_label': recommendation_label,
        'declared_score':    declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':   declared_rating,
        'declared_label':    declared_label,
        'advisory_matches_declared': advisory_matches_declared,
        'advisory_summary':  advisory_summary,
        'wmcda_weights':     wmcda_weight_rows(SystemConfig.get),
        'wmcda_method':      SystemConfig.get('wmcda_weight_method', 'manual'),
        'wmcda_consistency_ratio': SystemConfig.get('wmcda_ahp_consistency_ratio', ''),
        'status_steps':      build_status_progress(shipment.status, 'consignee'),
        'sad_document':      sad_document,
        'fan_assessment_rows': fan_rows,
        'fan_assessment_has_values': fan_assessment_has_values(fan_rows),
        'current_sublabel':  current_sublabel,
    }
    return render(request, 'consignee/shipment_detail.html', context)


# ─── Upload Payment Receipt ──────────────────────────────────────────────────

@login_required
@consignee_required
def upload_receipt(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        file = request.FILES.get('payment_receipt')
        if not file:
            messages.error(request, 'Please select a file to upload.')
        elif shipment.status not in ('assessed', 'paid', 'released', 'billed'):
            messages.error(request, 'Payment receipt can only be uploaded once your shipment is assessed.')
        else:
            shipment.payment_receipt = file
            shipment.payment_receipt_uploaded_at = timezone.now()
            shipment.save(update_fields=['payment_receipt', 'payment_receipt_uploaded_at', 'updated_at'])

            # Audit trail — record receipt upload in status log
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=request.user,
                old_status=shipment.status,
                new_status=shipment.status,
                notes='Consignee payment receipt uploaded for declarant verification.',
            )

            # Notify declarant
            if shipment.declarant:
                create_notification(
                    recipient=shipment.declarant,
                    shipment=shipment,
                    notification_type='status_update',
                    title=f'Consignee Payment Receipt Uploaded - {shipment.hawb_number}',
                    message=(
                        f'{request.user.get_full_name() or request.user.username} uploaded a payment receipt '
                        'for your verification. This does not mark the shipment paid until you confirm it in BOC/eTrade.'
                    ),
                )
            messages.success(request, 'Payment receipt uploaded successfully. Your declarant has been notified for verification.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


# ─── Feedback ─────────────────────────────────────────────────────────────────

@login_required
@consignee_required
def submit_feedback(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    # Only allow feedback once shipment is fully billed
    if shipment.status != 'billed':
        messages.error(request, 'Feedback can only be submitted once your shipment is fully processed.')
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


# ─── Approve / Revise / Reject Computation ───────────────────────────────────

@login_required
@consignee_required
def approve_computation(request, shipment_id):
    """Consignee approves the ECDT+MCDA computation, advancing status to approved."""
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
            notify_shipment_status_change(
                shipment=shipment,
                old_status=old_status,
                new_status='approved',
                changed_by=request.user,
            )
            messages.success(request, 'Computation approved. Your shipment will proceed to customs lodgement.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


@login_required
@consignee_required
def revise_computation(request, shipment_id):
    """Consignee requests revision — sends status back to for_revision."""
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        if shipment.status != 'computed':
            messages.error(request, 'This shipment is not awaiting your review.')
        else:
            notes    = request.POST.get('notes', '').strip()
            old_status = shipment.status
            shipment.status = 'for_revision'
            shipment.save()
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=request.user,
                old_status=old_status,
                new_status='for_revision',
                notes=notes or 'Consignee requested revision of the computation.',
            )
            notify_shipment_status_change(
                shipment=shipment,
                old_status=old_status,
                new_status='for_revision',
                changed_by=request.user,
                notes=notes,
            )
            messages.warning(request, 'Revision requested. The declarant will be notified.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


@login_required
@consignee_required
def reject_computation(request, shipment_id):
    """Consignee rejects the computation entirely."""
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        if shipment.status != 'computed':
            messages.error(request, 'This shipment is not awaiting your review.')
        else:
            notes    = request.POST.get('notes', '').strip()
            old_status = shipment.status
            shipment.status = 'rejected'
            shipment.save()
            StatusLog.objects.create(
                shipment=shipment,
                changed_by=request.user,
                old_status=old_status,
                new_status='rejected',
                notes=notes or 'Consignee rejected the computation.',
            )
            notify_shipment_status_change(
                shipment=shipment,
                old_status=old_status,
                new_status='rejected',
                changed_by=request.user,
                notes=notes,
            )
            messages.error(request, 'Computation rejected.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


# ─── Download Computation Results ────────────────────────────────────────────


@login_required
@consignee_required
def cancel_submission(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        if shipment.status != 'incoming':
            messages.error(request, 'Cannot delete - this shipment is already being processed.')
        else:
            shipment.delete()
            messages.success(request, 'Shipment deleted.')
            return redirect('consignee:my_submissions')

    return redirect('consignee:my_submissions')


# ─── Resubmit Documents (after deficiency flag) ───────────────────────────────

@login_required
@consignee_required
def resubmit_documents(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id, consignee=request.user)

    if request.method == 'POST':
        if not shipment.has_deficiency:
            messages.error(request, 'No deficiency flag on this shipment.')
            return redirect('consignee:shipment_detail', shipment_id=shipment_id)

        files_uploaded = 0
        for doc_type in ['invoice', 'packing_list', 'airway_bill']:
            file = request.FILES.get(doc_type)
            if file:
                # Replace existing document of this type if present
                ShipmentDocument.objects.filter(
                    shipment=shipment,
                    document_type=doc_type
                ).delete()
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type=doc_type,
                    file=file,
                )
                files_uploaded += 1

        if not files_uploaded:
            messages.error(request, 'Please upload at least one document.')
            return redirect('consignee:shipment_detail', shipment_id=shipment_id)

        # Clear deficiency flag
        shipment.has_deficiency       = False
        shipment.deficiency_type      = None
        shipment.deficiency_notes     = None
        shipment.deficiency_flagged_at = None
        shipment.save(update_fields=[
            'has_deficiency', 'deficiency_type',
            'deficiency_notes', 'deficiency_flagged_at',
        ])

        # Audit trail
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=shipment.status,
            new_status=shipment.status,
            notes=f'Consignee resubmitted {files_uploaded} document(s) after deficiency flag.',
        )

        # Notify declarant
        if shipment.declarant:
            create_notification(
                recipient=shipment.declarant,
                shipment=shipment,
                notification_type='status_update',
                title=f'Documents Resubmitted — {shipment.hawb_number}',
                message=(
                    f'The consignee has resubmitted {files_uploaded} document(s) '
                    f'for shipment {shipment.hawb_number}. Please review the updated documents.'
                ),
            )

        messages.success(request, f'{files_uploaded} document(s) resubmitted successfully. Your declarant has been notified.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)
