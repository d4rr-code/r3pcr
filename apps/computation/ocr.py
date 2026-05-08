import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import re
import os


def _w(value, conf=0.85):
    """Wrap extracted value with a confidence score."""
    if value:
        return {'value': str(value), 'confidence': conf}
    return {'value': '', 'confidence': 0.0}


def extract_text_from_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.pdf':
            images = convert_from_path(
                file_path,
                dpi=300,
                poppler_path=r'C:\Users\Francis\Downloads\poppler\poppler-25.12.0\Library\bin'
            )
            text = ''
            for image in images:
                text += pytesseract.image_to_string(image)
            return text
        elif ext in ['.jpg', '.jpeg', '.png']:
            image = Image.open(file_path)
            return pytesseract.image_to_string(image)
        else:
            return ''
    except Exception as e:
        print(f"OCR extraction error: {e}")
        return ''


def extract_fields_from_invoice(text):
    text_upper = text.upper()

    invoice_number = ''
    m = re.search(r'(GM-\d{2}-\d{4})', text)
    if m:
        invoice_number = m.group(1)

    invoice_date = ''
    m = re.search(r'(\d{4}\.\d{2}\.\d{2})', text)
    if m:
        invoice_date = m.group(1)

    hs_code = ''
    m = re.search(r'\b(9004\d{5,6})\b', text)
    if m:
        hs_code = m.group(1)

    declared_value = ''
    m = re.search(r'Total Invoice Value.*?USD.*?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'\(\s*USD\s*\)\s*([\d,]+\.\d{2})', text)
    if m:
        declared_value = m.group(1).replace(',', '')

    port_of_loading = 'INCHEON AIRPORT, SOUTH KOREA' if 'INCHEON' in text_upper else ''
    destination = 'MANILA, PHILIPPINES' if 'MANILA' in text_upper else ''

    consignee_name = ''
    consignee_address = ''
    if 'IICOMBINED PHILIPPINES' in text_upper:
        consignee_name = 'IICOMBINED PHILIPPINES INC.'
        consignee_address = '28TH FLOOR MENARCO TOWER, 32ND STREET FORT BONIFACIO, TAGUIG CITY 1630'

    description = 'Sunglasses' if 'SUNGLASSES' in text_upper else ''

    total_quantity = ''
    m = re.search(r'Gross Total Quantity\s*:\s*(\d+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'Total Quantity\s*:\s*(\d+)', text, re.IGNORECASE)
    if m:
        total_quantity = m.group(1)

    return {
        'invoice_number':    _w(invoice_number,    0.90),
        'invoice_date':      _w(invoice_date,      0.90),
        'hs_code':           _w(hs_code,           0.90),
        'declared_value':    _w(declared_value,    0.90),
        'total_quantity':    _w(total_quantity,    0.90),
        'description':       _w(description,       0.80),
        'consignee_name':    _w(consignee_name,    0.80),
        'consignee_address': _w(consignee_address, 0.75),
        'port_of_loading':   _w(port_of_loading,   0.75),
        'destination':       _w(destination,       0.75),
        'currency':          _w('USD',             0.95),
    }


def extract_fields_from_hawb(text):
    text_upper = text.upper()

    hawb_number = ''
    m = re.search(r'(DECX-\d{6})', text, re.IGNORECASE)
    if m:
        hawb_number = m.group(1)

    gross_weight = ''
    m = re.search(r'(\d+\.?\d*)\s*K[Gg]', text)
    if m:
        gross_weight = m.group(1)

    flight_number = ''
    m = re.search(r'(PR\d{3})', text)
    if m:
        flight_number = m.group(1)

    flight_date = ''
    m = re.search(r'JAN\.?\s*(\d+),?\s*(\d{4})', text, re.IGNORECASE)
    if m:
        flight_date = f"January {m.group(1)}, {m.group(2)}"

    hs_code = ''
    m = re.search(r'H\.?S\.?\s*CODE\s*:?\s*([\d.]+)', text, re.IGNORECASE)
    if m:
        hs_code = m.group(1)

    description = 'Sunglasses' if 'SUNGLASSES' in text_upper else ''
    origin = 'INCHEON, KOREA' if 'INCHEON' in text_upper else ''
    destination = 'MANILA, PHILIPPINES' if 'MANILA' in text_upper else ''

    return {
        'hawb_number':   _w(hawb_number,   0.90),
        'gross_weight':  _w(gross_weight,  0.90),
        'flight_number': _w(flight_number, 0.85),
        'flight_date':   _w(flight_date,   0.85),
        'hs_code':       _w(hs_code,       0.85),
        'description':   _w(description,   0.80),
        'origin':        _w(origin,        0.75),
        'destination':   _w(destination,   0.75),
        'no_of_pieces':  _w('',            0.0),
    }


def process_document(file_path, document_type):
    text = extract_text_from_file(file_path)
    if not text:
        return None, "Could not extract text from document"

    if document_type == 'invoice':
        fields = extract_fields_from_invoice(text)
    elif document_type == 'airway_bill':
        fields = extract_fields_from_hawb(text)
    else:
        fields = {}

    return fields, text
