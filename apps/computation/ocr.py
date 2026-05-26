import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
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


def assess_quality(text):
    if not text:
        return "poor"
    cleaned = text.strip()
    alnum_count = len(re.findall(r'[A-Za-z0-9]', cleaned))
    word_count = len(re.findall(r'[A-Za-z]{3,}', cleaned))
    if len(cleaned) < 100 or alnum_count < 50 or word_count < 8:
        return "poor"
    elif len(cleaned) < 300 or word_count < 30:
        return "low"
    return "good"


def _clean_text(value):
    return ' '.join(str(value or '').replace('\n', ' ').split())


def _clean_number(value):
    if value is None:
        return ''
    value = str(value).strip().replace(',', '')
    return re.sub(r'[^0-9.\-]', '', value)


def _first_match(text, patterns, group=1):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _clean_text(match.group(group))
    return ''


def _block_after_label(text, label_patterns, stop_patterns=None, max_lines=4):
    lines = [line.strip() for line in text.splitlines()]
    stop_patterns = stop_patterns or []
    for idx, line in enumerate(lines):
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in label_patterns):
            captured = []
            inline = re.sub('|'.join(label_patterns), '', line, flags=re.IGNORECASE).strip(' :-')
            if inline:
                captured.append(inline)
            for next_line in lines[idx + 1:idx + 1 + max_lines]:
                if not next_line:
                    continue
                if any(re.search(pattern, next_line, re.IGNORECASE) for pattern in stop_patterns):
                    break
                captured.append(next_line)
            return _clean_text(' '.join(captured))
    return ''


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


def _preprocess_image_for_ocr(image):
    """
    Normalize scanned document images before Tesseract.
    Upscaling, grayscale, autocontrast, light sharpening and thresholding help
    the common phone-scan cases: faint text, shadows, and low resolution.
    """
    image = image.convert('RGB')
    max_side = max(image.size)
    if max_side < 2200:
        scale = 2200 / max_side
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.6)
    gray = gray.filter(ImageFilter.SHARPEN)
    threshold = 180
    return gray.point(lambda px: 255 if px > threshold else 0, mode='1')


