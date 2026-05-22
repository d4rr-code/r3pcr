import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import re
import os
import io
import base64
import requests as http_requests


def _w(value, conf=0.85):
    """Wrap extracted value with a confidence score."""
    if value:
        return {'value': str(value), 'confidence': conf}
    return {'value': '', 'confidence': 0.0}


def _vision_api_call(api_key, image_bytes):
    """Send raw image bytes to Google Vision API. Returns extracted text string."""
    url = f'https://vision.googleapis.com/v1/images:annotate?key={api_key}'
    payload = {
        'requests': [{
            'image': {'content': base64.b64encode(image_bytes).decode('utf-8')},
            'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}],
        }]
    }
    try:
        resp = http_requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data['responses'][0].get('fullTextAnnotation', {}).get('text', '')
        else:
            print(f"Vision API error {resp.status_code}: {resp.text[:300]}")
            return ''
    except Exception as e:
        print(f"Vision API request error: {e}")
        return ''


def extract_text_from_file(file_path):
    """
    Extract text from a document file using Google Vision API (preferred)
    or Tesseract OCR (fallback when GOOGLE_VISION_API_KEY is not set).
    """
    ext = os.path.splitext(file_path)[1].lower()
    api_key = os.getenv('GOOGLE_VISION_API_KEY', '')

    try:
        if ext == '.pdf':
            poppler_path = os.getenv('POPPLER_PATH') or None
            images = convert_from_path(file_path, dpi=200, poppler_path=poppler_path)

            if api_key:
                # ── Google Vision path ──────────────────────────────────────────
                full_text = ''
                for image in images:
                    buf = io.BytesIO()
                    image.save(buf, format='JPEG')
                    full_text += _vision_api_call(api_key, buf.getvalue()) + '\n'
                return full_text
            else:
                # ── Tesseract fallback ─────────────────────────────────────────
                full_text = ''
                for image in images:
                    full_text += pytesseract.image_to_string(image)
                return full_text

        elif ext in ['.jpg', '.jpeg', '.png']:
            if api_key:
                with open(file_path, 'rb') as f:
                    return _vision_api_call(api_key, f.read())
            else:
                image = Image.open(file_path)
                return pytesseract.image_to_string(image)
        else:
            return ''

    except Exception as e:
        print(f"OCR extraction error: {e}")
        return ''


def _extract_line_items(text):
    """
    Extract individual line-item rows from commercial invoice / packing list text.
    Handles real invoice formats where rows may be prefixed with a row number
    (e.g. "1 PRODUCT NAME 100 PCS 29.61 2,368.60").

    Returns a list of dicts: [{description, quantity, total_value}, ...]
    Returns [] if fewer than 2 distinct items are found (fall back to single-total mode).
    """
    SKIP_WORDS = {
        # totals / summaries
        'subtotal', 'sub-total', 'sub total', 'grand total', 'total', 'discount',
        'net value', 'net amount', 'net price', 'value of goods', 'invoice amount',
        'total amount', 'total value', 'credit', 'debit', 'balance', 'position',
        # logistics / charges
        'freight', 'insurance', 'shipping', 'handling', 'tax', 'vat', 'charges',
        'surcharge', 'customs', 'duty', 'fee', 'commission',
        # document / header words
        'invoice', 'description', 'item', 'qty', 'quantity', 'unit', 'price',
        'amount', 'no.', 'number', 'date', 'currency', 'terms', 'payment',
        'bank', 'page', 'consignee', 'shipper', 'marks', 'country of origin',
        'gross weight', 'net weight', 'packing', 'carton', 'certificate',
        'warranty', 'incoterm', 'delivery', 'order', 'contract', 'ref',
    }

    # Match any line that ends with a 2-decimal monetary amount.
    # The lazy (.+?) captures everything before the final amount.
    line_pat = re.compile(r'^(.+?)\s+([\d,]+\.\d{2})\s*$')

    items = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 10:
            continue
        low = line.lower()
        # Skip lines containing summary / header keywords
        if any(w in low for w in SKIP_WORDS):
            continue

        m = line_pat.match(line)
        if not m:
            continue

        desc_raw  = m.group(1).strip()
        amount_str = m.group(2)

        # Must contain at least one real English word (≥3 letters)
        if not re.search(r'[A-Za-z]{3,}', desc_raw):
            continue

        # Strip leading row number — invoices often prefix rows with "1 ", "2 ", etc.
        desc_part = re.sub(r'^\d+\s+', '', desc_raw).strip()
        if len(desc_part) < 4:
            continue
        if not re.search(r'[A-Za-z]{3,}', desc_part):
            continue

        # Skip if description is now a known skip keyword
        if any(w in desc_part.lower() for w in SKIP_WORDS):
            continue

        try:
            amount = float(amount_str.replace(',', ''))
        except (ValueError, TypeError):
            continue

        # Skip very small amounts (likely unit prices, not line totals)
        if amount < 1.00:
            continue

        # Extract quantity from "(NNN PCS)" / "(NNN PIECES)" patterns in description
        qty = ''
        qty_match = re.search(
            r'\((\d+)\s*(?:PCS|PIECES|UNITS?|CTN|CTNS?|SET|SETS|ROLLS?|BOX|BOXES)\)',
            desc_part, re.IGNORECASE
        )
        if qty_match:
            qty = qty_match.group(1)
        else:
            # Try to pick up a standalone number just before the final amount on the line
            qty_inline = re.search(r'\s(\d+)\s+[\d,]+\.\d{2}\s*$', line)
            if qty_inline:
                qty = qty_inline.group(1)

        items.append({
            'description': desc_part[:200],   # cap at 200 chars
            'quantity':    qty,
            'total_value': amount,
        })

    if len(items) < 2:
        return []

    # Drop trailing item if its value ≈ sum of all preceding items (slipped-through subtotal)
    if len(items) >= 2:
        preceding_sum = sum(it['total_value'] for it in items[:-1])
        if preceding_sum > 0 and abs(items[-1]['total_value'] - preceding_sum) / preceding_sum < 0.01:
            items = items[:-1]

    if len(items) < 2:
        return []

    return items


