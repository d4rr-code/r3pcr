"""OCR orchestration: route a document to the right field extractor."""
from .engine import extract_text_from_file
from .text_utils import assess_quality
from .fields import (
    extract_fields_from_invoice, extract_fields_from_hawb,
    extract_fields_from_packing_list, extract_fields_from_fan,
)

def process_document(file_path, document_type):
    text = extract_text_from_file(file_path)
    quality = assess_quality(text)
    if not text:
        return {}, "Could not extract text from document", quality

    if document_type == 'invoice':
        fields = extract_fields_from_invoice(text)
    elif document_type in ('airway_bill', 'bill_of_lading'):
        fields = extract_fields_from_hawb(text)
    elif document_type == 'packing_list':
        fields = extract_fields_from_packing_list(text)
    elif document_type == 'sad':
        fields = extract_fields_from_fan(text)
    else:
        fields = {}

    return fields, text, quality
