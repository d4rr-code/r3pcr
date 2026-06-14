"""OCR line-item extraction: invoice/packing-list row patterns + HS-anchored fallback."""
import re

from .text_utils import _clean_number

# ── OCR line-item extraction: skip words, patterns + helpers (module-level) ──
# Hoisted out of _extract_line_items so the regexes compile once (not per call)
# and the pattern cascade can be flattened into _match_item_row().
SKIP_WORDS = {
    # totals / summaries
    'subtotal', 'sub-total', 'sub total', 'grand total', 'total', 'discount',
    'net value', 'net amount', 'net price', 'value of goods', 'invoice amount',
    'total amount', 'total value', 'credit', 'debit', 'balance', 'position',
    # logistics / charges
    'freight', 'insurance', 'shipping', 'handling', 'tax', 'vat', 'charges',
    'surcharge', 'customs', 'duty', 'fee', 'commission',
    # contact / header lines
    'tel', 'tel.', 'fax', 'fax.', 'email', 'e-mail', 'phone', 'mobile',
    'hotline', 'website', 'www.', 'address', 'addr.',
    # banking / payment details — must never become an item description
    'iban', 'bic', 'swift', 'sort code', 'account no', 'bank account',
    'routing', 'beneficiary', 'correspondent',
    # document / header words
    'invoice', 'description', 'item', 'qty', 'quantity', 'unit', 'price',
    'amount', 'no.', 'number', 'date', 'currency', 'terms', 'payment',
    'bank', 'page', 'consignee', 'shipper', 'marks', 'country of origin',
    'gross weight', 'net weight', 'packing', 'carton', 'certificate',
    'warranty', 'incoterm', 'delivery', 'order', 'contract', 'ref',
}

# Building-block sub-patterns
_M = r'(?:US\$|USD\s*|EUR\s*|PHP\s*|HKD\s*|CNY\s*|\$|€)?[\d,]+(?:\.\d{1,4})?'
_U = r'(?:PCS|PIECES|UNITS?|CTN|CTNS?|SET|SETS|ROLLS?|BOX(?:ES)?|KGS?|KG|EA|PAIRS?|PC|NOS?|LOTS?|PKGS?|PKG|BAGS?|BDL|BDLS?|PK|BTL|BTLS?)'
_HS = r'\d{4}(?:[\s.]?\d{2}){1,3}'
_CC = r'[A-Z]{2}'
_PKG = r'(?:BOX(?:ES)?|CTN|CTNS?|CARTONS?|PKGS?|PKG|PALLETS?|CASES?)'

# Pattern A — line with embedded HS code (and optional country code)
pat_A = re.compile(
    rf'^(?:\d+[\s.)]+)?(.+?)\s*({_HS})\s+(?:{_CC}\s+)?(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s*$',
    re.IGNORECASE,
)
# Pattern B — standard invoice line (no HS, no country code)
pat_B = re.compile(
    rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s*$',
    re.IGNORECASE,
)
# Pattern C — packing list (desc qty unit gross_wt net_wt pkgs)
pat_C = re.compile(
    rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s+(\d+)\s*$',
    re.IGNORECASE,
)
pat_C2 = re.compile(
    rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d[\d,]*(?:\.\d+)?)\s*({_U})\s+(\d[\d,]*(?:\.\d+)?)\s*{_PKG}\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s*$',
    re.IGNORECASE,
)
pat_C3 = re.compile(
    r'^(?:\d+[\s.)]+)?\S+\s+\S+\s+(.+?)\s+kg\s+.*?\s+(\d[\d,]*(?:[.,]\d+)?)\s*(PCS?|PC|PIECES|UNITS?)\s+([\d,]+(?:[.,]\d+)?)\s*kg\s+([\d,]+(?:[.,]\d+)?)\s*$',
    re.IGNORECASE,
)
# Pattern D — qty-only line (desc qty [unit] total, no unit price)
pat_D = re.compile(
    rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d{{1,8}})\s*({_U})?\s+({_M})\s*$',
    re.IGNORECASE,
)
# Pattern E — broad fallback: any desc + a decimal amount
pat_E = re.compile(
    r'^(?:\d+[\s.)]+)?([A-Za-z][A-Za-z0-9\s\-/,()]{3,80}?)\s+([\d,]+\.\d{1,4})\s*$',
    re.IGNORECASE,
)

# "HS CODE: XXXX.XX" label scanner
_hs_label_pat = re.compile(
    r'(?:\bHS[\s._-]*CODE\b|\bCustoms\s+tariff\s+no\.?)\s*[:\-]?\s*(\d{4}(?:[.\s]?\d{2}){1,3})',
    re.IGNORECASE,
)


