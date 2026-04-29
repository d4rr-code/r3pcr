import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import re
import os

def extract_text_from_file(file_path):
    """
    Extract raw text from PDF or image file
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == '.pdf':
            # Convert PDF pages to images
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
    """
    Extract key fields from Commercial Invoice text
    """
    fields = {
        'consignee_name': '',
        'consignee_address': '',
        'port_of_loading': '',
        'destination': '',
        'declared_value': '',
        'total_quantity': '',
        'invoice_number': '',
        'invoice_date': '',
        'hs_code': '',
        'description': '',
        'currency': 'USD',
    }

    lines = text.split('\n')
    text_upper = text.upper()

    # Extract Invoice Number (pattern: GM-XX-XXXX or similar)
    invoice_match = re.search(r'(GM-\d{2}-\d{4})', text)
    if invoice_match:
        fields['invoice_number'] = invoice_match.group(1)

    # Extract Invoice Date (pattern: YYYY.MM.DD)
    date_match = re.search(r'(\d{4}\.\d{2}\.\d{2})', text)
    if date_match:
        fields['invoice_date'] = date_match.group(1)

    # Extract HS Code (pattern: 9 or 10 digit number)
    hs_match = re.search(r'\b(9004\d{5,6})\b', text)
    if hs_match:
        fields['hs_code'] = hs_match.group(1)

    # Extract Total Invoice Value (USD amount)
    value_match = re.search(r'Total Invoice Value.*?USD.*?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if not value_match:
        value_match = re.search(r'\(\s*USD\s*\)\s*([\d,]+\.\d{2})', text)
    if value_match:
        fields['declared_value'] = value_match.group(1).replace(',', '')

    # Extract Port of Loading
    if 'INCHEON' in text_upper:
        fields['port_of_loading'] = 'INCHEON AIRPORT, SOUTH KOREA'

    # Extract Destination
    if 'MANILA' in text_upper:
        fields['destination'] = 'MANILA, PHILIPPINES'

    # Extract Consignee
    if 'IICOMBINED PHILIPPINES' in text_upper:
        fields['consignee_name'] = 'IICOMBINED PHILIPPINES INC.'
        fields['consignee_address'] = '28TH FLOOR MENARCO TOWER, 32ND STREET FORT BONIFACIO, TAGUIG CITY 1630'

    # Extract Description
    if 'SUNGLASSES' in text_upper:
        fields['description'] = 'Sunglasses'

    # Extract Total Quantity
    qty_match = re.search(r'Gross Total Quantity\s*:\s*(\d+)', text, re.IGNORECASE)
    if not qty_match:
        qty_match = re.search(r'Total Quantity\s*:\s*(\d+)', text, re.IGNORECASE)
    if qty_match:
        fields['total_quantity'] = qty_match.group(1)

    return fields


def extract_fields_from_hawb(text):
    """
    Extract key fields from House Airway Bill text
    """
    fields = {
        'hawb_number': '',
        'gross_weight': '',
        'no_of_pieces': '',
        'flight_number': '',
        'flight_date': '',
        'origin': '',
        'destination': '',
        'description': '',
        'hs_code': '',
    }

    text_upper = text.upper()

    # Extract HAWB Number (pattern: DECX-XXXXXX)
    hawb_match = re.search(r'(DECX-\d{6})', text, re.IGNORECASE)
    if hawb_match:
        fields['hawb_number'] = hawb_match.group(1)

    # Extract Gross Weight
    weight_match = re.search(r'(\d+\.?\d*)\s*K[Gg]', text)
    if weight_match:
        fields['gross_weight'] = weight_match.group(1)

    # Extract Flight Number
    flight_match = re.search(r'(PR\d{3})', text)
    if flight_match:
        fields['flight_number'] = flight_match.group(1)

    # Extract Date
    date_match = re.search(r'JAN\.?\s*(\d+),?\s*(\d{4})', text, re.IGNORECASE)
    if date_match:
        fields['flight_date'] = f"January {date_match.group(1)}, {date_match.group(2)}"

    # Extract HS Code
    hs_match = re.search(r'H\.?S\.?\s*CODE\s*:?\s*([\d.]+)', text, re.IGNORECASE)
    if hs_match:
        fields['hs_code'] = hs_match.group(1)

    # Extract Description
    if 'SUNGLASSES' in text_upper:
        fields['description'] = 'Sunglasses'

    # Origin and Destination
    if 'INCHEON' in text_upper:
        fields['origin'] = 'INCHEON, KOREA'
    if 'MANILA' in text_upper:
        fields['destination'] = 'MANILA, PHILIPPINES'

    return fields


def process_document(file_path, document_type):
    """
    Main function — extract text then parse fields
    based on document type
    """
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