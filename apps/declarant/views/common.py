import json
import logging
import os
import re
import tempfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from apps.shipments.models import HSCode, Shipment, ShipmentDocument, StatusLog
from apps.shipments.due_dates import (
    URGENCY_BUSINESS_DAYS,
    add_business_days as _add_business_days,
    annotate_due as _annotate_due,
    business_days_diff as _business_days_diff,
    urgency_business_days as _urgency_business_days,
    urgency_days_for as _urgency_days_for,
)
from apps.shipments.fan import FAN_ASSESSMENT_FIELDS, fan_assessment_has_values, fan_assessment_rows
from apps.shipments.status_progress import build_status_progress
from apps.notifications.utils import create_notification, notify_shipment_status_change, send_assessed_email, send_billed_email
from apps.computation.ocr import process_document, _extract_line_items, _extract_hs_anchored_items
from apps.computation.models import ShipmentLineItem
from apps.supervisor.models import IssueReport

logger = logging.getLogger('r3pcr.declarant')

_CHAPTER_TITLES = {
    1: 'Live animals',
    2: 'Meat and edible meat offal',
    3: 'Fish and crustaceans, molluscs and other aquatic invertebrates',
    4: "Dairy produce; birds' eggs; natural honey; edible products of animal origin, not elsewhere specified or included",
    5: 'Products of animal origin, not elsewhere specified or included',
    6: 'Live trees and other plants; bulbs, roots and the like; cut flowers and ornamental foliage',
    7: 'Edible vegetables and certain roots and tubers',
    8: 'Edible fruit and nuts; peel of citrus fruit or melons',
    9: 'Coffee, tea, mate and spices',
    10: 'Cereals',
    11: 'Products of the milling industry; malt; starches; inulin; wheat gluten',
    12: 'Oil seeds and oleaginous fruits; miscellaneous grains, seeds and fruit; industrial or medicinal plants; straw and fodder',
    13: 'Lac; gums, resins and other vegetable saps and extracts',
    14: 'Vegetable plaiting materials; vegetable products not elsewhere specified or included',
    15: 'Animal, vegetable or microbial fats and oils and their cleavage products; prepared edible fats; animal or vegetable waxes',
    16: 'Preparations of meat, of fish, crustaceans, molluscs or other aquatic invertebrates, or of insects',
    17: 'Sugars and sugar confectionery',
    18: 'Cocoa and cocoa preparations',
    19: 'Preparations of cereals, flour, starch or milk; pastrycooks products',
    20: 'Preparations of vegetables, fruit, nuts or other parts of plants',
    21: 'Miscellaneous edible preparations',
    22: 'Beverages, spirits and vinegar',
    23: 'Residues and waste from the food industries; prepared animal fodder',
    24: 'Tobacco and manufactured tobacco substitutes; nicotine products',
    25: 'Salt; sulphur; earths and stone; plastering materials, lime and cement',
    26: 'Ores, slag and ash',
    27: 'Mineral fuels, mineral oils and products of their distillation; bituminous substances; mineral waxes',
    28: 'Inorganic chemicals; organic or inorganic compounds of precious metals, rare-earth metals, radioactive elements or isotopes',
    29: 'Organic chemicals',
    30: 'Pharmaceutical products',
    31: 'Fertilisers',
    32: 'Tanning or dyeing extracts; tannins and derivatives; dyes, pigments, paints, varnishes, putty and inks',
    33: 'Essential oils and resinoids; perfumery, cosmetic or toilet preparations',
    34: 'Soap, organic surface-active agents, washing preparations, lubricating preparations, waxes and similar products',
    35: 'Albuminoidal substances; modified starches; glues; enzymes',
    36: 'Explosives; pyrotechnic products; matches; pyrophoric alloys; certain combustible preparations',
    37: 'Photographic or cinematographic goods',
    38: 'Miscellaneous chemical products',
    39: 'Plastics and articles thereof',
    40: 'Rubber and articles thereof',
    41: 'Raw hides and skins, other than furskins, and leather',
    42: 'Articles of leather; saddlery and harness; travel goods, handbags and similar containers; articles of animal gut',
    43: 'Furskins and artificial fur; manufactures thereof',
    44: 'Wood and articles of wood; wood charcoal',
    45: 'Cork and articles of cork',
    46: 'Manufactures of straw, esparto or other plaiting materials; basketware and wickerwork',
    47: 'Pulp of wood or other fibrous cellulosic material; recovered paper or paperboard',
    48: 'Paper and paperboard; articles of paper pulp, of paper or of paperboard',
    49: 'Printed books, newspapers, pictures and other products of the printing industry; manuscripts, typescripts and plans',
    50: 'Silk',
    51: 'Wool, fine or coarse animal hair; horsehair yarn and woven fabric',
    52: 'Cotton',
    53: 'Other vegetable textile fibres; paper yarn and woven fabrics of paper yarn',
    54: 'Man-made filaments; strip and the like of man-made textile materials',
    55: 'Man-made staple fibres',
    56: 'Wadding, felt and nonwovens; special yarns; twine, cordage, ropes and cables and articles thereof',
    57: 'Carpets and other textile floor coverings',
    58: 'Special woven fabrics; tufted textile fabrics; lace; tapestries; trimmings; embroidery',
    59: 'Impregnated, coated, covered or laminated textile fabrics; textile articles suitable for industrial use',
    60: 'Knitted or crocheted fabrics',
    61: 'Articles of apparel and clothing accessories, knitted or crocheted',
    62: 'Articles of apparel and clothing accessories, not knitted or crocheted',
    63: 'Other made up textile articles; sets; worn clothing and worn textile articles; rags',
    64: 'Footwear, gaiters and the like; parts of such articles',
    65: 'Headgear and parts thereof',
    66: 'Umbrellas, sun umbrellas, walking-sticks, seat-sticks, whips, riding-crops and parts thereof',
    67: 'Prepared feathers and down; artificial flowers; articles of human hair',
    68: 'Articles of stone, plaster, cement, asbestos, mica or similar materials',
    69: 'Ceramic products',
    70: 'Glass and glassware',
    71: 'Natural or cultured pearls, precious or semi-precious stones, precious metals, imitation jewellery; coin',
    72: 'Iron and steel',
    73: 'Articles of iron or steel',
    74: 'Copper and articles thereof',
    75: 'Nickel and articles thereof',
    76: 'Aluminium and articles thereof',
    77: 'Reserved for possible future use in the Harmonized System',
    78: 'Lead and articles thereof',
    79: 'Zinc and articles thereof',
    80: 'Tin and articles thereof',
    81: 'Other base metals; cermets; articles thereof',
    82: 'Tools, implements, cutlery, spoons and forks, of base metal; parts thereof',
    83: 'Miscellaneous articles of base metal',
    84: 'Nuclear reactors, boilers, machinery and mechanical appliances; parts thereof',
    85: 'Electrical machinery and equipment and parts thereof; sound recorders and reproducers; television image and sound recorders and reproducers',
    86: 'Railway or tramway locomotives, rolling-stock and parts thereof; railway or tramway track fixtures and fittings',
    87: 'Vehicles other than railway or tramway rolling-stock, and parts and accessories thereof',
    88: 'Aircraft, spacecraft, and parts thereof',
    89: 'Ships, boats and floating structures',
    90: 'Optical, photographic, cinematographic, measuring, checking, precision, medical or surgical instruments and apparatus',
    91: 'Clocks and watches and parts thereof',
    92: 'Musical instruments; parts and accessories of such articles',
    93: 'Arms and ammunition; parts and accessories thereof',
    94: 'Furniture; bedding, mattresses, cushions and similar stuffed furnishings; lamps and lighting fittings; illuminated signs; prefabricated buildings',
    95: 'Toys, games and sports requisites; parts and accessories thereof',
    96: 'Miscellaneous manufactured articles',
    97: 'Works of art, collectors pieces and antiques',
}