def extract_fields_from_invoice(text):
    text_upper = text.upper()

    # ── Invoice Number ─────────────────────────────────────────────────────────
    invoice_number = ''
    patterns_inv = [
        r'Invoice\s+N[Oo]\.?\s*[:\s]+([A-Z0-9\-]+)',
        r'Invoice\s+Number\s*[:\s]+([A-Z0-9\-]+)',
        r'INVOICE\s+NO\.?\s*[:\s]+([A-Z0-9\-]+)',
        r'(GM-\d{2}-\d{4})',
        r'Invoice\s+No\.\s+(\d+)',
    ]
    for pat in patterns_inv:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            invoice_number = m.group(1).strip()
            break

    # ── Invoice Date ───────────────────────────────────────────────────────────
    invoice_date = ''
    patterns_date = [
        r'(\d{4}\.\d{2}\.\d{2})',
        r'(\d{2}\.\d{2}\.\d{4})',
        r'Doc\s+Date\s*[:\s]+(\S+)',
        r'DATE[:\s]+([A-Z][a-z]+\s+\d+,?\s+\d{4})',
        r'(\d{1,2}/\d{1,2}/\d{4})',
    ]
    for pat in patterns_date:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            invoice_date = m.group(1).strip()
            break

    # ── HS Code ────────────────────────────────────────────────────────────────
    hs_code = ''
    patterns_hs = [
        r'HS\s+CODE\s*[:\s]+(\d[\d.]+)',
        r'H\.S\.?\s*CODE\s*[:\s]+(\d[\d.]+)',
        r'Customs\s+tariff\s+no\.\s*[:\s]+(\d[\d.]+)',
        r'\b(9004\d{5,6})\b',
    ]
    for pat in patterns_hs:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hs_code = m.group(1).strip()
            break

    # ── Declared Value (grand total fallback) ──────────────────────────────────
    declared_value = ''
    patterns_val = [
        r'TOTAL[:\s]*USD\s*([\d,]+\.?\d*)',
        r'USD\s*([\d,]+\.?\d*)\s*$',
        r'Invoice\s+amount\s+EUR\s+([\d,]+[.,]\d{2})',
        r'Total\s+Invoice\s+Value.*?USD.*?([\d,]+\.?\d*)',
        r'\(\s*USD\s*\)\s*([\d,]+\.\d{2})',
        r'Value\s+of\s+goods\s+in\s+EUR\s+([\d,]+[.,]\d{2})',
        r'Net\s+value\s+position\s+([\d,]+[.,]\d{2})',
        r'TOTAL[:\s]*([\d,]+\.\d{2})',
    ]
    for pat in patterns_val:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            declared_value = m.group(1).replace(',', '').replace(' ', '')
            if re.match(r'^\d+\.\d{3},\d{2}$', m.group(1).strip()):
                declared_value = m.group(1).replace('.', '').replace(',', '.')
            break

    # ── Currency ───────────────────────────────────────────────────────────────
    currency = 'USD'
    if 'EUR' in text_upper and 'USD' not in text_upper:
        currency = 'EUR'
    elif 'EUR' in text_upper and 'USD' in text_upper:
        currency = 'USD'

    # ── Total Quantity ─────────────────────────────────────────────────────────
    total_quantity = ''
    patterns_qty = [
        r'TOTAL[:\s]*([\d,]+)\s*PCS',
        r'Gross\s+Total\s+Quantity\s*[:\s]+(\d+)',
        r'Total\s+Quantity\s*[:\s]+(\d+)',
        r'(\d+\.?\d*)\s*(?:PC|PCS)\b',
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
        ('OVEN', 'Laboratory Oven'),
        ('CHAMBER', 'Climate Chamber'),
        ('CENTRIFUGE', 'Centrifuge'),
        ('MICROSCOPE', 'Microscope'),
        ('PLASTIC NASAL SPRAY', 'Plastic Nasal Spray Bottle'),
        ('PLASTIC BOTTLE', 'Plastic Bottle'),
        ('NASAL SPRAY', 'Nasal Spray'),
        ('BOTTLE', 'Plastic Bottle'),
        ('SPARE PART', 'Spare Parts'),
        ('ACCESSORY', 'Accessories'),
        ('EQUIPMENT', 'Laboratory Equipment'),
        ('INSTRUMENT', 'Scientific Instrument'),
        ('DEVICE', 'Electronic Device'),
        ('MACHINE', 'Machinery'),
        ('CHEMICAL', 'Chemical'),
        ('REAGENT', 'Reagent'),
        ('TEXTILE', 'Textile'),
        ('GARMENT', 'Garment'),
        ('ELECTRONIC', 'Electronic Components'),
        ('FURNITURE', 'Furniture'),
        ('FOOD', 'Food Products'),
    ]
    for keyword, label in desc_keywords:
        if keyword in text_upper:
            description = label
            break

    # Try extracting from invoice line if no keyword matched
    if not description:
        m = re.search(
            r'(?:Description|Commodity|Goods?)\s*[:\s]+([A-Za-z][^\n]{3,60})',
            text, re.IGNORECASE
        )
        if m:
            description = m.group(1).strip()

    # ── Consignee ──────────────────────────────────────────────────────────────
    consignee_name = ''
    consignee_address = ''
    m = re.search(r'TO:\s*M/S\s*\n?([^\n]+)', text, re.IGNORECASE)
    if m:
        consignee_name = m.group(1).strip()
    else:
        m = re.search(r'TO:\s*([^\n]+)', text, re.IGNORECASE)
        if m:
            consignee_name = m.group(1).strip()

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

    fields = {
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

    # ── Multi-item extraction ──────────────────────────────────────────────────
    line_items = _extract_line_items(text)
    if line_items:
        fields['__items__'] = line_items

    return fields


def extract_fields_from_hawb(text):
    text_upper = text.upper()

    # ── HAWB / BOL / Consignment Number ───────────────────────────────────────
    hawb_number = ''
    patterns_hawb = [
        r'(DECX-\d{6})',
        r'Consignment\s+No\.?\s*[:\s]+(\d+)',
        r'HAWB\s*[:\s]+([A-Z0-9\-]+)',
        r'AWB\s*(?:No\.?)?\s*[:\s]+([A-Z0-9\-]+)',
        r'House\s+Air\s+Waybill\s*[:\s]+([A-Z0-9\-]+)',
        r'B/L\s*(?:No\.?)?\s*[:\s]+([A-Z0-9\-]+)',           # B/L No.: XXXXXX
        r'Bill\s+of\s+Lading\s*[:\s]+([A-Z0-9\-]+)',         # Bill of Lading: XXXXXX
        r'Booking\s+(?:Ref\.?|Reference)\s*[:\s]+([A-Z0-9\-]+)',
    ]
    for pat in patterns_hawb:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hawb_number = m.group(1).strip()
            break

    # ── Gross Weight ───────────────────────────────────────────────────────────
    gross_weight = ''
    patterns_gw = [
        r'G\.?W\.?\(?KGS?\)?\s*[:\s]*([\d,]+\.?\d*)',
        r'Gross\s*[:\s]*([\d,]+\.?\d*)\s*kg',
        r'([\d,]+\.?\d*)\s*KGS?\b',
        r'([\d.]+)\s*kg\b',
        r'Gross\s+Weight\s*[:\s]*([\d,]+\.?\d*)',
    ]
    for pat in patterns_gw:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            gross_weight = m.group(1).replace(',', '')
            break

    # ── Volume / CBM ───────────────────────────────────────────────────────────
    volume_cbm = ''
    patterns_vol = [
        r'([\d,]+\.?\d*)\s*CBM\b',                            # 12.500 CBM
        r'Volume\s*[:\s]*([\d,]+\.?\d*)\s*(?:CBM|M3|m³)',
        r'Measurement\s*[:\s]*([\d,]+\.?\d*)\s*(?:CBM|M3)',
        r'([\d,]+\.?\d*)\s*M3\b',
    ]
    for pat in patterns_vol:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            volume_cbm = m.group(1).replace(',', '')
            break

    # ── Vessel / Flight Number ─────────────────────────────────────────────────
    flight_number = ''
    patterns_flight = [
        r'(PR\d{3,4})',
        r'Flight\s*[:\s]+([A-Z]{2}\d{3,4})',
        r'Vessel\s*[:\s]+([^\n,]+)',                           # Vessel: MV EVER GLORY
        r'Voy(?:age)?\s*(?:No\.?)?\s*[:\s]+([A-Z0-9\-]+)',   # Voyage No.: 0123E
    ]
    for pat in patterns_flight:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            flight_number = m.group(1).strip()
            break

    # ── ETD / ETA dates ────────────────────────────────────────────────────────
    flight_date = ''
    patterns_fdate = [
        r'ETD\s*[:\s]*([\d/\-\.]+(?:\s+\d{4})?)',            # ETD: 2026-01-15
        r'ETA\s*[:\s]*([\d/\-\.]+(?:\s+\d{4})?)',            # ETA: 2026-01-22
        r'((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?\s*\d+,?\s*\d{4})',
        r'(\d{4}\.\d{2}\.\d{2})',
        r'(\d{2}/\d{2}/\d{4})',
    ]
    for pat in patterns_fdate:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            flight_date = m.group(1).strip()
            break

    # ── Port of Loading / Discharge ────────────────────────────────────────────
    port_loading = ''
    patterns_pol = [
        r'Port\s+of\s+Loading\s*[:\s]+([^\n,]+)',
        r'POL\s*[:\s]+([^\n,]+)',
        r'From\s*[:\s]+([^\n,]+)',
    ]
    for pat in patterns_pol:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            port_loading = m.group(1).strip()
            break

    port_discharge = ''
    patterns_pod = [
        r'Port\s+of\s+Discharge\s*[:\s]+([^\n,]+)',
        r'POD\s*[:\s]+([^\n,]+)',
        r'Destination\s*[:\s]+([^\n,]+)',
        r'To\s*[:\s]+([^\n,]+)',
    ]
    for pat in patterns_pod:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            port_discharge = m.group(1).strip()
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

    # Try to extract from "Nature and quantity of goods" or "Description of Goods"
    if not description:
        m = re.search(
            r'(?:Nature\s+and\s+Quantity\s+of\s+Goods|Description\s+of\s+Goods?)\s*[:\n]+\s*([^\n]+)',
            text, re.IGNORECASE
        )
        if m:
            description = m.group(1).strip()

    # ── No. of Pieces ──────────────────────────────────────────────────────────
    no_of_pieces = ''
    m = re.search(r'TOTAL[:\s]*([\d,]+)\s*PCS', text, re.IGNORECASE)
    if not m:
        m = re.search(r'([\d,]+)\s*(?:PCS|PIECES|CARTONS?|PKGS?)\b', text, re.IGNORECASE)
    if m:
        no_of_pieces = m.group(1).replace(',', '')

    # ── Shipper / Consignee ────────────────────────────────────────────────────
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

    # Fall back to port_discharge if destination is empty
    if not destination and port_discharge:
        destination = port_discharge

    return {
        'hawb_number':    _w(hawb_number,   0.90),
        'gross_weight':   _w(gross_weight,  0.90),
        'volume_cbm':     _w(volume_cbm,    0.85),
        'flight_number':  _w(flight_number, 0.85),
        'flight_date':    _w(flight_date,   0.85),
        'hs_code':        _w(hs_code,       0.85),
        'description':    _w(description,   0.80),
        'port_loading':   _w(port_loading,  0.80),
        'port_discharge': _w(port_discharge,0.80),
        'origin':         _w(origin,        0.75),
        'destination':    _w(destination,   0.75),
        'no_of_pieces':   _w(no_of_pieces,  0.80),
    }


def extract_fields_from_packing_list(text):
    """Extract shipping / cargo fields from a packing list document."""
    text_upper = text.upper()

    # ── Gross Weight ───────────────────────────────────────────────────────────
    gross_weight = ''
    patterns_gw = [
        r'Gross\s+Weight\s*[:\s]*([\d,]+\.?\d*)\s*(?:KGS?|kg)',
        r'G\.?W\.?\s*[:\s]*([\d,]+\.?\d*)\s*(?:KGS?|kg)',
        r'Total\s+G\.?W\.?\s*[:\s]*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*KGS?\b',
    ]
    for pat in patterns_gw:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            gross_weight = m.group(1).replace(',', '')
            break

    # ── Net Weight ────────────────────────────────────────────────────────────
    net_weight = ''
    patterns_nw = [
        r'Net\s+Weight\s*[:\s]*([\d,]+\.?\d*)\s*(?:KGS?|kg)',
        r'N\.?W\.?\s*[:\s]*([\d,]+\.?\d*)\s*(?:KGS?|kg)',
        r'Total\s+N\.?W\.?\s*[:\s]*([\d,]+\.?\d*)',
    ]
    for pat in patterns_nw:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            net_weight = m.group(1).replace(',', '')
            break

    # ── Number of Packages / Cartons ──────────────────────────────────────────
    num_packages = ''
    patterns_pkg = [
        r'Total\s+(?:No\.?\s+of\s+)?(?:Cartons?|Packages?|Cases?|Pkgs?)\s*[:\s]*([\d,]+)',
        r'([\d,]+)\s*(?:CARTONS?|PACKAGES?|CASES?|PKGS?)\b',
        r'No\.\s+of\s+(?:Cartons?|Packages?)\s*[:\s]*([\d,]+)',
    ]
    for pat in patterns_pkg:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            num_packages = m.group(1).replace(',', '')
            break

    # ── Total Quantity ─────────────────────────────────────────────────────────
    total_quantity = ''
    patterns_qty = [
        r'Total\s+Quantity\s*[:\s]*([\d,]+)',
        r'TOTAL[:\s]*([\d,]+)\s*PCS',
        r'([\d,]+)\s*(?:PCS|PIECES|UNITS?)\b',
    ]
    for pat in patterns_qty:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            total_quantity = m.group(1).replace(',', '')
            break

    # ── Volume / CBM ──────────────────────────────────────────────────────────
    volume_cbm = ''
    patterns_vol = [
        r'([\d,]+\.?\d*)\s*CBM\b',
        r'Volume\s*[:\s]*([\d,]+\.?\d*)\s*(?:CBM|M3|m³)',
        r'Total\s+(?:Volume|Measurement)\s*[:\s]*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*M3\b',
    ]
    for pat in patterns_vol:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            volume_cbm = m.group(1).replace(',', '')
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

    if not description:
        m = re.search(
            r'(?:Description\s+of\s+Goods?|Commodity)\s*[:\n]+\s*([^\n]+)',
            text, re.IGNORECASE
        )
        if m:
            description = m.group(1).strip()

    # ── Per-item rows (if packing list has line items) ─────────────────────────
    line_items = _extract_line_items(text)

    fields = {
        'gross_weight':   _w(gross_weight,  0.90),
        'net_weight':     _w(net_weight,    0.85),
        'num_packages':   _w(num_packages,  0.85),
        'total_quantity': _w(total_quantity,0.85),
        'volume_cbm':     _w(volume_cbm,    0.85),
        'description':    _w(description,   0.80),
    }

    if line_items:
        fields['__items__'] = line_items

    return fields


def process_document(file_path, document_type):
    text = extract_text_from_file(file_path)
    if not text:
        return None, "Could not extract text from document"

    if document_type == 'invoice':
        fields = extract_fields_from_invoice(text)
    elif document_type in ('airway_bill', 'bill_of_lading'):
        fields = extract_fields_from_hawb(text)
    elif document_type == 'packing_list':
        fields = extract_fields_from_packing_list(text)
    else:
        fields = {}

    return fields, text
