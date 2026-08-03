import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment
from apps.supervisor.models import SystemConfig
from apps.supervisor.audit import log_audit
from ..models import ShippingAdvisory
from ..wmcda import load_wmcda_weights, wmcda_weight_rows

logger = logging.getLogger('r3pcr.computation')


def _lerp(x, x0, x1, y0, y1):
    if x <= x0: return y0
    if x >= x1: return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _get_freight_rates():
    """Load configurable freight rates from SystemConfig.
    Returns dict with rates per mode: air_rate_per_kg, lcl_rate_per_kg,
    fcl_base_20ft, fcl_base_40ft, fcl_rate_per_kg."""
    def _cfg(key, default):
        raw = SystemConfig.get(key, str(default))
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)
    return {
        'air_rate_per_kg':  _cfg('mcda_air_rate_per_kg', 180),
        'lcl_rate_per_kg':  _cfg('mcda_lcl_rate_per_kg', 15),
        'lcl_rate_per_cbm': _cfg('mcda_lcl_rate_per_cbm', 8000),
        'lcl_min_charge':   _cfg('mcda_lcl_min_charge', 3000),
        'fcl_base_20ft':    _cfg('mcda_fcl_base_20ft', 45000),
        'fcl_base_40ft':    _cfg('mcda_fcl_base_40ft', 75000),
        'fcl_rate_per_kg':  _cfg('mcda_fcl_rate_per_kg', 8),
        'air_transit_days': _cfg('mcda_air_transit_days', 5),
        'sea_transit_days': _cfg('mcda_sea_transit_days', 21),
        'air_max_weight':   _cfg('mcda_air_max_weight', 500),
        'fcl_min_volume':   _cfg('mcda_fcl_min_volume', 8),
    }


