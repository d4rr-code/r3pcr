import json
import logging
import re

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q, Count
from apps.shipments.models import Shipment, HSCode, ShipmentHSCode
from ..models import ShipmentLineItem

logger = logging.getLogger('r3pcr.computation')

_HS_STOPWORDS = {
    'the','and','for','with','from','this','that','are','all','per',
    'each','pcs','set','unit','nos','lot','item','items','qty','piece',
    'pieces','new','used','other','various','type','types','model','grade',
    'size','kind','made','part','parts','product','products','goods',
    'invoice','commercial','packing','list','description','quantity','amount',
    'total','value','price','currency','origin','country','weight','gross',
    'net','freight','insurance','shipment','shipper','consignee','code',
    'number','date','page','carton','cartons','package','packages',
    'name','address','contact','telephone','email','port','loading',
    'discharge','company','limited','ltd','corp','inc','manila','china',
    'usd','eur','php','amount','unit',
}


_HS_PHRASE_BOOSTS = [
    (('circuit', 'board'), ('printed circuit', 'circuit')),
    (('printed', 'circuit'), ('printed circuit', 'circuit')),
    (('usb',), ('connector', 'conductor', 'wire', 'cable')),
    (('cable',), ('connector', 'conductor', 'wire', 'cable')),
    (('wire',), ('connector', 'conductor', 'wire', 'cable')),
    (('connector',), ('connector', 'conductor', 'wire', 'cable')),
    (('led',), ('lamp', 'lighting', 'diode', 'semiconductor')),
]


def _hs_normalize_word(word):
    word = (word or '').lower().strip()
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 4 and word.endswith(('ches', 'shes', 'xes', 'zes')):
        return word[:-2]
    if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def _hs_keyword_tokens(text):
    tokens = []
    seen = set()
    for raw in re.findall(r'[a-zA-Z]{3,}', (text or '').lower()):
        token = _hs_normalize_word(raw)
        if token in _HS_STOPWORDS or len(token) < 3:
            continue
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _hs_code_candidates(value):
    digits = re.sub(r'\D', '', str(value or ''))
    if len(digits) < 6:
        return []
    candidates = [digits]
    if len(digits) == 6:
        candidates.append(f'{digits[:4]}.{digits[4:]}')
    elif len(digits) == 8:
        candidates.append(f'{digits[:4]}.{digits[4:6]}.{digits[6:]}')
    elif len(digits) == 10:
        candidates.append(f'{digits[:4]}.{digits[4:6]}.{digits[6:8]}.{digits[8:]}')
    raw = str(value or '').strip()
    if raw:
        candidates.append(raw)
    return list(dict.fromkeys(candidates))


def _hs_code_digits(value):
    return re.sub(r'\D', '', str(value or ''))


def find_hs_by_document_code(value):
    """
    Resolve an HS code printed in a supplier document.

    OCR may read the same code as 8534.00.00, 85340000, or 8534 00 00.
    We compare digit-normalized codes first so exact document codes are pinned
    instead of falling back to the first broad chapter/prefix match.
    """
    digits = _hs_code_digits(value)
    if len(digits) < 6 or len(digits) > 10:
        return None

    hs_qs = HSCode.objects.filter(is_active=True)
    variants = _hs_code_candidates(value)
    exact = hs_qs.filter(code__in=variants).first()
    if exact:
        return exact

    candidates = list(
        hs_qs.filter(code__startswith=digits[:4])
        .only('id', 'code', 'description', 'duty_rate', 'chapter')
    )
    if not candidates:
        return None

    exact_digit_matches = [hs for hs in candidates if _hs_code_digits(hs.code) == digits]
    if exact_digit_matches:
        return exact_digit_matches[0]

    if len(digits) >= 8:
        same_subheading = [
            hs for hs in candidates
            if _hs_code_digits(hs.code).startswith(digits[:8])
        ]
        if same_subheading:
            same_subheading.sort(
                key=lambda hs: (
                    0 if _hs_code_digits(hs.code).endswith('00') else 1,
                    len(_hs_code_digits(hs.code)),
                    hs.code,
                )
            )
            return same_subheading[0]

    same_heading = [
        hs for hs in candidates
        if _hs_code_digits(hs.code).startswith(digits[:6])
    ]
    if same_heading:
        same_heading.sort(key=lambda hs: (len(_hs_code_digits(hs.code)), hs.code))
        return same_heading[0]
    return None


