import json


FAN_ASSESSMENT_FIELDS = [
    ('customs_duty', 'Customs Duty'),
    ('vat', 'VAT'),
    ('total_taxes', 'Total Taxes'),
    ('total_fees', 'Total Fees'),
    ('total_payable', 'Total Assessment / Amount Payable'),
]


def fan_assessment_rows(document):
    if not document:
        return []

    if document.ocr_fields_json:
        try:
            data = json.loads(document.ocr_fields_json)
        except Exception:
            data = {}
    else:
        data = {}

    rows = []
    for key, label in FAN_ASSESSMENT_FIELDS:
        raw = data.get(key, {})
        value = raw.get('value', '') if isinstance(raw, dict) else raw
        confidence = raw.get('confidence', None) if isinstance(raw, dict) else None
        rows.append({
            'key': key,
            'label': label,
            'value': value or '',
            'confidence': confidence,
        })
    return rows


def fan_assessment_has_values(rows):
    return any(str(row.get('value') or '').strip() for row in rows or [])
