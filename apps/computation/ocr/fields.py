"""OCR structured-field extraction per document type (invoice, HAWB, packing list, FAN)."""
import re
from decimal import Decimal, InvalidOperation

from .text_utils import (
    _w, _clean_text, _clean_number, _volume_cbm_from_dimensions,
    _first_match, _block_after_label,
)
from .line_items import _extract_line_items

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
        r'(\d{4}-\d{2}-\d{2})',
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
    elif 'PHP' in text_upper:
        currency = 'PHP'

    shipper_name = _block_after_label(
        text,
        [r'\bShipper\b', r'\bSeller\b', r'\bExporter\b'],
        [r'\bConsignee\b', r'\bBuyer\b', r'\bInvoice\b', r'\bNotify\b'],
    )
    country_of_origin = _first_match(text, [
        r'Country\s+of\s+Origin\s*[:\s]+([A-Za-z ,.-]+)',
        r'Origin\s*[:\s]+([A-Za-z ,.-]+)',
        r'Made\s+in\s+([A-Za-z ,.-]+)',
    ])

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
    description = _block_after_label(
        text,
        [r'\bDescription\s+of\s+Goods?\b', r'\bCommodity\b', r'\bGoods?\b'],
        [r'\bTotal\b', r'\bAmount\b', r'\bShipper\b', r'\bConsignee\b', r'\bInvoice\b'],
        max_lines=2,
    )
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
    if not consignee_name:
        m = re.search(r'TO:\s*([^\n]+)', text, re.IGNORECASE)
        if m:
            consignee_name = m.group(1).strip()
    if not consignee_name:
        consignee_name = _block_after_label(
            text,
            [r'\bConsignee\b', r'\bBuyer\b', r'\bSold\s+to\b', r'\bBill\s+to\b'],
            [r'\bShipper\b', r'\bNotify\b', r'\bInvoice\b', r'\bPort\b', r'\bDescription\b'],
            max_lines=3,
        )
    # Extract address as the lines immediately following the consignee name block
    if not consignee_address:
        consignee_address = _block_after_label(
            text,
            [r'\bConsignee\b', r'\bBuyer\b', r'\bBill\s+to\b'],
            [r'\bShipper\b', r'\bNotify\b', r'\bPort\b', r'\bDescription\b', r'\bInvoice\b'],
            max_lines=4,
        )
        # If the address block is the same as the name, clear it (single-line consignee block)
        if consignee_address == consignee_name:
            consignee_address = ''

    # ── Port / Destination ─────────────────────────────────────────────────────
    port_of_loading = _first_match(text, [
        r'Port\s+of\s+Loading\s*[:\s]+([^\n,]+)',
        r'Port\s+of\s+Origin\s*[:\s]+([^\n,]+)',
        r'Origin\s+Port\s*[:\s]+([^\n,]+)',
        r'POL\s*[:\s]+([^\n,]+)',
        r'Shipped\s+from\s*[:\s]+([^\n,]+)',
    ])

    destination = _first_match(text, [
        r'Port\s+of\s+Discharge\s*[:\s]+([^\n,]+)',
        r'Port\s+of\s+Destination\s*[:\s]+([^\n,]+)',
        r'Destination\s+Port\s*[:\s]+([^\n,]+)',
        r'POD\s*[:\s]+([^\n,]+)',
        r'Shipped\s+to\s*[:\s]+([^\n,]+)',
    ])

    fields = {
        'invoice_number':    _w(invoice_number,    0.90),
        'invoice_date':      _w(invoice_date,      0.90),
        'hs_code':           _w(hs_code,           0.90),
        'declared_value':    _w(declared_value,    0.90),
        'total_quantity':    _w(total_quantity,    0.90),
        'description':       _w(description,       0.80),
        'shipper_name':      _w(shipper_name,      0.75),
        'consignee_name':    _w(consignee_name,    0.80),
        'consignee_address': _w(consignee_address, 0.75),
        'country_of_origin': _w(country_of_origin, 0.75),
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
        r'HAWB\s*(?:No\.?|Number)?\s*[:\s]+([A-Z0-9\-]+)',
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
    dimensions_text = ''
    if not volume_cbm:
        volume_cbm, dimensions_text = _volume_cbm_from_dimensions(text)

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
        r'Port\s+of\s+Origin\s*[:\s]+([^\n,]+)',
        r'Origin\s+Port\s*[:\s]+([^\n,]+)',
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
        r'Port\s+of\s+Destination\s*[:\s]+([^\n,]+)',
        r'Destination\s+Port\s*[:\s]+([^\n,]+)',
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
    description = _block_after_label(
        text,
        [r'\bNature\s+and\s+Quantity\s+of\s+Goods\b', r'\bDescription\s+of\s+Goods?\b', r'\bCommodity\b'],
        [r'\bShipper\b', r'\bConsignee\b', r'\bTotal\b', r'\bWeight\b'],
        max_lines=2,
    )
    if not description:
        m = re.search(
            r'(?:Nature\s+and\s+Quantity\s+of\s+Goods|Description\s+of\s+Goods?)\s*[:\n]+\s*([^\n]+)',
            text, re.IGNORECASE
        )
        if m:
            description = m.group(1).strip()

    # ── No. of Pieces ──────────────────────────────────────────────────────────
    no_of_pieces = ''
    m = re.search(r'(?:No\.?\s*of\s*Pieces|Number\s+of\s+Pieces|Pieces)\s*[:\s]+([\d,]+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'TOTAL[:\s]*([\d,]+)\s*PCS', text, re.IGNORECASE)
    if not m:
        m = re.search(r'([\d,]+)\s*(?:PCS|PIECES|CARTONS?|PKGS?)\b', text, re.IGNORECASE)
    if m:
        no_of_pieces = m.group(1).replace(',', '')

    # ── Origin / Destination ───────────────────────────────────────────────────
    origin = _first_match(text, [
        r'Airport\s+of\s+Departure\s*[:\s]+([^\n,]+)',
        r'Port\s+of\s+Loading\s*[:\s]+([^\n,]+)',
        r'Origin\s*[:\s]+([^\n,]+)',
        r'From\s*[:\s]+([^\n,]+)',
    ])

    destination = _first_match(text, [
        r'Airport\s+of\s+Destination\s*[:\s]+([^\n,]+)',
        r'Port\s+of\s+Discharge\s*[:\s]+([^\n,]+)',
        r'Port\s+of\s+Destination\s*[:\s]+([^\n,]+)',
        r'Destination\s*[:\s]+([^\n,]+)',
        r'To\s*[:\s]+([^\n,]+)',
    ])

    # Fall back to port_discharge if destination is empty
    if not destination and port_discharge:
        destination = port_discharge

    shipper_name = _block_after_label(
        text,
        [r'\bShipper\b', r'\bSender\b'],
        [r'\bConsignee\b', r'\bNotify\b', r'\bPort\b', r'\bAirport\b'],
    )
    consignee_name = _block_after_label(
        text,
        [r'\bConsignee\b'],
        [r'\bNotify\b', r'\bShipper\b', r'\bPort\b', r'\bAirport\b'],
    )

    return {
        'hawb_number':    _w(hawb_number,   0.90),
        'bol_number':     _w(hawb_number,   0.90),
        'gross_weight':   _w(gross_weight,  0.90),
        'total_gross_weight': _w(gross_weight, 0.90),
        'volume_cbm':     _w(volume_cbm,    0.85),
        'dimensions':     _w(dimensions_text, 0.75),
        'flight_number':  _w(flight_number, 0.85),
        'flight_date':    _w(flight_date,   0.85),
        'hs_code':        _w(hs_code,       0.85),
        'description':    _w(description,   0.80),
        'shipper_name':   _w(shipper_name,  0.75),
        'consignee_name': _w(consignee_name,0.75),
        'port_loading':   _w(port_loading,  0.80),
        'port_discharge': _w(port_discharge,0.80),
        'port_origin':    _w(port_loading or origin, 0.80),
        'port_destination': _w(port_discharge or destination, 0.80),
        'origin':         _w(origin,        0.75),
        'destination':    _w(destination,   0.75),
        'no_of_pieces':   _w(no_of_pieces,  0.80),
        'number_of_pieces': _w(no_of_pieces,0.80),
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
    dimensions_text = ''
    if not volume_cbm:
        volume_cbm, dimensions_text = _volume_cbm_from_dimensions(text)

    # ── Description ────────────────────────────────────────────────────────────
    description = _block_after_label(
        text,
        [r'\bDescription\s+of\s+Goods?\b', r'\bCommodity\b'],
        [r'\bTotal\b', r'\bWeight\b', r'\bPackage\b', r'\bShipper\b'],
        max_lines=2,
    )
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
        'dimensions':     _w(dimensions_text, 0.75),
        'description':    _w(description,   0.80),
    }

    if line_items:
        fields['__items__'] = line_items

    return fields


def extract_fields_from_fan(text):
    """Extract key assessment amounts from a Final Assessment Notice."""
    amount_pattern = r'([0-9][0-9,.\s]*[0-9])'

    def _fan_number(value):
        raw = re.sub(r'[^0-9,.\-]', '', str(value or '').replace(' ', ''))
        if not raw:
            return ''
        if '.' not in raw and ',' in raw:
            parts = raw.split(',')
            if len(parts[-1]) == 2:
                raw = ''.join(parts[:-1]) + '.' + parts[-1]
            else:
                raw = ''.join(parts)
        else:
            raw = raw.replace(',', '')
        return re.sub(r'[^0-9.\-]', '', raw)

    def _amount_after(labels, prefer_last=False):
        matches = []
        for label in labels:
            pat = rf'{label}\s*(?:PHP|Php|P)?\s*[:\-]?\s*{amount_pattern}'
            for m in re.finditer(pat, text, re.IGNORECASE | re.MULTILINE):
                matches.append(_fan_number(m.group(1)))
        matches = [m for m in matches if m]
        if not matches:
            return ''
        return matches[-1] if prefer_last else matches[0]

    def _decimal(value):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    customs_duty = _amount_after([
        r'CUSTOMS\s+DUTY',
        r'AMOUNT\s+OF\s+DUTY',
        r'\bCUD\b',
    ])
    vat = _amount_after([
        r'TOTAL\s+VAT',
        r'VALUE\s+ADDED\s+TAX',
        r'\bVAT\b',
    ], prefer_last=True)
    total_taxes = _amount_after([
        r'TOTAL\s+ITEM\s+TAXES',
        r'TOTAL\s+DUTIES\s+AND\s+TAXES',
        r'DUTIES\s+AND\s+TAXES',
        r'TOTAL\s+TAXES',
    ])
    total_fees = _amount_after([
        r'TOTAL\s+FEES',
        r'TOTAL\s+GLOBAL\s+TAXES',
        r'OTHER\s+CHARGES',
        r'CHARGES\s+AND\s+FEES',
    ])
    total_payable = _amount_after([
        r'TOTAL\s+ASSESSMENT',
        r'TOTAL\s+AMOUNT\s+PAYABLE',
        r'AMOUNT\s+PAYABLE',
        r'GRAND\s+TOTAL',
        r'TOTAL\s+PAYABLE',
        r'TOTAL\s+AMOUNT\s+DUE',
        r'AMOUNT\s+DUE',
    ], prefer_last=True)

    taxes = _decimal(total_taxes)
    fees = _decimal(total_fees)
    payable = _decimal(total_payable)
    if taxes is not None and fees is not None:
        computed_payable = taxes + fees
        if payable is None or abs(payable - computed_payable) > Decimal('1.00'):
            total_payable = f'{computed_payable:.2f}'

    return {
        'customs_duty':  _w(customs_duty, 0.80),
        'vat':           _w(vat, 0.80),
        'total_taxes':   _w(total_taxes, 0.75),
        'total_fees':    _w(total_fees, 0.70),
        'total_payable': _w(total_payable, 0.80),
    }


