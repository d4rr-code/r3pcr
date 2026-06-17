"""OCR text/number parsing helpers (shared by the engine, line-item, and field extractors)."""
import re

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


def _volume_cbm_from_dimensions(text):
    """
    Convert clear L x W x H measurements into CBM.
    Examples: 65x66x6 cm, 65 × 66 × 6 CM, 0.65 x 0.66 x 0.06 m.
    """
    if not text:
        return '', ''

    total_cbm = 0.0
    matches = []
    dim_re = re.compile(
        r'(?:(\d{1,4})\s*(?:CTNS?|CARTONS?|PKGS?|PACKAGES?|CASES?)\s*)?'
        r'(\d+(?:[.,]\d+)?)\s*(?:x|X|×|\*)\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:x|X|×|\*)\s*'
        r'(\d+(?:[.,]\d+)?)\s*(cm|cms|centimeters?|mm|millimeters?|m|meters?)\b',
        re.IGNORECASE,
    )

    for match in dim_re.finditer(text):
        multiplier = int(match.group(1) or 1)
        length = float(match.group(2).replace(',', '.'))
        width = float(match.group(3).replace(',', '.'))
        height = float(match.group(4).replace(',', '.'))
        unit = match.group(5).lower()

        if unit.startswith('mm') or unit.startswith('millimeter'):
            cbm = (length * width * height) / 1_000_000_000
        elif unit.startswith('cm') or unit.startswith('centimeter'):
            cbm = (length * width * height) / 1_000_000
        else:
            cbm = length * width * height

        if cbm <= 0 or cbm > 500:
            continue
        total_cbm += cbm * multiplier
        matches.append(match.group(0).strip())

    if total_cbm <= 0:
        return '', ''
    return f'{total_cbm:.4f}', '; '.join(matches[:5])


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