def extract_document_hs_codes(text):
    """Return unique HS-like codes explicitly printed in OCR text."""
    if not text:
        return []
    patterns = [
        r'\bH\.?\s*S\.?\s*(?:CODE|NO\.?|NUMBER)?\s*[:\-]?\s*([0-9][0-9\s.]{5,18})',
        r'\b(?:HTS|AHTN|TARIFF(?:\s+CODE)?|CUSTOMS\s+TARIFF(?:\s+NO\.?)?)\s*[:\-]?\s*([0-9][0-9\s.]{5,18})',
        r'\b(\d{4}[.]\d{2}[.]\d{2}(?:[.]\d{2})?)\b',
        r'\b(\d{8}|\d{10})\b',
    ]
    found = []
    for pattern in patterns:
        for raw in re.findall(pattern, text, re.IGNORECASE):
            digits = _hs_code_digits(raw)
            if len(digits) in (6, 8, 10):
                found.append(raw)

    unique = []
    seen = set()
    for raw in found:
        digits = _hs_code_digits(raw)
        if digits not in seen:
            seen.add(digits)
            unique.append(raw)
    return unique


def suggest_hs_codes(text, top_n=5):
    """
    Two-layer HS code recommendation engine.

    Layer 1 (Rule-based): DB-level OR prefilter on description keywords to
    narrow candidates from 9,268 rows to a small working set, then Python
    scoring with a minimum threshold of 2 matching keywords.

    Layer 2 (Historical): previously confirmed ShipmentHSCode assignments
    each contribute +0.5 to the score for that HS code.

    Returns up to top_n HSCode objects, ranked highest first.
    """
    if not text or not text.strip():
        return []

    unique_keywords = _hs_keyword_tokens(text)

    # Need at least 1 keyword. For single-word searches (e.g. “incubator”),
    # do a direct icontains match and return the best results.
    if not unique_keywords:
        return []

    query_keywords = list(unique_keywords)
    source_words = set(unique_keywords)
    for required_terms, target_terms in _HS_PHRASE_BOOSTS:
        if all(term in source_words for term in required_terms):
            for term in target_terms:
                for raw in re.findall(r'[a-zA-Z]{3,}', term):
                    token = _hs_normalize_word(raw)
                    if token not in query_keywords and token not in _HS_STOPWORDS:
                        query_keywords.append(token)

    # Layer 1 — DB-level OR prefilter (avoids loading all 9,268 rows per call)
    q = Q()
    for kw in query_keywords[:18]:   # cap keyword count to keep query manageable
        q |= Q(description__icontains=kw)

    candidates = list(
        HSCode.objects.filter(q, is_active=True)
        .only('id', 'description', 'code', 'duty_rate', 'chapter')
    )
    if not candidates:
        return []

    # Score candidates in Python.
    # Note: AHTN descriptions are often very short ("Sunglasses", "Centrifuges"),
    # so requiring ≥2 hits would filter out many valid matches.
    # Minimum threshold = 1; higher scores naturally rank better matches first.
    scored = []
    for hs in candidates:
        hs_text = (hs.description or '').lower()
        hs_words = set(_hs_keyword_tokens(hs_text))
        score = 0.0
        for required_terms, target_terms in _HS_PHRASE_BOOSTS:
            if all(term in source_words for term in required_terms):
                if any(term in hs_text for term in target_terms):
                    score += 3.0
        if {'circuit', 'board'}.issubset(source_words) or {'printed', 'circuit'}.issubset(source_words):
            if hs_text.startswith('printed circuit'):
                score += 8.0
            if 'manufacture of printed circuit' in hs_text:
                score -= 4.0
        if {'cable'}.issubset(source_words) or {'wire'}.issubset(source_words):
            if hs_text.startswith('electric conductor') or hs_text.startswith('insulated wire'):
                score += 5.0
        for position, kw in enumerate(unique_keywords):
            hit = False
            if kw in hs_words:
                score += 2.0
                hit = True
            elif len(kw) >= 5 and kw in hs_text:
                score += 1.0
                hit = True
            if hit and position < 3:
                score += 0.5
        if score >= 1:
            scored.append([hs, score])

    if not scored:
        return []

    # Layer 2 — historical boost from confirmed past assignments
    hist = dict(
        ShipmentHSCode.objects
        .filter(is_confirmed=True)
        .values('hs_code_id')
        .annotate(n=Count('id'))
        .values_list('hs_code_id', 'n')
    )
    if hist:
        for entry in scored:
            entry[1] += min(hist.get(entry[0].id, 0) * 0.5, 3.0)

    scored.sort(key=lambda x: x[1], reverse=True)
    return [hs for hs, _ in scored[:top_n]]


