import logging
import os
import tempfile
import threading

from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment, ShipmentDocument
from ..ocr import process_document

logger = logging.getLogger('r3pcr.computation')

from .ecdt import _store_document_ocr

_OCR_FIELD_PRIORITY = {
    'declared_value':    ['invoice', 'packing_list'],
    'description':       ['invoice', 'packing_list', 'airway_bill'],
    'total_quantity':    ['packing_list', 'invoice'],
    'gross_weight':      ['airway_bill', 'packing_list'],
    'volume_cbm':        ['airway_bill', 'packing_list'],
    'dimensions':        ['packing_list', 'airway_bill'],
    'hawb_number':       ['airway_bill'],
    'invoice_number':    ['invoice'],
    'invoice_date':      ['invoice'],
    'shipper_name':      ['invoice', 'airway_bill'],
    'country_of_origin': ['invoice'],
    'hs_code':           ['invoice', 'airway_bill'],
    'flight_number':     ['airway_bill'],
    'flight_date':       ['airway_bill'],
    'port_loading':      ['airway_bill'],
    'port_discharge':    ['airway_bill'],
    'port_origin':       ['airway_bill'],
    'port_destination':  ['airway_bill'],
    'origin':            ['airway_bill', 'invoice'],
    'destination':       ['airway_bill', 'invoice'],
    'consignee_name':    ['invoice'],
    'consignee_address': ['invoice'],
    'currency':          ['invoice'],
    'net_weight':        ['packing_list'],
    'num_packages':      ['packing_list'],
    'total_gross_weight':['airway_bill', 'packing_list'],
    'number_of_pieces':  ['airway_bill', 'packing_list'],
    'bol_number':        ['airway_bill'],
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
            logger.debug('OCR starting: %s | type=%s', doc.file.name, doc.document_type)
            fields, raw_text, quality = process_document(tmp_path, doc.document_type)
            _store_document_ocr(doc, fields, raw_text, quality)
            logger.debug('OCR raw text length: %s chars', len(raw_text) if raw_text else 0)
            logger.debug('OCR fields returned: %s', list(fields.keys()) if fields else None)

            if fields:
                line_items = fields.pop('__items__', [])
                request.session['ocr_fields']      = fields
                request.session['ocr_items']       = line_items
                request.session['ocr_shipment_id'] = shipment_id
                found    = sum(1 for v in fields.values() if isinstance(v, dict) and v.get('value'))
                item_msg = f', {len(line_items)} line items detected' if line_items else ''
                request.session['ocr_toast'] = ('success', f'OCR complete — {found} fields extracted{item_msg}.')
                logger.info('OCR success: %s fields, %s items', found, len(line_items))
            else:
                request.session['ocr_toast'] = ('warning', 'OCR ran but found no structured fields. Fill in manually.')
                logger.info('OCR found no fields. Raw text snippet: %s',
                            repr(raw_text[:200]) if raw_text else 'EMPTY')
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.exception('OCR failed for doc %s: %s', getattr(doc, 'id', '?'), e)
        request.session['ocr_toast'] = ('error', f'OCR failed: {e}')
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── OCR Extract All (single button — merges all documents) ──────────────────

@login_required
def ocr_extract_all(request, shipment_id):
    """Run OCR on every invoice/airway_bill/packing_list document at once.
    Starts in a background thread and redirects immediately so the page
    doesn't block. The process page auto-refreshes until results appear."""
    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    documents = list(shipment.documents.filter(
        document_type__in=['invoice', 'airway_bill', 'packing_list']
    ))
    if not documents:
        request.session['ocr_toast'] = ('warning', 'No supported documents uploaded yet.')
        return redirect('declarant:process', shipment_id=shipment_id)

    def _run_all(docs):
        for doc in docs:
            doc_type = doc.document_type
            try:
                ext = os.path.splitext(doc.file.name)[1] or '.pdf'
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    doc.file.open('rb')
                    tmp.write(doc.file.read())
                    doc.file.close()
                    tmp_path = tmp.name
                try:
                    logger.debug('OCR-all processing %s: %s', doc_type, doc.file.name)
                    fields, raw_text, quality = process_document(tmp_path, doc_type)
                    _store_document_ocr(doc, fields, raw_text, quality)
                    found = sum(1 for v in (fields or {}).values()
                                if isinstance(v, dict) and v.get('value'))
                    logger.debug('OCR-all %s: quality=%s, %s fields, %s chars',
                                 doc_type, quality, found, len(raw_text or ''))
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            except Exception as e:
                logger.exception('OCR-all failed on %s: %s', doc_type, e)

    t = threading.Thread(target=_run_all, args=(documents,), daemon=True)
    t.start()

    request.session['ocr_toast'] = (
        'info',
        f'Scanning {len(documents)} document{"s" if len(documents) != 1 else ""}… '
        'Results will appear automatically in a few seconds.'
    )
    return redirect('declarant:process', shipment_id=shipment_id)


# ─── Auto-save (Draft) Endpoints ─────────────────────────────────────────────