def _tesseract_confidence(image, config):
    try:
        data = pytesseract.image_to_data(
            image,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
        confs = []
        for value in data.get('conf', []):
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            if score >= 0:
                confs.append(score)
        if confs:
            return sum(confs) / len(confs)
    except Exception as e:
        print(f"[OCR] Tesseract confidence check failed: {e}")
    return 0


def _tesseract_image_to_text(image):
    original = image.convert('RGB')
    processed = _preprocess_image_for_ocr(original)
    variants = [
        ('processed-psm6', processed, '--psm 6'),
        ('processed-psm4', processed, '--psm 4'),
        ('processed-psm3', processed, '--psm 3'),
        ('original-psm3', original, '--psm 3'),
    ]
    best_text = ''
    best_score = -1
    for label, candidate, config in variants:
        try:
            text = pytesseract.image_to_string(candidate, config=config)
            confidence = _tesseract_confidence(candidate, config)
            useful_chars = len(re.findall(r'[A-Za-z0-9]', text or ''))
            score = useful_chars + confidence * 4
            print(f"[OCR] Tesseract {label}: {len(text or '')} chars, conf {confidence:.1f}")
            if score > best_score:
                best_score = score
                best_text = text or ''
        except Exception as e:
            print(f"[OCR] Tesseract {label} failed: {e}")
    try:
        processed.close()
    except Exception:
        pass
    try:
        original.close()
    except Exception:
        pass
    return best_text


def _image_to_text(image, api_key=''):
    if api_key:
        try:
            buf = io.BytesIO()
            image.convert('RGB').save(buf, format='JPEG', quality=92)
            vision_text = _vision_api_call(api_key, buf.getvalue())
            buf.close()
            if assess_quality(vision_text) != 'poor':
                print(f"[OCR] Vision accepted: {len(vision_text)} chars")
                return vision_text
            print("[OCR] Vision output poor/empty; falling back to Tesseract")
        except Exception as e:
            print(f"[OCR] Vision path failed; falling back to Tesseract: {e}")
    return _tesseract_image_to_text(image)


def _extract_text_from_pdf_direct(file_path):
    """
    Fast path: extract embedded text directly from a text-based PDF using pypdf.
    Returns the extracted text, or '' if pypdf is unavailable or the PDF is image-only.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(file_path)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ''
            pages_text.append(text)
        return '\n'.join(pages_text).strip()
    except Exception as e:
        print(f"[OCR] pypdf direct extraction failed: {e}")
        return ''


def extract_text_from_file(file_path):
    """
    Extract text from a document file.

    Strategy (in order):
    1. For PDFs: try pypdf direct text extraction first (fast, lossless for digital PDFs).
       If >= 80 chars are extracted, use that result — no image conversion needed.
    2. Fall back to image-based OCR:
       - Google Vision API if GOOGLE_VISION_API_KEY is set
       - Tesseract otherwise (page-by-page at 150 DPI, --psm 3 for complex layouts)
    3. For image files: Vision API or Tesseract directly.
    """
    import gc
    from pdf2image import pdfinfo_from_path

    ext = os.path.splitext(file_path)[1].lower()
    api_key = os.getenv('GOOGLE_VISION_API_KEY', '')

    try:
        if ext == '.pdf':
            # ── Fast path: direct text extraction from digital PDFs ──────────
            direct_text = _extract_text_from_pdf_direct(file_path)
            if assess_quality(direct_text) != 'poor':
                print(f"[OCR] pypdf direct: {len(direct_text)} chars extracted")
                return direct_text

            # ── Image-based OCR path (for scanned PDFs) ──────────────────────
            print(f"[OCR] pypdf got {len(direct_text.strip())} chars — falling back to image OCR")
            poppler_path = os.getenv('POPPLER_PATH') or None

            # Determine page count without loading all pages into memory
            try:
                info = pdfinfo_from_path(file_path, poppler_path=poppler_path)
                num_pages = int(info.get('Pages', 1))
            except Exception:
                num_pages = 10  # safe fallback

            full_text = ''
            for page_num in range(1, num_pages + 1):
                try:
                    images = convert_from_path(
                        file_path,
                        dpi=300,
                        first_page=page_num,
                        last_page=page_num,
                        poppler_path=poppler_path,
                    )
                    if not images:
                        break
                    image = images[0]

                    # _image_to_text: Vision API → if poor/failed → Tesseract
                    page_text = _image_to_text(image, api_key)
                    print(f"[OCR] page {page_num}: {len(page_text)} chars")
                    full_text += page_text + '\n'

                    # Explicitly release page memory before next iteration
                    image.close()
                    del image, images
                    gc.collect()

                except Exception as page_err:
                    print(f"[OCR] page {page_num} error: {page_err}")
                    break

            return full_text

        elif ext in ['.jpg', '.jpeg', '.png']:
            if api_key:
                with open(file_path, 'rb') as f:
                    return _vision_api_call(api_key, f.read())
            else:
                image = Image.open(file_path)
                text = pytesseract.image_to_string(image, config='--psm 3')
                image.close()
                return text
        else:
            return ''

    except Exception as e:
        print(f"[OCR] extraction error: {e}")
        return ''


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
    SKIP_WORDS = {
        # totals / summaries
        'subtotal', 'sub-total', 'sub total', 'grand total', 'total', 'discount',
        'net value', 'net amount', 'net price', 'value of goods', 'invoice amount',
        'total amount', 'total value', 'credit', 'debit', 'balance', 'position',
        # logistics / charges
        'freight', 'insurance', 'shipping', 'handling', 'tax', 'vat', 'charges',
        'surcharge', 'customs', 'duty', 'fee', 'commission',
        # contact / header lines  ← NEW
        'tel', 'tel.', 'fax', 'fax.', 'email', 'e-mail', 'phone', 'mobile',
        'hotline', 'website', 'www.', 'address', 'addr.',
        # document / header words
        'invoice', 'description', 'item', 'qty', 'quantity', 'unit', 'price',
        'amount', 'no.', 'number', 'date', 'currency', 'terms', 'payment',
        'bank', 'page', 'consignee', 'shipper', 'marks', 'country of origin',
        'gross weight', 'net weight', 'packing', 'carton', 'certificate',
        'warranty', 'incoterm', 'delivery', 'order', 'contract', 'ref',
    }

    # ── Building-block sub-patterns ───────────────────────────────────────────
    # Money: optional currency prefix, digits with commas, optional decimals
    _M = r'(?:US\$|USD\s*|EUR\s*|PHP\s*|HKD\s*|CNY\s*|\$|€)?[\d,]+(?:\.\d{1,4})?'
    # Unit of measure
    _U = r'(?:PCS|PIECES|UNITS?|CTN|CTNS?|SET|SETS|ROLLS?|BOX(?:ES)?|KGS?|KG|EA|PAIRS?|PC|NOS?|LOTS?|PKGS?|PKG|BAGS?|BDL|BDLS?|PK|BTL|BTLS?)'
    # HS code: 4 digits then 1–3 groups of 2 digits separated by optional space or dot
    _HS = r'\d{4}(?:[\s.]?\d{2}){1,3}'
    # 2-letter country / origin code (appears between HS and qty in some invoices)
    _CC = r'[A-Z]{2}'

    # ── Pattern A ─ line with embedded HS code (and optional country code) ───
    # e.g. "THE PENINSULA GROUP MAGAZINE 2025  4911 1010  HK  625  US$3.00  US$1,875.00"
    pat_A = re.compile(
        rf'^(?:\d+[\s.)]+)?(.+?)\s+(?:{_HS})\s+(?:{_CC}\s+)?(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s*$',
        re.IGNORECASE,
    )

    # ── Pattern B ─ standard invoice line (no HS, no country code) ───────────
    # e.g. "1 PLASTIC BOTTLE 500ML  100  PCS  2.50  250.00"
    pat_B = re.compile(
        rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s*$',
        re.IGNORECASE,
    )

    # ── Pattern C ─ packing list (desc qty unit gross_wt net_wt pkgs) ────────
    # e.g. "NASAL SPRAY 200  PCS  15.00  12.00  10"
    pat_C = re.compile(
        rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d[\d,]*(?:\.\d+)?)\s*({_U})?\s+({_M})\s+({_M})\s+(\d+)\s*$',
        re.IGNORECASE,
    )

    # ── Pattern D ─ qty-only line (desc qty [unit] total, no unit price) ─────
    # e.g. "SAMPLE ITEM  50  PCS  1,250.00"
    pat_D = re.compile(
        rf'^(?:\d+[\s.)]+)?(.+?)\s+(\d{{1,8}})\s*({_U})?\s+({_M})\s*$',
        re.IGNORECASE,
    )

    # ── Pattern E ─ broad fallback: any desc + a decimal amount ───────────────
    # e.g. "LABORATORY OVEN 3,500.00"  or  "Spare Parts for Centrifuge 1250.00"
    pat_E = re.compile(
        r'^(?:\d+[\s.)]+)?([A-Za-z][A-Za-z0-9\s\-/,()]{3,80}?)\s+([\d,]+\.\d{1,4})\s*$',
        re.IGNORECASE,
    )

    items = []
    for raw_line in text.splitlines():
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

        matched_pattern = None
        desc_raw = qty = unit = unit_price_str = amount_str = ''
        gross_weight = net_weight = packages = ''
        confidence = 0.70

        # Try patterns in specificity order: A (most specific) → D (least)
        m = pat_A.match(line)
        if m:
            desc_raw, qty, unit = m.group(1), m.group(2), m.group(3) or ''
            unit_price_str, amount_str = m.group(4), m.group(5)
            matched_pattern, confidence = 'A', 0.90
        else:
            m = pat_C.match(line)
            if m:
                desc_raw, qty, unit = m.group(1), m.group(2), m.group(3) or ''
                gross_weight = _clean_number(m.group(4))
                net_weight   = _clean_number(m.group(5))
                packages     = _clean_number(m.group(6))
                matched_pattern, confidence = 'C', 0.80
            else:
                m = pat_B.match(line)
                if m:
                    desc_raw, qty, unit = m.group(1), m.group(2), m.group(3) or ''
                    unit_price_str, amount_str = m.group(4), m.group(5)
                    matched_pattern, confidence = 'B', 0.80
                else:
                    m = pat_D.match(line)
                    if m:
                        desc_raw, qty, unit = m.group(1), m.group(2), m.group(3) or ''
                        amount_str = m.group(4)
                        matched_pattern, confidence = 'D', 0.65
                    else:
                        m = pat_E.match(line)
                        if m:
                            desc_raw   = m.group(1)
                            amount_str = m.group(2)
                            qty = unit = ''
                            matched_pattern, confidence = 'E', 0.50

        if not matched_pattern:
            continue

        # Description must contain at least one word of ≥3 letters
        if not re.search(r'[A-Za-z]{3,}', desc_raw):
            continue

        # Strip leading row number
        desc_part = re.sub(r'^\d+[\s.)]+', '', desc_raw).strip()
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
        })

    if not items:
        return []

    # Drop trailing item if its total ≈ sum of all preceding items (subtotal slipped through)
    if len(items) >= 2:
        preceding_sum = sum(float(it['total_value'] or 0) for it in items[:-1])
        last_value    = float(items[-1].get('total_value') or 0)
        if preceding_sum > 0 and last_value and abs(last_value - preceding_sum) / preceding_sum < 0.01:
            items = items[:-1]

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
        'description':    _w(description,   0.80),
    }

    if line_items:
        fields['__items__'] = line_items

    return fields


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
    else:
        fields = {}

    return fields, text, quality
