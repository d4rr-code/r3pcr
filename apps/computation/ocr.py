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

    # ── Invoice Number ─────────────────────────────────────────────────────────
    invoice_number = ''
    patterns_inv = [
        r'Invoice\s+N[Oo]\.?\s*[:\s]+([A-Z0-9\-]+)',       # Invoice No. 13001044
        r'Invoice\s+Number\s*[:\s]+([A-Z0-9\-]+)',          # Invoice Number: 13001044
        r'INVOICE\s+NO\.?\s*[:\s]+([A-Z0-9\-]+)',           # INVOICE NO. 08XB029HLI
        r'(GM-\d{2}-\d{4})',                                 # GM-XX-XXXX format
        r'Invoice\s+No\.\s+(\d+)',                           # Invoice No. 13001044
    ]
    for pat in patterns_inv:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            invoice_number = m.group(1).strip()
            break

    # ── Invoice Date ───────────────────────────────────────────────────────────
    invoice_date = ''
    patterns_date = [
        r'(\d{4}\.\d{2}\.\d{2})',                            # 2026.01.23
        r'(\d{2}\.\d{2}\.\d{4})',                            # 22.01.2026
        r'Doc\s+Date\s*[:\s]+(\S+)',                         # Doc Date: 23.01.2026
        r'DATE[:\s]+([A-Z][a-z]+\s+\d+,?\s+\d{4})',         # DATE: December 22, 2025
        r'(\d{1,2}/\d{1,2}/\d{4})',                          # 12/22/2025
    ]
    for pat in patterns_date:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            invoice_date = m.group(1).strip()
            break

    # ── HS Code ────────────────────────────────────────────────────────────────
    hs_code = ''
    patterns_hs = [
        r'HS\s+CODE\s*[:\s]+(\d[\d.]+)',                     # HS CODE: 3923.30
        r'H\.S\.?\s*CODE\s*[:\s]+(\d[\d.]+)',                # H.S. CODE: 3923.30
        r'Customs\s+tariff\s+no\.\s*[:\s]+(\d[\d.]+)',       # Customs tariff no.: 84198998
        r'\b(9004\d{5,6})\b',                                 # Sunglasses HS code
    ]
    for pat in patterns_hs:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hs_code = m.group(1).strip()
            break

    # ── Declared Value ─────────────────────────────────────────────────────────
    declared_value = ''
    patterns_val = [
        r'TOTAL[:\s]*USD\s*([\d,]+\.?\d*)',                   # TOTAL: USD36,450.00
        r'USD\s*([\d,]+\.?\d*)\s*$',                          # USD36,450.00 at end of line
        r'Invoice\s+amount\s+EUR\s+([\d,]+[.,]\d{2})',        # Invoice amount EUR 1,123.73
        r'Total\s+Invoice\s+Value.*?USD.*?([\d,]+\.?\d*)',    # Total Invoice Value USD ...
        r'\(\s*USD\s*\)\s*([\d,]+\.\d{2})',                   # ( USD ) 36,450.00
        r'Value\s+of\s+goods\s+in\s+EUR\s+([\d,]+[.,]\d{2})',# Value of goods in EUR 883.73
        r'Net\s+value\s+position\s+([\d,]+[.,]\d{2})',        # Net value position 883.73
        r'TOTAL[:\s]*([\d,]+\.\d{2})',                        # TOTAL: 36,450.00
    ]
    for pat in patterns_val:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            declared_value = m.group(1).replace(',', '').replace(' ', '')
            # Handle European decimal format (1.123,73 -> 1123.73)
            if re.match(r'^\d+\.\d{3},\d{2}$', m.group(1).strip()):
                declared_value = m.group(1).replace('.', '').replace(',', '.')
            break

    # ── Currency ───────────────────────────────────────────────────────────────
    currency = 'USD'
    if 'EUR' in text_upper and 'USD' not in text_upper:
        currency = 'EUR'
    elif 'EUR' in text_upper and 'USD' in text_upper:
        currency = 'USD'  # default to USD if both present

    # ── Total Quantity ─────────────────────────────────────────────────────────
    total_quantity = ''
    patterns_qty = [
        r'TOTAL[:\s]*([\d,]+)\s*PCS',                        # TOTAL: 590,000PCS
        r'Gross\s+Total\s+Quantity\s*[:\s]+(\d+)',           # Gross Total Quantity: 590000
        r'Total\s+Quantity\s*[:\s]+(\d+)',                   # Total Quantity: 590000
        r'(\d+\.?\d*)\s*(?:PC|PCS)\b',                       # 1.00 PC or 590000 PCS
    ]
    for pat in patterns_qty:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            total_quantity = m.group(1).replace(',', '')
            break

    # ── Description ────────────────────────────────────────────────────────────
    description = ''
    desc_keywords = [
        ('SUNGLASSES', 'Sunglasses'),
        ('INCUBATOR', 'Incubator'),
        ('PLASTIC NASAL SPRAY', 'Plastic Nasal Spray Bottle'),
        ('PLASTIC BOTTLE', 'Plastic Bottle'),
        ('NASAL SPRAY', 'Nasal Spray'),
        ('BOTTLE', 'Plastic Bottle'),
    ]
    for keyword, label in desc_keywords:
        if keyword in text_upper:
            description = label
            break

    # ── Consignee ──────────────────────────────────────────────────────────────
    consignee_name = ''
    consignee_address = ''
    # Try to find "TO: <Name>" pattern
    m = re.search(r'TO:\s*M/S\s*\n?([^\n]+)', text, re.IGNORECASE)
    if m:
        consignee_name = m.group(1).strip()
    else:
        m = re.search(r'TO:\s*([^\n]+)', text, re.IGNORECASE)
        if m:
            consignee_name = m.group(1).strip()

    # Known consignees
    if 'IICOMBINED PHILIPPINES' in text_upper:
        consignee_name = 'IICOMBINED PHILIPPINES INC.'
        consignee_address = '28TH FLOOR MENARCO TOWER, 32ND STREET FORT BONIFACIO, TAGUIG CITY 1630'
    elif 'HIZON LABORATORIES' in text_upper:
        consignee_name = 'HIZON LABORATORIES INC'
        consignee_address = '29 HIZON BUILDING, QUEZON AVENUE, LOURDES 1, QUEZON CITY, 1114 PHILIPPINES'
    elif 'ITS SCIENCE' in text_upper:
        consignee_name = 'ITS SCIENCE (PHILS.) INC.'
        consignee_address = 'ORTIGAS CENTER, UNIT 1603-04, 1605 PASIG CITY, PHILIPPINES'

    # ── Port / Destination ─────────────────────────────────────────────────────
    port_of_loading = ''
    if 'INCHEON' in text_upper:
        port_of_loading = 'INCHEON AIRPORT, SOUTH KOREA'
    elif 'CHINA' in text_upper or 'WUXI' in text_upper:
        port_of_loading = 'CHINA'
    elif 'GERMANY' in text_upper or 'SCHWABACH' in text_upper:
        port_of_loading = 'GERMANY'

    destination = ''
    if 'MANILA' in text_upper:
        destination = 'MANILA, PHILIPPINES'
    elif 'PHILIPPINES' in text_upper or 'PASIG' in text_upper or 'QUEZON' in text_upper:
        destination = 'PHILIPPINES'

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
        'currency':          _w(currency,          0.95),
    }