def _normalize_hs_code(raw):
    digits = re.sub(r'\D', '', str(raw or ''))
    if len(digits) == 6:
        return f'{digits[:4]}.{digits[4:]}'
    if len(digits) == 8:
        return f'{digits[:4]}.{digits[4:6]}.{digits[6:]}'
    if len(digits) == 10:
        return f'{digits[:4]}.{digits[4:6]}.{digits[6:8]}.{digits[8:]}'
    return re.sub(r'\s+', '.', str(raw or '').strip())


def _match_item_row(line):
    """Try the line-item patterns in specificity order (A, C2, C3, C, B, D, E)
    and return the parsed fields of the first match, or None.

    Flattens what used to be a 7-deep if/else cascade. Returned keys are a
    subset of: desc_raw, qty, unit, unit_price_str, amount_str, gross_weight,
    net_weight, packages, inline_doc_hs_code, plus matched_pattern + confidence.
    """
    m = pat_A.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'inline_doc_hs_code': _normalize_hs_code(m.group(2)),
            'qty': m.group(3), 'unit': m.group(4) or '',
            'unit_price_str': m.group(5), 'amount_str': m.group(6),
            'matched_pattern': 'A', 'confidence': 0.90,
        }
    m = pat_C2.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'qty': m.group(2), 'unit': m.group(3) or '',
            'packages': _clean_number(m.group(4)),
            'gross_weight': _clean_number(m.group(5)),
            'net_weight': _clean_number(m.group(6)),
            'matched_pattern': 'C2', 'confidence': 0.88,
        }
    m = pat_C3.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'qty': m.group(2), 'unit': m.group(3) or '',
            'gross_weight': _clean_number(m.group(4).replace(',', '.')),
            'net_weight': _clean_number(m.group(5).replace(',', '.')),
            'matched_pattern': 'C3', 'confidence': 0.86,
        }
    m = pat_C.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'qty': m.group(2), 'unit': m.group(3) or '',
            'gross_weight': _clean_number(m.group(4)),
            'net_weight': _clean_number(m.group(5)),
            'packages': _clean_number(m.group(6)),
            'matched_pattern': 'C', 'confidence': 0.80,
        }
    m = pat_B.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'qty': m.group(2), 'unit': m.group(3) or '',
            'unit_price_str': m.group(4), 'amount_str': m.group(5),
            'matched_pattern': 'B', 'confidence': 0.80,
        }
    m = pat_D.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'qty': m.group(2), 'unit': m.group(3) or '',
            'amount_str': m.group(4),
            'matched_pattern': 'D', 'confidence': 0.65,
        }
    m = pat_E.match(line)
    if m:
        return {
            'desc_raw': m.group(1), 'amount_str': m.group(2), 'qty': '', 'unit': '',
            'matched_pattern': 'E', 'confidence': 0.50,
        }
    return None


def _extract_cell_table_items(lines):
    """Recover simple PDF tables extracted as one cell per line by pypdf."""
    cells = [line.strip() for line in lines if line and line.strip()]
    if not any(cell.lower() == 'line items' for cell in cells):
        return []

    def _is_row_number(value):
        return bool(re.fullmatch(r'\d{1,3}', value or ''))

    def _has_description(value):
        value = value or ''
        low = value.lower()
        return (
            len(value) >= 4
            and bool(re.search(r'[A-Za-z]{3,}', value))
            and not any(w in low for w in SKIP_WORDS)
        )

    def _clean_qty_unit(value):
        raw = str(value or '').strip()
        m = re.fullmatch(r'(\d[\d,]*(?:\.\d+)?)\s*([A-Za-z]+)?', raw)
        if not m:
            return raw, ''
        return m.group(1), (m.group(2) or '').upper()

    items = []
    i = 0
    while i < len(cells) - 2:
        if not _is_row_number(cells[i]):
            i += 1
            continue

        desc = re.sub(r'^\d+[\s.)]+', '', cells[i + 1]).strip()
        if not _has_description(desc):
            i += 1
            continue

        # Commercial invoice table:
        # no, description, HS, qty, unit, unit price, total
        if i + 6 < len(cells) and re.fullmatch(_HS, cells[i + 2], re.IGNORECASE):
            qty = cells[i + 3].strip()
            unit = cells[i + 4].strip().upper()
            unit_price = _clean_number(cells[i + 5])
            amount = _clean_number(cells[i + 6])
            if qty and unit and amount:
                try:
                    total_value = float(amount)
                except (TypeError, ValueError):
                    total_value = ''
                if total_value != '':
                    items.append({
                        'description':  desc[:200],
                        'quantity':     qty,
                        'unit':         unit,
                        'unit_price':   unit_price,
                        'total_value':  total_value,
                        'gross_weight': '',
                        'net_weight':   '',
                        'num_packages': '',
                        'source':       'ocr',
                        'confidence':   0.82,
                        'doc_hs_code':  _normalize_hs_code(cells[i + 2]),
                    })
                    i += 7
                    continue

        # Packing-list table:
        # no, description, qty+unit, packages, gross, net, volume
        if i + 5 < len(cells):
            qty, unit = _clean_qty_unit(cells[i + 2])
            packages = _clean_number(cells[i + 3])
            gross = _clean_number(cells[i + 4])
            net = _clean_number(cells[i + 5])
            if qty and gross and net:
                items.append({
                    'description':  desc[:200],
                    'quantity':     qty,
                    'unit':         unit,
                    'unit_price':   '',
                    'total_value':  '',
                    'gross_weight': gross,
                    'net_weight':   net,
                    'num_packages': packages,
                    'source':       'ocr',
                    'confidence':   0.76,
                    'doc_hs_code':  None,
                })
                i += 6
                continue

        i += 1

    return items


