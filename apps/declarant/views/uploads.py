import json
import logging
import os
import re
import tempfile
from decimal import Decimal, InvalidOperation
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.shipments.models import HSCode, Shipment, ShipmentDocument, StatusLog
from apps.shipments.fan import FAN_ASSESSMENT_FIELDS
from apps.notifications.utils import create_notification, send_assessed_email, send_billed_email
from apps.computation.ocr import process_document
from apps.computation.models import ShipmentLineItem

logger = logging.getLogger('r3pcr.declarant')

from .common import *  # noqa: F401,F403

def _process_fan_document_ocr(fan_doc):
    """Run FAN OCR from local or remote storage-backed files."""
    temp_path = None
    try:
        try:
            source_path = fan_doc.file.path
        except NotImplementedError:
            suffix = os.path.splitext(fan_doc.file.name or '')[1] or '.bin'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                for chunk in fan_doc.file.chunks():
                    tmp.write(chunk)
                temp_path = tmp.name
            source_path = temp_path

        fields, raw_text, quality = process_document(source_path, 'sad')
        fan_doc.ocr_text = raw_text
        fan_doc.ocr_fields_json = json.dumps(fields)
        fan_doc.ocr_quality = quality
        fan_doc.ocr_ran_at = timezone.now()
        fan_doc.save(update_fields=['ocr_text', 'ocr_fields_json', 'ocr_quality', 'ocr_ran_at'])
        return True
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