# ─── HS Code Suggest (AJAX) ───────────────────────────────────────────────────

def _hs_payload(hs, source):
    return {
        'id': hs.id,
        'code': hs.code,
        'description': hs.description,
        'duty_rate': float(hs.duty_rate),
        'source': source,
    }


def _invoice_ocr_description(shipment):
    doc = shipment.documents.filter(document_type='invoice', ocr_ran_at__isnull=False).order_by('-ocr_ran_at').first()
    if not doc:
        return '', ''

    description_parts = []
    if doc.ocr_fields_json:
        try:
            fields = json.loads(doc.ocr_fields_json)
        except (TypeError, ValueError):
            fields = {}
        desc = fields.get('description')
        if isinstance(desc, dict) and desc.get('value'):
            description_parts.append(str(desc['value']))
        items = fields.get('__items__')
        if isinstance(items, list):
            for item in items:
                if item.get('description'):
                    description_parts.append(str(item['description']))
    return ' '.join(description_parts).strip(), doc.ocr_text or ''


@login_required
def hs_suggestions(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        return JsonResponse({'error': 'Access denied.'}, status=403)

    q = request.GET.get('q', '').strip()
    if q:
        direct = find_hs_by_document_code(q)
        if direct:
            return JsonResponse([_hs_payload(direct, 'suggested')], safe=False)
        results = HSCode.objects.filter(
            Q(code__icontains=q) | Q(description__icontains=q),
            is_active=True,
        )[:10]
        return JsonResponse([_hs_payload(hs, 'suggested') for hs in results], safe=False)

    description, raw_text = _invoice_ocr_description(shipment)
    rows = []
    seen = set()

    direct_codes = extract_document_hs_codes(raw_text or '')
    for code in direct_codes:
        hs = find_hs_by_document_code(code)
        if hs and hs.id not in seen:
            rows.append(_hs_payload(hs, 'document'))
            seen.add(hs.id)
        if len(rows) >= 5:
            return JsonResponse(rows, safe=False)

    words = [
        word for word in re.findall(r'[A-Za-z]{3,}', description.lower())
        if word not in _HS_STOPWORDS
    ]
    scored = {}
    for word in words:
        for hs in HSCode.objects.filter(description__icontains=word, is_active=True)[:80]:
            scored.setdefault(hs.id, [hs, 0])
            scored[hs.id][1] += 1

    for hs, _score in sorted(scored.values(), key=lambda item: item[1], reverse=True):
        if hs.id in seen:
            continue
        rows.append(_hs_payload(hs, 'suggested'))
        seen.add(hs.id)
        if len(rows) >= 5:
            break

    return JsonResponse(rows, safe=False)


@login_required
def confirm_hs_code(request, shipment_id):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required.'}, status=405)

    shipment = get_object_or_404(Shipment, id=shipment_id)
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        return JsonResponse({'ok': False, 'error': 'Access denied.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (TypeError, ValueError):
        payload = request.POST
    hs_code_value = str(payload.get('code') or '').strip()
    hs_code_id = payload.get('id')
    hs_qs = HSCode.objects.filter(is_active=True)
    hs = hs_qs.filter(id=hs_code_id).first() if hs_code_id else None
    if not hs and hs_code_value:
        hs = hs_qs.filter(code=hs_code_value).first()
    if not hs:
        return JsonResponse({'ok': False, 'error': 'HS code not found.'}, status=404)

    rel, _created = ShipmentHSCode.objects.get_or_create(
        shipment=shipment,
        hs_code=hs,
        defaults={'is_suggested': True, 'is_confirmed': True},
    )
    if not rel.is_confirmed:
        rel.is_confirmed = True
        rel.is_suggested = True
        rel.save(update_fields=['is_confirmed', 'is_suggested'])
    return JsonResponse({'ok': True})


@login_required
def hs_code_suggest(request):
    """
    AJAX endpoint for per-row live suggestions.
    GET ?q=<item description>&doc_hs=<HS from document>&context=<OCR text>&limit=<n>

    Priority order:
    1. doc_hs  — HS code explicitly printed in the invoice/packing list (highest confidence).
                 Looked up directly in the DB and returned as the first result.
    2. q alone — keyword search on item description.
    3. q + context — enriched search using full OCR raw text when q gives < 2 results.
    """
    try:
        q       = request.GET.get('q', '').strip()
        doc_hs  = request.GET.get('doc_hs', '').strip()
        context = request.GET.get('context', '').strip()[:3000]  # raw OCR text — expanded cap
        limit   = min(int(request.GET.get('limit', 5) or 5), 10)

        seen_ids = set()
        pinned   = []

        # ── Priority 1: HS code explicitly printed in the document ────────────
        if doc_hs:
            hs_obj = find_hs_by_document_code(doc_hs)
            if hs_obj:
                pinned.append({
                    'id':      hs_obj.id,
                    'code':    hs_obj.code,
                    'desc':    hs_obj.description[:80],
                    'rate':    float(hs_obj.duty_rate),
                    'chapter': hs_obj.chapter or '',
                    'source':  'document',
                })
                seen_ids.add(hs_obj.id)

        # ── Priority 2: OCR raw text as PRIMARY classification source ──────────
        # When raw OCR text is available it is far richer than a short extracted
        # item description.  Run the suggestion engine against the full OCR text
        # first; then re-rank results by how well they also match the typed
        # description (q), so description-specific terms bubble to the top.
        if context:
            # Combine: OCR text carries the product vocabulary; q refines it
            search_text = (context + ' ' + q).strip() if q else context
            kw_results = suggest_hs_codes(search_text, top_n=limit * 2)

            # Re-rank: items whose descriptions also match q get priority
            if q and kw_results:
                q_keywords = _hs_keyword_tokens(q)
                if q_keywords:
                    def _desc_hits(hs):
                        words = set(_hs_keyword_tokens(hs.description))
                        return sum(1 for kw in q_keywords if kw in words)
                    kw_results = sorted(kw_results, key=_desc_hits, reverse=True)

            kw_results = kw_results[:limit]

            # Fallback: if OCR context produced nothing, try description alone
            if not kw_results and q:
                kw_results = suggest_hs_codes(q, top_n=limit)
        else:
            # No OCR context — use typed description only (original behaviour)
            kw_results = suggest_hs_codes(q, top_n=limit)

        remaining_slots = limit - len(pinned)
        extra = [
            {
                'id':      hs.id,
                'code':    hs.code,
                'desc':    hs.description[:80],
                'rate':    float(hs.duty_rate),
                'chapter': hs.chapter or '',
                'source':  'suggested',
            }
            for hs in kw_results
            if hs.id not in seen_ids
        ][:remaining_slots]

        return JsonResponse({'suggestions': pinned + extra})

    except Exception as e:
        logger.warning('hs_code_suggest error: %s', e)
        return JsonResponse({'suggestions': [], 'error': str(e)})


# ─── Update ShipmentLineItem HS Code (AJAX PATCH) ────────────────────────────

@login_required
def update_line_item_hs(request, item_id):
    """
    PATCH /computation/line-item/<id>/hs/
    Body: { hs_code_id: <int> }
    Updates the hs_code FK on a ShipmentLineItem and returns the duty_rate.
    Only the assigned declarant may call this.
    """
    if request.method not in ('POST', 'PATCH'):
        return JsonResponse({'ok': False, 'error': 'POST/PATCH required.'}, status=405)

    item = get_object_or_404(ShipmentLineItem, id=item_id)
    shipment = item.shipment

    if request.user.role != 'declarant' or shipment.declarant != request.user:
        return JsonResponse({'ok': False, 'error': 'Access denied.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except (TypeError, ValueError):
        payload = request.POST

    hs_code_id = payload.get('hs_code_id')
    if not hs_code_id:
        return JsonResponse({'ok': False, 'error': 'hs_code_id required.'}, status=400)

    try:
        hs = HSCode.objects.get(id=int(hs_code_id), is_active=True)
    except (HSCode.DoesNotExist, ValueError):
        return JsonResponse({'ok': False, 'error': 'HS code not found.'}, status=404)

    item.hs_code     = hs
    item.is_confirmed = True
    item.save(update_fields=['hs_code', 'is_confirmed', 'updated_at'])

    # Record the confirmation for historical boost
    ShipmentHSCode.objects.get_or_create(
        shipment=shipment, hs_code=hs,
        defaults={'is_suggested': True, 'is_confirmed': True},
    )

    return JsonResponse({
        'ok':       True,
        'hs_code':  hs.code,
        'duty_rate': float(hs.duty_rate),
    })


# ─── HS Code Search ───────────────────────────────────────────────────────────

@login_required
def hs_code_search(request):
    query   = request.GET.get('q', '')
    results = []
    if query:
        from django.db.models import Q
        results = HSCode.objects.filter(
            Q(code__icontains=query) | Q(description__icontains=query),
            is_active=True
        )[:10]
    return render(request, 'computation/hs_search.html', {
        'query': query, 'results': results,
    })


# ─── Graduated WMCDA ─────────────────────────────────────────────────────────