def extract_fields_from_hawb(text):
    text_upper = text.upper()

    # ── HAWB / Consignment Number ──────────────────────────────────────────────
    hawb_number = ''
    patterns_hawb = [
        r'(DECX-\d{6})',                                      # DECX-XXXXXX
        r'Consignment\s+No\.?\s*[:\s]+(\d+)',                 # Consignment No. 238955
        r'HAWB\s*[:\s]+([A-Z0-9\-]+)',                        # HAWB: XXXXXX
        r'AWB\s*(?:No\.?)?\s*[:\s]+([A-Z0-9\-]+)',           # AWB No.: XXXXXX
        r'House\s+Air\s+Waybill\s*[:\s]+([A-Z0-9\-]+)',      # House Air Waybill: XXXXXX
    ]
    for pat in patterns_hawb:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hawb_number = m.group(1).strip()
            break

    # ── Gross Weight ───────────────────────────────────────────────────────────
    gross_weight = ''
    patterns_gw = [
        r'G\.?W\.?\(?KGS?\)?\s*[:\s]*([\d,]+\.?\d*)',        # G.W(KGS): 4063.000
        r'Gross\s*[:\s]*([\d,]+\.?\d*)\s*kg',                 # Gross: 58,900 kg
        r'([\d,]+\.?\d*)\s*KGS?\b',                           # 4063.000KGS
        r'([\d.]+)\s*kg\b',                                    # 58.900 kg
        r'Gross\s+Weight\s*[:\s]*([\d,]+\.?\d*)',             # Gross Weight: 4063
    ]
    for pat in patterns_gw:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            gross_weight = m.group(1).replace(',', '')
            break

    # ── Flight / Vessel Number ─────────────────────────────────────────────────
    flight_number = ''
    patterns_flight = [
        r'(PR\d{3,4})',                                        # PR123
        r'Flight\s*[:\s]+([A-Z]{2}\d{3,4})',                  # Flight: PR123
        r'Vessel\s*[:\s]+([^\n]+)',                            # Vessel: XXX
    ]
    for pat in patterns_flight:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            flight_number = m.group(1).strip()
            break

    # ── Flight / Departure Date ────────────────────────────────────────────────
    flight_date = ''
    patterns_fdate = [
        r'((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s*\d+,?\s*\d{4})',
        r'(\d{4}\.\d{2}\.\d{2})',
        r'(\d{2}/\d{2}/\d{4})',
    ]
    for pat in patterns_fdate:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            flight_date = m.group(1).strip()
            break

    # ── HS Code ────────────────────────────────────────────────────────────────
    hs_code = ''
    patterns_hs = [
        r'HS\s+CODE\s*[:\s]+(\d[\d.]+)',
        r'H\.S\.?\s*CODE\s*[:\s]+(\d[\d.]+)',
        r'Customs\s+tariff\s+no\.\s*[:\s]+(\d[\d.]+)',
    ]
    for pat in patterns_hs:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hs_code = m.group(1).strip()
            break

    # ── Description ────────────────────────────────────────────────────────────
    description = ''
    desc_keywords = [
        ('SUNGLASSES', 'Sunglasses'),
        ('INCUBATOR', 'Incubator'),
        ('PLASTIC NASAL SPRAY', 'Plastic Nasal Spray Bottle'),
        ('PLASTIC BOTTLE', 'Plastic Bottle'),
        ('NASAL SPRAY', 'Nasal Spray'),
        ('BOTTLE', 'Plastic Bottle'),
    ]
    for keyword, label in desc_keywords:
        if keyword in text_upper:
            description = label
            break

    # ── No. of Pieces ──────────────────────────────────────────────────────────
    no_of_pieces = ''
    m = re.search(r'TOTAL[:\s]*([\d,]+)\s*PCS', text, re.IGNORECASE)
    if not m:
        m = re.search(r'([\d,]+)\s*(?:PCS|PIECES|CARTONS?)\b', text, re.IGNORECASE)
    if m:
        no_of_pieces = m.group(1).replace(',', '')

    # ── Origin / Destination ───────────────────────────────────────────────────
    origin = ''
    if 'INCHEON' in text_upper:
        origin = 'INCHEON, KOREA'
    elif 'CHINA' in text_upper or 'WUXI' in text_upper:
        origin = 'CHINA'
    elif 'GERMANY' in text_upper or 'SCHWABACH' in text_upper or 'BÜCHENBACH' in text_upper:
        origin = 'GERMANY'

    destination = ''
    if 'MANILA' in text_upper:
        destination = 'MANILA, PHILIPPINES'
    elif 'PHILIPPINES' in text_upper or 'PASIG' in text_upper or 'QUEZON' in text_upper:
        destination = 'PHILIPPINES'

    return {
        'hawb_number':   _w(hawb_number,   0.90),
        'gross_weight':  _w(gross_weight,  0.90),
        'flight_number': _w(flight_number, 0.85),
        'flight_date':   _w(flight_date,   0.85),
        'hs_code':       _w(hs_code,       0.85),
        'description':   _w(description,   0.80),
        'origin':        _w(origin,        0.75),
        'destination':   _w(destination,   0.75),
        'no_of_pieces':  _w(no_of_pieces,  0.80),
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
