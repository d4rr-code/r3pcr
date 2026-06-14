import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
from apps.notifications.utils import create_notification, notify_incoming_shipment, notify_shipment_status_change
from ..models import Feedback

logger = logging.getLogger('r3pcr.consignee')
from .common import generate_hawb

@login_required
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
            f'<strong>{hawb_number}</strong>.'
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

    flagged_shipments = [s for s in shipments_list if s.has_deficiency]
    active_shipments = [s for s in shipments_list if not s.has_deficiency]
    paginator = Paginator(active_shipments, 6)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    return render(request, 'consignee/my_submissions.html', {
        'shipments':     page_obj.object_list,
        'flagged_shipments': flagged_shipments,
        'active_count':  len(active_shipments),
        'page_obj':      page_obj,
        'pagination_query': query_params.urlencode(),
        'total_shipments': len(shipments_list),
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
                        'Your selected shipping type matches the WMCDA recommendation for this shipment profile.'
                    )
                else:
                    advisory_summary = (
                        f'For similar future shipments, WMCDA suggests {recommendation_label} '
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
        'status_steps':      build_status_progress(shipment.status, 'consignee'),
        'sad_document':      sad_document,
        'fan_assessment_rows': fan_rows,
        'fan_assessment_has_values': fan_assessment_has_values(fan_rows),
        'current_sublabel':  current_sublabel,
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
            notify_shipment_status_change(
                shipment=shipment,
                old_status=old_status,
                new_status='approved',
                changed_by=request.user,
            )
            messages.success(request, 'Computation approved. Your shipment will proceed to customs lodgement.')

    return redirect('consignee:shipment_detail', shipment_id=shipment_id)


@login_required
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


# ─── Resubmit Documents (after deficiency flag) ───────────────────────────────

@login_required
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
