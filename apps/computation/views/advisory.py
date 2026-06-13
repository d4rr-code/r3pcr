import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from apps.shipments.models import Shipment
from apps.supervisor.models import SystemConfig
from ..models import ShippingAdvisory

logger = logging.getLogger('r3pcr.computation')

def _lerp(x, x0, x1, y0, y1):
    if x <= x0: return y0
    if x >= x1: return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


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

    if volume > 0:
        lcl_cost = max(0.20, _lerp(volume, 0, 15, 0.92, 0.28))
        fcl_cost = min(0.95, _lerp(volume, 0, 15, 0.22, 0.90))
    else:
        lcl_cost = max(0.25, _lerp(weight, 0, 1000, 0.88, 0.35))
        fcl_cost = _lerp(value, 0, 30000, 0.30, 0.88)
    air_cost = max(0.15, _lerp(weight, 0, 500, 0.55, 0.18))

    base_lcl_time = max(0.30, _lerp(distance, 0, 2000, 0.72, 0.50))
    base_fcl_time = max(0.35, _lerp(distance, 0, 2000, 0.78, 0.55))
    base_air_time = 0.62
    lcl_time = max(0.20, base_lcl_time - 0.37 * urgency_factor)
    fcl_time = max(0.25, base_fcl_time - 0.30 * urgency_factor)
    air_time = min(0.99, base_air_time + 0.34 * urgency_factor)

    lcl_weight_component = _lerp(weight, 0, 2000, 0.92, 0.28)
    fcl_weight_component = _lerp(weight, 0, 2000, 0.18, 0.95)
    air_weight_component = max(0.10, _lerp(weight, 0, 300, 0.95, 0.15))
    if volume > 0:
        lcl_volume_component = max(0.15, _lerp(volume, 0, 15, 0.95, 0.18))
        fcl_volume_component = min(0.95, _lerp(volume, 0, 15, 0.18, 0.95))
        air_volume_component = max(0.10, _lerp(volume, 0, 3, 0.95, 0.10))
        lcl_weight = round(0.55 * lcl_weight_component + 0.45 * lcl_volume_component, 3)
        fcl_weight = round(0.55 * fcl_weight_component + 0.45 * fcl_volume_component, 3)
        air_weight = round(0.55 * air_weight_component + 0.45 * air_volume_component, 3)
    else:
        lcl_weight = lcl_weight_component
        fcl_weight = fcl_weight_component
        air_weight = air_weight_component

    distance_max = 20000
    lcl_distance = max(0.45, _lerp(distance, 0, distance_max, 0.72, 0.80))
    fcl_distance = min(0.95, _lerp(distance, 0, distance_max, 0.55, 0.92))
    air_distance = min(0.95, _lerp(distance, 0, distance_max, 0.60, 0.95))

    try:
        w_cost = float(SystemConfig.get('wmcda_w_cost', '35')) / 100
        w_time = float(SystemConfig.get('wmcda_w_time', '30')) / 100
        w_weight = float(SystemConfig.get('wmcda_w_weight', '20')) / 100
        w_dist = float(SystemConfig.get('wmcda_w_distance', '15')) / 100
    except Exception:
        w_cost, w_time, w_weight, w_dist = 0.35, 0.30, 0.20, 0.15

    def tws(cost, time, cargo, dist):
        return round(cost * w_cost + time * w_time + cargo * w_weight + dist * w_dist, 4)

    scores = {
        'lcl': tws(lcl_cost, lcl_time, lcl_weight, lcl_distance),
        'fcl': tws(fcl_cost, fcl_time, fcl_weight, fcl_distance),
        'air': tws(air_cost, air_time, air_weight, air_distance),
    }
    recommended = max(scores, key=scores.get)

    breakdown = {
        'lcl': {'cost': round(lcl_cost, 3), 'time': round(lcl_time, 3), 'weight': round(lcl_weight, 3), 'distance': round(lcl_distance, 3)},
        'fcl': {'cost': round(fcl_cost, 3), 'time': round(fcl_time, 3), 'weight': round(fcl_weight, 3), 'distance': round(fcl_distance, 3)},
        'air': {'cost': round(air_cost, 3), 'time': round(air_time, 3), 'weight': round(air_weight, 3), 'distance': round(air_distance, 3)},
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
            f'FCL is optimal for large or heavy cargo. '
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
            logger.debug('WMCDA breakdown re-derive failed: %s', e)
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
                        f'WMCDA Recommendation: {label_map.get(recommended, recommended.upper())}. '
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
        messages.error(request, 'Run the WMCDA computation first before saving an advisory.')
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