@login_required
@declarant_required
def upload_sad(request, shipment_id):
    """Declarant uploads the FAN Document and advances ongoing shipments to assessed."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status not in ('ongoing', 'assessed', 'paid', 'released', 'billed'):
        messages.error(request, 'FAN Document can only be uploaded once shipment is ongoing or assessed.')
        return redirect('declarant:process', shipment_id=shipment_id)

    file = request.FILES.get('sad_file')
    if not file:
        messages.error(request, 'Please select a file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    old_status = shipment.status

    # Replace any existing FAN document
    shipment.documents.filter(document_type='sad').delete()
    fan_doc = ShipmentDocument.objects.create(
        shipment=shipment,
        document_type='sad',
        file=file,
    )
    ocr_ok = False
    try:
        ocr_ok = _process_fan_document_ocr(fan_doc)
    except Exception as exc:
        logger.warning('FAN OCR failed for shipment %s: %s', shipment.id, exc)

    if old_status == 'ongoing':
        shipment.status = 'assessed'
        shipment.save(update_fields=['status', 'updated_at'])
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='assessed',
            notes='FAN Document uploaded by declarant.',
        )
        send_assessed_email(shipment)

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'FAN Document Available — {shipment.hawb_number}',
        message=(
            'The FAN Document has been uploaded by your declarant. '
            'Please check your shipment details for the official BOC assessment amount.'
        ),
    )

    if old_status == 'ongoing':
        messages.success(request, 'FAN Document uploaded. Shipment status updated to assessed and the consignee has been notified.')
    else:
        messages.success(request, 'FAN Document uploaded. The consignee has been notified.')
    if not ocr_ok:
        messages.warning(request, 'FAN OCR could not prefill the assessment breakdown. Please encode the values manually from the uploaded document.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def save_fan_assessment(request, shipment_id):
    """Declarant verifies/overrides the OCR assessment breakdown from FAN."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    fan_doc = shipment.documents.filter(document_type='sad').first()
    if not fan_doc:
        messages.error(request, 'Upload the FAN Document before saving an assessment breakdown.')
        return redirect('declarant:process', shipment_id=shipment_id)

    try:
        data = json.loads(fan_doc.ocr_fields_json or '{}')
    except Exception:
        data = {}

    for key, _label in FAN_ASSESSMENT_FIELDS:
        data[key] = {
            'value': _fan_amount(request.POST.get(key)),
            'confidence': 1.0,
            'verified': True,
        }

    data['_verified_by'] = request.user.get_full_name() or request.user.username
    data['_verified_at'] = timezone.now().isoformat()
    fan_doc.ocr_fields_json = json.dumps(data)
    fan_doc.save(update_fields=['ocr_fields_json'])

    messages.success(request, 'FAN assessment breakdown saved.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def upload_supporting_document(request, shipment_id, stage):
    """Upload post-assessment documents and advance the shipment when appropriate."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    stages = {
        'payment': {
            'allowed': {'assessed', 'paid', 'released', 'billed'},
            'doc_type': 'payment_proof',
            'next_status': 'paid',
            'from_status': 'assessed',
            'title': 'BOC / eTrade Payment Receipt Available',
            'label': 'BOC / eTrade payment receipt',
            'message': 'The official BOC / eTrade payment receipt has been uploaded for your shipment.',
        },
        'release': {
            'allowed': {'paid', 'released', 'billed'},
            'doc_type': 'release_doc',
            'next_status': 'released',
            'from_status': 'paid',
            'title': 'Release Documents Available',
            'label': 'release / delivery document',
            'message': 'Release or delivery documents have been uploaded for your shipment.',
        },
        'billing': {
            'allowed': {'released', 'billed'},
            'doc_type': 'billing_doc',
            'next_status': 'billed',
            'from_status': 'released',
            'title': 'Final Billing Documents Available',
            'label': 'final billing document',
            'message': 'Final billing or completion documents have been uploaded for your shipment.',
        },
    }
    config = stages.get(stage)
    if not config:
        messages.error(request, 'Invalid document upload stage.')
        return redirect('declarant:process', shipment_id=shipment_id)

    if shipment.status not in config['allowed']:
        messages.error(request, f'{config["label"].title()} uploads are not available for the current shipment status.')
        return redirect('declarant:process', shipment_id=shipment_id)

    files = request.FILES.getlist('support_files') or request.FILES.getlist('receipt_files')
    if not files:
        messages.error(request, 'Please select at least one file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    for f in files:
        ShipmentDocument.objects.create(
            shipment=shipment,
            document_type=config['doc_type'],
            file=f,
        )

    old_status = shipment.status
    new_status = config['next_status']
    if old_status == config['from_status']:
        shipment.status = new_status
        shipment.save(update_fields=['status', 'updated_at'])
        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status=new_status,
            notes=f'{config["label"].title()} uploaded by declarant.',
        )
        if new_status == 'billed':
            send_billed_email(shipment)

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'{config["title"]} - {shipment.hawb_number}',
        message=(
            f'{config["message"]} '
            'Please review your shipment details to view the uploaded documents.'
        ),
    )

    messages.success(request, f'{len(files)} {config["label"]}(s) uploaded. The consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


@login_required
@declarant_required
def upload_receipt(request, shipment_id):
    """Declarant uploads billing receipts / payment proof when billing a shipment."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    if shipment.status != 'billed':
        messages.error(request, 'Billing receipts can only be uploaded once the shipment is billed.')
        return redirect('declarant:process', shipment_id=shipment_id)

    files = request.FILES.getlist('receipt_files')
    if not files:
        messages.error(request, 'Please select at least one file to upload.')
        return redirect('declarant:process', shipment_id=shipment_id)

    for f in files:
        ShipmentDocument.objects.create(
            shipment=shipment,
            document_type='receipt',
            file=f,
        )

    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'Billing Documents Available — {shipment.hawb_number}',
        message=(
            'Your declarant has uploaded billing receipts for your shipment. '
            'Please review your shipment details to view and confirm the billing documents.'
        ),
    )

    messages.success(request, f'{len(files)} billing receipt(s) uploaded. The consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Flag Document Deficiency ────────────────────────────────────────────────

@login_required
@declarant_required
def flag_deficiency(request, shipment_id):
    """Flag a document deficiency and notify the consignee."""
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    deficiency_type  = request.POST.get('deficiency_type', '').strip()
    deficiency_notes = request.POST.get('deficiency_notes', '').strip()

    if not deficiency_type:
        messages.error(request, 'Please select a deficiency type.')
        return redirect('declarant:process', shipment_id=shipment_id)

    type_labels = {
        'missing_invoice': 'Missing Commercial Invoice',
        'missing_packing': 'Missing Packing List',
        'missing_awb':     'Missing Airway Bill / Bill of Lading',
        'incorrect_values':'Incorrect Declared Values',
        'illegible_doc':   'Illegible / Poor Quality Document',
        'missing_other':   'Missing Supporting Document',
        'other':           'Document Deficiency',
    }
    type_label = type_labels.get(deficiency_type, deficiency_type)
    note_text  = f'{type_label}. {deficiency_notes}'.strip('. ') if deficiency_notes else type_label

    # Save deficiency flag to shipment
    shipment.has_deficiency    = True
    shipment.deficiency_type   = deficiency_type
    shipment.deficiency_notes  = note_text
    shipment.deficiency_flagged_at = timezone.now()
    shipment.save(update_fields=['has_deficiency', 'deficiency_type', 'deficiency_notes', 'deficiency_flagged_at'])

    # Audit trail — same status, just record the flag
    StatusLog.objects.create(
        shipment=shipment,
        changed_by=request.user,
        old_status=shipment.status,
        new_status=shipment.status,
        notes=f'Deficiency flagged: {note_text}',
    )

    # Notify the consignee
    create_notification(
        recipient=shipment.consignee,
        shipment=shipment,
        notification_type='status_update',
        title=f'Document Deficiency — {shipment.hawb_number}',
        message=f'A deficiency has been flagged on your shipment: {note_text}. Please resubmit the corrected documents.',
    )

    messages.success(request, 'Deficiency flagged — consignee has been notified.')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Save Selected HS Codes → ECDT Guide ─────────────────────────────────────

@login_required
@declarant_required
def save_ocr_items(request, shipment_id):
    """
    Accept the declarant's verified OCR item rows and selected HS code IDs.
    Confirmed OCR rows are staged for the ECDT workspace, while selected HS
    codes remain available as a guide panel.
    """
    if request.method != 'POST':
        return redirect('declarant:process', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if shipment.declarant != request.user:
        messages.error(request, 'You are not assigned to this shipment.')
        return redirect('declarant:queue')

    hs_code_ids = [
        _id for _id in request.POST.getlist('hs_code_id[]')
        if _id and str(_id).strip().isdigit()
    ]

    guide_codes = []
    for hs_id in hs_code_ids:
        try:
            hs = HSCode.objects.get(id=int(hs_id), is_active=True)
            guide_codes.append({
                'id':          hs.id,
                'code':        hs.code,
                'description': hs.description,
                'duty_rate':   float(hs.duty_rate),
            })
        except HSCode.DoesNotExist:
            pass

    def _decimal(value):
        cleaned = re.sub(r'[^\d.\-]', '', str(value or ''))
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None

    def _integer(value):
        dec = _decimal(value)
        if dec is None:
            return None
        return int(dec)

    def _confidence(value):
        dec = _decimal(value)
        if dec is None:
            return Decimal('0.0')
        if dec > 1:
            dec = dec / Decimal('100')
        return max(Decimal('0.0'), min(dec, Decimal('1.0')))

    def _hs_from_post(hs_id, doc_code):
        if hs_id and str(hs_id).strip().isdigit():
            hs = HSCode.objects.filter(id=int(hs_id), is_active=True).first()
            if hs:
                return hs
        normalized = re.sub(r'\D', '', str(doc_code or ''))
        if not normalized:
            return None
        for hs in HSCode.objects.filter(is_active=True).only('id', 'code', 'duty_rate'):
            if re.sub(r'\D', '', hs.code or '') == normalized:
                return hs
        return None

    descriptions = request.POST.getlist('description[]')
    if descriptions:
        values = request.POST.getlist('total_value[]')
        quantities = request.POST.getlist('quantity[]')
        units = request.POST.getlist('unit[]')
        gross_weights = request.POST.getlist('gross_weight[]')
        net_weights = request.POST.getlist('net_weight[]')
        packages = request.POST.getlist('packages[]')
        confidences = request.POST.getlist('confidence[]')
        item_hs_ids = request.POST.getlist('item_hs_code_id[]')
        doc_hs_codes = request.POST.getlist('doc_hs_code[]')

        ShipmentLineItem.objects.filter(shipment=shipment, source='ocr').delete()
        saved_rows = 0
        for idx, description in enumerate(descriptions):
            description = (description or '').strip()
            if not description:
                continue
            hs = _hs_from_post(
                item_hs_ids[idx] if idx < len(item_hs_ids) else '',
                doc_hs_codes[idx] if idx < len(doc_hs_codes) else '',
            )
            ShipmentLineItem.objects.create(
                shipment=shipment,
                description=description,
                quantity=_decimal(quantities[idx] if idx < len(quantities) else ''),
                unit=(units[idx] if idx < len(units) else '').strip()[:30],
                total_val_usd=_decimal(values[idx] if idx < len(values) else ''),
                hs_code=hs,
                duty_rate=hs.duty_rate if hs else None,
                gross_weight=_decimal(gross_weights[idx] if idx < len(gross_weights) else ''),
                net_weight=_decimal(net_weights[idx] if idx < len(net_weights) else ''),
                packages=_integer(packages[idx] if idx < len(packages) else ''),
                confidence=_confidence(confidences[idx] if idx < len(confidences) else ''),
                is_confirmed=True,
                source='ocr',
                row_order=idx + 1,
            )
            saved_rows += 1

        if saved_rows:
            messages.success(request, f'Saved {saved_rows} verified OCR item row(s) for the ECDT workspace.')

    # Store in session — compute_shipment reads these to show the guide panel
    request.session['guide_hs_codes']    = guide_codes
    request.session['guide_shipment_id'] = str(shipment_id)
    request.session.modified = True

    from django.http import HttpResponseRedirect
    from django.urls import reverse
    return HttpResponseRedirect(
        reverse('computation:compute', kwargs={'shipment_id': shipment_id})
    )