def compute_wmcda(weight, volume, value, urgency, distance):
    urgency_factor = {
        'standard': 0.0,
        'normal': 0.0,
        'priority': 0.5,
        'urgent': 1.0,
        'rush': 1.3,
    }.get(urgency, 0.0)
    is_time_critical = urgency in ('urgent', 'rush')
    urgency_label = {
        'standard': 'standard',
        'normal': 'standard',
        'priority': 'priority',
        'urgent': 'urgent',
        'rush': 'rush',
    }.get(urgency, urgency)

    rates = _get_freight_rates()

    # ── Cost scoring: based on actual freight cost comparison ──────────────────
    # Compute estimated freight cost per mode (PHP), then normalize.
    # LCL: greater of (weight × per-kg rate) or (volume × per-CBM rate), with minimum
    # Air: chargeable weight (actual vs dimensional) × per-kg rate
    # FCL: fixed container base + per-kg surcharge for heavy cargo
    chargeable_weight = weight
    if volume > 0:
        vol_weight = volume * 167  # dimensional weight for air
        chargeable_weight = max(weight, vol_weight)

    air_cost_php = chargeable_weight * rates['air_rate_per_kg']

    lcl_by_weight = weight * rates['lcl_rate_per_kg']
    lcl_by_volume = (volume or 0) * rates['lcl_rate_per_cbm']
    lcl_cost_php = max(lcl_by_weight, lcl_by_volume, rates['lcl_min_charge'])

    fcl_container = rates['fcl_base_20ft'] if (volume or 0) <= 20 else rates['fcl_base_40ft']
    fcl_cost_php = fcl_container + max(0, weight - 1000) * rates['fcl_rate_per_kg']

    costs = [air_cost_php, lcl_cost_php, fcl_cost_php]
    min_cost = min(costs)
    max_cost = max(costs) if max(costs) > 0 else 1
    cost_range = max_cost - min_cost if max_cost > min_cost else 1
    air_cost = round(0.9 - 0.7 * (air_cost_php - min_cost) / cost_range, 3)
    lcl_cost = round(0.9 - 0.7 * (lcl_cost_php - min_cost) / cost_range, 3)
    fcl_cost = round(0.9 - 0.7 * (fcl_cost_php - min_cost) / cost_range, 3)

    # ── Time scoring: based on transit days + urgency factor ──────────────────
    # At normal urgency, time differences are small (cost matters more).
    # At urgent/rush, time gap widens to strongly favor Air.
    air_days = rates['air_transit_days']
    sea_days = rates['sea_transit_days']
    sea_days_adj = sea_days * max(1.0, distance / 3000)
    air_days_adj = air_days * max(1.0, distance / 8000)

    # Base scores: narrow gap at normal urgency (both modes "acceptable")
    base_air_time = 0.70
    base_lcl_time = 0.55
    base_fcl_time = 0.58
    # Distance penalty: sea gets worse at long distances
    if sea_days_adj > 0:
        dist_penalty = min(0.15, (distance - 2000) / 20000 * 0.15) if distance > 2000 else 0
        base_lcl_time = max(0.40, base_lcl_time - dist_penalty)
        base_fcl_time = max(0.43, base_fcl_time - dist_penalty)

    # Urgency widens the gap: Air gains, sea modes lose
    air_time = min(0.99, base_air_time + 0.20 * urgency_factor)
    lcl_time = max(0.15, base_lcl_time - 0.22 * urgency_factor)
    fcl_time = max(0.20, base_fcl_time - 0.18 * urgency_factor)

    # ── Cargo suitability scoring ─────────────────────────────────────────────
    # Air: excellent for light cargo, degrades for heavy
    air_weight_score = max(0.10, _lerp(weight, 0, rates['air_max_weight'], 0.95, 0.15))
    # LCL: good for small-to-medium, drops off for heavy cargo
    lcl_weight_score = _lerp(weight, 0, 1500, 0.88, 0.35)
    # FCL: good for heavy/bulky
    fcl_weight_score = _lerp(weight, 0, 1500, 0.20, 0.88)

    if volume > 0:
        air_vol = max(0.10, _lerp(volume, 0, 3, 0.90, 0.10))
        # LCL loses suitability above ~5 CBM; FCL gains above ~3 CBM
        lcl_vol = _lerp(volume, 0, 6, 0.88, 0.25)
        fcl_vol = _lerp(volume, 0, 5, 0.15, 0.92)
        air_weight_final = round(0.5 * air_weight_score + 0.5 * air_vol, 3)
        lcl_weight_final = round(0.5 * lcl_weight_score + 0.5 * lcl_vol, 3)
        fcl_weight_final = round(0.5 * fcl_weight_score + 0.5 * fcl_vol, 3)
    else:
        air_weight_final = round(air_weight_score, 3)
        lcl_weight_final = round(lcl_weight_score, 3)
        fcl_weight_final = round(fcl_weight_score, 3)

    # ── Distance scoring ──────────────────────────────────────────────────────
    # Air gains advantage at longer distances (less delay proportionally)
    distance_max = 20000
    air_distance = min(0.95, _lerp(distance, 0, distance_max, 0.55, 0.95))
    lcl_distance = max(0.35, _lerp(distance, 0, distance_max, 0.75, 0.45))
    fcl_distance = max(0.40, _lerp(distance, 0, distance_max, 0.70, 0.55))

    try:
        _, weights = load_wmcda_weights(SystemConfig.get)
        w_cost = weights['cost']
        w_time = weights['time']
        w_weight = weights['weight']
        w_dist = weights['distance']
    except Exception:
        w_cost, w_time, w_weight, w_dist = 0.35, 0.30, 0.20, 0.15

    def tws(cost, time, cargo, dist):
        return round(cost * w_cost + time * w_time + cargo * w_weight + dist * w_dist, 4)

    scores = {
        'lcl': tws(lcl_cost, lcl_time, lcl_weight_final, lcl_distance),
        'fcl': tws(fcl_cost, fcl_time, fcl_weight_final, fcl_distance),
        'air': tws(air_cost, air_time, air_weight_final, air_distance),
    }
    recommended = max(scores, key=scores.get)

    breakdown = {
        'lcl': {'cost': round(lcl_cost, 3), 'time': round(lcl_time, 3), 'weight': round(lcl_weight_final, 3), 'distance': round(lcl_distance, 3)},
        'fcl': {'cost': round(fcl_cost, 3), 'time': round(fcl_time, 3), 'weight': round(fcl_weight_final, 3), 'distance': round(fcl_distance, 3)},
        'air': {'cost': round(air_cost, 3), 'time': round(air_time, 3), 'weight': round(air_weight_final, 3), 'distance': round(air_distance, 3)},
    }

    weight_label = f'{weight:.0f} kg'
    value_label = f'${value:,.0f}'
    vol_label = f'{volume:.2f} CBM' if volume > 0 else ''
    cargo_desc = f'{weight_label}{", " + vol_label if vol_label else ""}'

    explanations = {
        'lcl': (
            f'LCL is cost-efficient for small-to-moderate cargo ({cargo_desc}). '
            f'{"Sea transit may conflict with " + urgency_label + " urgency." if is_time_critical else "Suitable transit time for this urgency level."}'
        ),
        'fcl': (
            f'FCL is recommended for large or heavy cargo. '
            f'{"Volume of " + vol_label + " justifies a dedicated container. " if volume > 10 else ""}'
            f'{"Cargo of " + cargo_desc + " and value of " + value_label + " justify the container cost." if value > 10000 or weight > 500 else "May underutilize a full container for this cargo size."}'
            f'{" Sea transit may be too slow for " + urgency_label + " urgency." if is_time_critical else ""}'
        ),
        'air': (
            f'{"Rush urgency makes Air Freight the fastest practical option. " if urgency == "rush" else ""}'
            f'{"Urgency requires faster transit. " if urgency == "urgent" else ""}'
            f'{"Air Freight is ideal for priority delivery at " + value_label + ". " if urgency == "priority" else ""}'
            f'{"Air Freight offers speed and security for high-value goods at " + value_label + "." if value > 10000 and not is_time_critical else ""}'
            f'{"Air Freight is competitive for this shipment profile." if not is_time_critical and value <= 10000 else ""}'
        ),
    }
    return scores, recommended, breakdown, explanations.get(recommended, '')