def _extract_line_items(text):
    """
    Extract individual line-item rows from commercial invoice / packing list text.

    Handles real-world invoice formats including:
      - Currency-prefixed amounts  (US$3.00, USD 29.61, EUR 5.00, $10, €5)
      - Embedded HS codes mid-line (4911 1010, 3923.90.90, 6402 99 00)
      - Country codes between HS and qty  (HK, CN, US)
      - Standard invoice lines (desc qty unit_price total)
      - Packing list lines    (desc qty gross_wt net_wt pkgs)
      - Qty-only lines        (desc qty total)

    Returns a list of dicts: [{description, quantity, unit, unit_price, total_value}, ...]
    Returns [] only when no reliable item rows are found.
    """
    lines = text.splitlines()

    _hs_at_line = {}  # line_index → normalized HS code string  e.g. "3923.30"
    for _i, _ln in enumerate(lines):
        _m = _hs_label_pat.search(_ln)
        if _m:
            _raw = _m.group(1).strip()
            # Normalize spaces/dots between digit groups → dots
            _normalized = _normalize_hs_code(_raw)
            _hs_at_line[_i] = _normalized

    def _nearby_description(start_idx, fallback=''):
        skip_fragments = {
            'serial number', 'order no', 'customs tariff', 'country of origin',
            'preferential origin', 'temperature range', 'voltage', 'frequency',
            'door type', 'interface', 'power plug', 'accessories',
        }
        for next_line in lines[start_idx + 1:start_idx + 9]:
            candidate = next_line.strip()
            if not candidate or len(candidate) < 4:
                continue
            low = candidate.lower()
            if any(frag in low for frag in skip_fragments):
                continue
            if _hs_label_pat.search(candidate):
                continue
            if re.search(r'(?:US\$|USD|EUR|PHP|\$|€)\s*[\d,]+(?:\.\d+)?', candidate, re.IGNORECASE):
                continue
            if re.match(r'^[\d\s,.$€\-=_/]+$', candidate):
                continue
            if re.search(r'[A-Za-z]{3,}', candidate):
                return re.sub(r'^\d+[\s.)]+', '', candidate).strip()
        return fallback

    items = []
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or len(line) < 10:
            continue
        low = line.lower()
        if any(w in low for w in SKIP_WORDS):
            continue

        # Skip phone / fax / address lines even when keyword appears mid-line
        # Patterns: +63, (63-2), (+63), (+1-), international dial codes, @-signs
        if re.search(r'(?:\+\d{1,3}[\s\-.]|\(\d{2,4}\)[\s\-]\d|\b\d{3}[-.\s]\d{4}\b|@\w+\.\w)', line):
            continue

        # Skip banking/payment lines: IBAN XX\d+, BIC/SWIFT codes, account numbers
        if re.search(r'\bIBAN\b|\bBIC\b|\bSWIFT\b|\bDE\d{2}\b', line, re.IGNORECASE):
            continue

        # Try patterns in specificity order: A → C2 → C3 → C → B → D → E
        match = _match_item_row(line)
        if not match:
            continue
        desc_raw           = match.get('desc_raw', '')
        qty                = match.get('qty', '')
        unit               = match.get('unit', '')
        unit_price_str     = match.get('unit_price_str', '')
        amount_str         = match.get('amount_str', '')
        gross_weight       = match.get('gross_weight', '')
        net_weight         = match.get('net_weight', '')
        packages           = match.get('packages', '')
        inline_doc_hs_code = match.get('inline_doc_hs_code')
        matched_pattern    = match['matched_pattern']
        confidence         = match['confidence']

        # Description must contain at least one word of ≥3 letters
        has_word_description = bool(re.search(r'[A-Za-z]{3,}', desc_raw))

        # Strip leading row number
        desc_part = re.sub(r'^\d+[\s.)]+', '', desc_raw).strip()
        desc_part = re.sub(rf'\s*{_HS}\s*$', '', desc_part, flags=re.IGNORECASE).strip()
        if not has_word_description or re.fullmatch(r'[A-Za-z]{1,4}\d+', desc_part or ''):
            desc_part = _nearby_description(idx, desc_part)
        if len(desc_part) < 4 or not re.search(r'[A-Za-z]{3,}', desc_part):
            continue

        if any(w in desc_part.lower() for w in SKIP_WORDS):
            continue

        # Reject qty that looks like a full HS code (8–10 consecutive digits) —
        # means Pattern B caught an embedded HS code instead of a real quantity
        if qty and re.match(r'^\d{8,10}$', qty.replace(' ', '').replace('.', '')):
            continue

        # Skip address-style descriptions: e.g. "1-15 Some Street" or
        # OCR fragments that start with a hyphenated number range
        if re.match(r'^\d+[-/]\d+', desc_part):
            continue

        # Parse amount — strip currency prefix before converting to float
        amount = ''
        if amount_str:
            raw_amt = re.sub(r'^(?:US\$|USD\s*|EUR\s*|PHP\s*|HKD\s*|CNY\s*|\$|€)', '', amount_str.strip())
            try:
                amount = float(raw_amt.replace(',', ''))
            except (ValueError, TypeError):
                if matched_pattern not in ('C',):   # packing list doesn't need amount
                    continue
            if isinstance(amount, float) and amount < 0.01:
                continue

        # Parse unit price
        unit_price = ''
        if unit_price_str:
            raw_up = re.sub(r'^(?:US\$|USD\s*|EUR\s*|PHP\s*|HKD\s*|CNY\s*|\$|€)', '', unit_price_str.strip())
            unit_price = _clean_number(raw_up)

        # Qty fallback — look for "(NNN PCS)" style in description
        if not qty:
            qty_m = re.search(
                r'\((\d+)\s*(?:PCS|PIECES|UNITS?|CTN|CTNS?|SET|SETS|ROLLS?|BOX(?:ES)?|KGS?)\)',
                desc_part, re.IGNORECASE,
            )
            if qty_m:
                qty = qty_m.group(1)
        if not qty:
            qty_inline = re.search(r'\s(\d+)\s+[\d,]+\.\d{2}\s*$', line)
            if qty_inline:
                qty = qty_inline.group(1)

        # ── HS Code from document: scan the next 1-6 lines for "HS CODE: XXXX"
        # This handles real invoices that print the code on its own line below
        # the item description, e.g.:
        #   BOTTLE, PLASTIC NASAL SPRAY 17/415, PE 30ML  ...  USD10,150.00
        #   HS CODE: 3923.30
        doc_hs_code = inline_doc_hs_code
        for _look in range(1, 21):
            _next_idx = idx + _look
            if _next_idx in _hs_at_line:
                doc_hs_code = _hs_at_line[_next_idx]
                break
            # Stop scanning forward if next line looks like a new item
            # (contains another currency amount — i.e. a new invoice row started)
            if _next_idx < len(lines):
                _next_stripped = lines[_next_idx].strip()
                if _next_stripped and re.search(
                    r'(?:US\$|USD)\s*[\d,]+\.\d{2}|\b[\d,]{4,}\.\d{2}\b',
                    _next_stripped,
                ):
                    break

        items.append({
            'description':  desc_part[:200],
            'quantity':     qty,
            'unit':         unit.upper(),
            'unit_price':   unit_price,
            'total_value':  amount,
            'gross_weight': gross_weight,
            'net_weight':   net_weight,
            'num_packages': packages,
            'source':       'ocr',
            'confidence':   confidence,
            'doc_hs_code':  doc_hs_code,  # HS code printed in the document (highest priority)
        })

    if not items:
        return _extract_cell_table_items(lines)

    # Drop trailing item if its total ≈ sum of all preceding items (subtotal slipped through)
    if len(items) >= 2:
        preceding_sum = sum(float(it['total_value'] or 0) for it in items[:-1])
        last_value    = float(items[-1].get('total_value') or 0)
        if preceding_sum > 0 and last_value and abs(last_value - preceding_sum) / preceding_sum < 0.01:
            items = items[:-1]

    return items