ETRADE_LODGEMENT_URL = 'https://www.etrade.net.ph/etrade-2.0/login/auth'


# ─── Role Decorator ───────────────────────────────────────────────────────────

def declarant_required(view_func):
    """Restrict view to authenticated users with role='declarant'."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.error(request, 'Access denied - declarants only.')
            return redirect('accounts:login')
        if request.user.role != 'declarant':
            from apps.accounts.views.common import redirect_by_role
            messages.error(request, 'Access denied - declarants only.')
            return redirect_by_role(request.user)
        return view_func(request, *args, **kwargs)
    return wrapper


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _fan_amount(value):
    cleaned = re.sub(r'[^0-9.]', '', str(value or ''))
    return cleaned


def _run_and_store_document_ocr(doc):
    ext = os.path.splitext(doc.file.name)[1] or '.pdf'
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        doc.file.open('rb')
        tmp.write(doc.file.read())
        doc.file.close()
        tmp_path = tmp.name
    try:
        fields, raw_text, quality = process_document(tmp_path, doc.document_type)
        doc.ocr_text = raw_text or ''
        doc.ocr_fields_json = json.dumps(fields or {}, default=str)
        doc.ocr_quality = quality
        doc.ocr_ran_at = timezone.now()
        doc.save(update_fields=['ocr_text', 'ocr_fields_json', 'ocr_quality', 'ocr_ran_at'])
        return fields or {}, raw_text or '', quality
    finally:
        os.unlink(tmp_path)


def _ocr_scan_in_background(doc_ids):
    """Run OCR for the given documents in a daemon thread so the HTTP request
    returns immediately and the gunicorn worker isn't blocked. Progress is
    observable via each document's ocr_ran_at (clients poll `ocr_status`)."""
    from django.db import connection
    try:
        for doc in ShipmentDocument.objects.filter(id__in=doc_ids):
            try:
                _run_and_store_document_ocr(doc)
            except Exception as e:
                logger.warning('OCR-async failed for doc %s (%s): %s', doc.id, doc.document_type, e)
    finally:
        connection.close()   # don't leak this thread's DB connection


def _ocr_display_documents(documents):
    display = []
    for doc in documents:
        fields = []
        if doc.ocr_fields_json:
            try:
                data = json.loads(doc.ocr_fields_json)
            except (TypeError, ValueError):
                data = {}
            for key, field in data.items():
                if key.startswith('__'):
                    continue
                value = field.get('value') if isinstance(field, dict) else field
                confidence = field.get('confidence', 0) if isinstance(field, dict) else 0
                if value:
                    fields.append({
                        'name': key.replace('_', ' ').title(),
                        'value': value,
                        'confidence': float(confidence or 0),
                    })
        display.append({'doc': doc, 'fields': fields})
    return display


# ─── Dashboard ────────────────────────────────────────────────────────────────



__all__ = [
    'declarant_required', '_CHAPTER_TITLES', 'ETRADE_LODGEMENT_URL',
    'URGENCY_BUSINESS_DAYS', '_fan_amount',
    '_urgency_business_days', '_urgency_days_for', '_add_business_days',
    '_business_days_diff', '_annotate_due',
    '_run_and_store_document_ocr', '_ocr_scan_in_background',
    '_ocr_display_documents',
]