# Shipping Advisory (auto-populated)
@login_required
def shipping_advisory(request, shipment_id):
    shipment = get_object_or_404(Shipment, id=shipment_id)

    # Only the assigned declarant may access the shipping advisory
    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    existing = ShippingAdvisory.objects.filter(shipment=shipment).first()
    result = breakdown = explanation = None
    scores = None

    # ── Auto-populate from shipment + computation data ──
    computation = getattr(shipment, 'computation', None)

    if existing:
        auto_weight   = float(existing.gross_weight)
        auto_volume   = float(existing.cargo_volume)
        auto_value    = float(existing.declared_value)
        auto_urgency  = existing.urgency_level
        auto_distance = float(existing.distance_km)

        # Re-derive breakdown and explanation from saved inputs so the criterion
        # table is visible on every page load, not just immediately after a POST.
        try:
            scores, result, breakdown, explanation = compute_wmcda(
                auto_weight, auto_volume, auto_value, auto_urgency, auto_distance
            )
        except Exception as e:
            logger.debug('MCDA breakdown re-derive failed: %s', e)
    else:
        # Pull weight from shipment model field
        auto_weight = float(shipment.gross_weight) if shipment.gross_weight else 0.0
        # Pull declared value from computation or shipment in the invoice currency.
        if computation and computation.declared_value:
            auto_value = float(computation.declared_value)
        elif shipment.declared_value:
            auto_value = float(shipment.declared_value)
        else:
            auto_value = 0.0
        auto_volume   = 0.0
        auto_urgency  = shipment.urgency
        auto_distance = 2600.0  # Default: Incheon, Korea → Manila, Philippines

    # Determine which fields were auto-populated vs missing
    missing_fields = []
    if not auto_weight:
        missing_fields.append('Gross Weight (kg)')
    if not auto_value:
        missing_fields.append(f'Declared Value ({shipment.invoice_currency or "USD"})')

    auto_data = {
        'gross_weight':   auto_weight,
        'cargo_volume':   auto_volume,
        'declared_value': auto_value,
        'urgency_level':  auto_urgency,
        'distance_km':    auto_distance,
    }
    auto_sources = {
        'gross_weight':   'shipment' if (not existing and shipment.gross_weight) else ('advisory' if existing else 'manual'),
        'declared_value': 'computation' if (not existing and computation and computation.declared_value) else ('advisory' if existing else 'manual'),
        'urgency_level':  'shipment' if not existing else 'advisory',
        'distance_km':    'default' if not existing else 'advisory',
        'cargo_volume':   'advisory' if existing else 'manual',
    }

    if request.method == 'POST':
        try:
            weight   = float(request.POST.get('gross_weight', 0))
            volume   = float(request.POST.get('cargo_volume', 0))
            value    = float(request.POST.get('declared_value', 0))
            urgency  = request.POST.get('urgency_level', 'normal')
            distance = float(request.POST.get('distance_km', 2600))

            scores, recommended, breakdown, explanation = compute_wmcda(
                weight, volume, value, urgency, distance
            )

            ShippingAdvisory.objects.update_or_create(
                shipment=shipment,
                defaults={
                    'gross_weight':     weight,
                    'cargo_volume':     volume,
                    'declared_value':   value,
                    'urgency_level':    urgency,
                    'distance_km':      distance,
                    'lcl_score':        scores['lcl'],
                    'fcl_score':        scores['fcl'],
                    'air_score':        scores['air'],
                    'recommended_type': recommended,
                    'computed_by':      request.user,
                }
            )
            log_audit(
                'advisory_generate',
                f'Shipping Type Advisory generated for {shipment.hawb_number}.',
                request=request,
                shipment=shipment,
                target=shipment,
                details={
                    'recommended_type': recommended,
                    'lcl_score': scores['lcl'],
                    'fcl_score': scores['fcl'],
                    'air_score': scores['air'],
                },
            )
            result = recommended
            messages.success(request, f'Recommendation: {recommended.upper()}')

            # Notify consignee of the advisory result
            try:
                from apps.notifications.utils import create_notification
                label_map = {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL'}
                create_notification(
                    recipient=shipment.consignee,
                    shipment=shipment,
                    notification_type='status_update',
                    title=f'Shipping Advisory Ready — {shipment.hawb_number}',
                    message=(
                        f'MCDA Recommendation: {label_map.get(recommended, recommended.upper())}. '
                        f'{explanation[:120] if explanation else ""}'
                    ),
                )
            except Exception as e:
                logger.debug('Advisory-ready notification failed: %s', e)

        except Exception as e:
            messages.error(request, f'Error: {e}')

    # ── Historical advisory counts (same shipment type as this shipment) ───────
    wmcda_history = None
    if shipment.shipment_type:
        from collections import Counter
        past = list(
            ShippingAdvisory.objects
            .filter(
                shipment__shipment_type=shipment.shipment_type,
                recommended_type__isnull=False,
            )
            .exclude(shipment=shipment)
            .values_list('recommended_type', flat=True)
        )
        if past:
            counts   = Counter(past)
            top_mode = counts.most_common(1)[0]
            pct      = round(top_mode[1] / len(past) * 100)
            _label_map = {
                'air':  'Air Freight',
                'lcl':  'LCL',
                'fcl':  'FCL',
            }
            wmcda_history = {
                'total':      len(past),
                'top_mode':   top_mode[0],
                'top_pct':    pct,
                'mode_label': _label_map.get(top_mode[0], top_mode[0].upper()),
                'ship_type':  shipment.get_shipment_type_display(),
                'counts':     {k: counts.get(k, 0) for k in ('air', 'lcl', 'fcl')},
            }

    context = {
        'shipment':       shipment,
        'existing':       existing,
        'result':         result,
        'scores':         scores,
        'breakdown':      breakdown,
        'explanation':    explanation,
        'auto_data':      auto_data,
        'auto_sources':   auto_sources,
        'missing_fields': missing_fields,
        'wmcda_history':  wmcda_history,
        'wmcda_weights':  wmcda_weight_rows(SystemConfig.get),
    }
    return render(request, 'computation/advisory.html', context)


# ─── Save Declarant Advisory ──────────────────────────────────────────────────

@login_required
def save_declarant_advisory(request, shipment_id):
    if request.method != 'POST':
        return redirect('computation:advisory', shipment_id=shipment_id)

    shipment = get_object_or_404(Shipment, id=shipment_id)

    if request.user.role != 'declarant' or shipment.declarant != request.user:
        messages.error(request, 'Access denied.')
        return redirect('declarant:queue')

    advisory = ShippingAdvisory.objects.filter(shipment=shipment).first()
    if not advisory:
        messages.error(request, 'Run the MCDA computation first before saving an advisory.')
        return redirect('computation:advisory', shipment_id=shipment_id)

    recommendation = request.POST.get('declarant_recommendation', '').strip()
    note = request.POST.get('declarant_note', '').strip()

    valid_types = {'air', 'lcl', 'fcl', ''}
    if recommendation not in valid_types:
        messages.error(request, 'Invalid shipping type selected.')
        return redirect('computation:advisory', shipment_id=shipment_id)

    advisory.declarant_recommendation = recommendation or None
    advisory.declarant_note = note or None
    advisory.save(update_fields=['declarant_recommendation', 'declarant_note'])
    log_audit(
        'advisory_update',
        f'Declarant advisory updated for {shipment.hawb_number}.',
        request=request,
        shipment=shipment,
        target=advisory,
        details={'declarant_recommendation': recommendation or None, 'has_note': bool(note)},
    )

    if recommendation:
        label_map = {'air': 'Air Freight', 'lcl': 'LCL', 'fcl': 'FCL'}
        mode_label = label_map.get(recommendation, recommendation.upper())
        try:
            from apps.notifications.utils import create_notification
            create_notification(
                recipient=shipment.consignee,
                shipment=shipment,
                notification_type='status_update',
                title=f'Declarant Advisory — {shipment.hawb_number}',
                message=(
                    f'Your declarant recommends {mode_label} for your shipment. '
                    f'{note}' if note else f'Your declarant recommends {mode_label} for your shipment.'
                ),
            )
        except Exception as e:
            logger.debug('Declarant-advisory notification failed: %s', e)
        messages.success(request, f'Advisory saved — {mode_label} recommended to consignee.')
    else:
        messages.success(request, 'Declarant advisory cleared.')

    return redirect('computation:advisory', shipment_id=shipment_id)