def _extract_hs_anchored_items(text):
    """
    Fallback extractor used when _extract_line_items() finds nothing.

    Strategy: scan for 'HS CODE: XXXX.XX' labels, then walk backwards
    through the preceding lines to reconstruct the item description.

    This handles real invoice formats where items span multiple lines:
        BOTTLE, PLASTIC NASAL       ← desc line 1
        SPRAY 17/415, PE 30ML       ← desc line 2 (continuation)
        HS CODE: 3923.30            ← anchor — we find this first

    Returns a list of minimal item dicts with doc_hs_code set.
    """
    hs_label_pat = re.compile(
        r'\bHS[\s._-]*CODE\b[\s:]*(\d{4}(?:[.\s]?\d{2}){1,3})',
        re.IGNORECASE,
    )

    # Fragments that indicate a line is NOT a product description
    _SKIP_LOW = {
        'tel', 'email', 'fax', 'www', 'invoice', 'packing list', 'packing',
        'total', 'payment', 'shipper', 'consignee', 'date', 'no.',
        'per proforma', 'cif', 'fob', 'manufacturer', 'documentary',
        'credit', 'shipping mark', 'packed in', 'authorized', 'signature',
        'room', 'road', 'jiangsu', 'china', 'philippines', 'quezon',
        'commodity', 'quantity', 'package', 'measurement', 'specs',
    }

    lines = text.splitlines()
    items = []
    seen_codes = set()

    for idx, line in enumerate(lines):
        m = hs_label_pat.search(line)
        if not m:
            continue

        raw_code = m.group(1).strip()
        hs_code  = re.sub(r'\s+', '.', raw_code)
        if hs_code in seen_codes:
            continue
        seen_codes.add(hs_code)

        # Walk backwards from the HS CODE line to collect description lines
        desc_lines = []
        for prev_idx in range(idx - 1, max(idx - 10, -1), -1):
            prev = lines[prev_idx].strip()
            if not prev or len(prev) < 3:
                continue
            # Stop at another HS code label
            if hs_label_pat.search(prev):
                break
            low = prev.lower()
            # Stop at header/footer/address lines
            if any(frag in low for frag in _SKIP_LOW):
                break
            # Skip lines that are pure numbers, amounts, or dashes
            if re.match(r'^[\d\s,.$€\-=_]+$', prev):
                break
            # Skip lines that look like table column headers
            if re.match(r'^(?:PCS|CTNS?|KGS?|CBM|UNIT|QTY)\b', prev, re.IGNORECASE):
                break

            # ── Strip inline qty + price suffix from invoice lines ────────────
            # e.g. "BOTTLE, PLASTIC NASAL  290,000  USD0.035/PC  USD10,150.00"
            # →    "BOTTLE, PLASTIC NASAL"
            clean = re.sub(
                r'\s+\d[\d,]+\s+(?:US\$?|USD|EUR|\$|€)[\d.,/\w]+.*$',
                '', prev, flags=re.IGNORECASE,
            ).strip()
            # Also strip trailing standalone large numbers (quantities like 290,000)
            clean = re.sub(r'\s+\d{1,3}(?:,\d{3})+\s*$', '', clean).strip()
            # Keep at least the cleaned version (even if it removes something)
            used = clean if (clean and re.search(r'[A-Za-z]{3,}', clean)) else prev

            desc_lines.insert(0, used)
            if len(desc_lines) >= 4:
                break

        if not desc_lines:
            continue

        # Join and do a final trim of any remaining price/qty at the end
        desc = ' '.join(desc_lines).strip()
        desc = re.sub(r'\s+\d[\d,]*(?:\.\d+)?\s*$', '', desc).strip()
        desc = re.sub(r'\s+(?:US\$?|USD|EUR)[\d.,]+\s*$', '', desc, flags=re.IGNORECASE).strip()

        if len(desc) < 4 or not re.search(r'[A-Za-z]{3,}', desc):
            continue

        items.append({
            'description':  desc[:200],
            'quantity':     '',
            'unit':         '',
            'unit_price':   '',
            'total_value':  '',
            'gross_weight': '',
            'net_weight':   '',
            'num_packages': '',
            'source':       'ocr',
            'confidence':   0.75,
            'doc_hs_code':  hs_code,
        })

    return items

