import json
import logging
import os
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, date as date_type
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, Sum, Min, Max, F, ExpressionWrapper, DurationField, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.core.mail import send_mail
from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpResponse
from apps.accounts.models import User
from apps.accounts.views import _validate_phone_number
from apps.shipments.models import Shipment, ShipmentDocument, HSCode, StatusLog, TariffSchedule, HSCodeRate
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback
from apps.notifications.utils import create_notification, notify_shipment_status_change
from apps.supervisor.exchange_rates import ensure_daily_exchange_rates
from ..models import SystemConfig, Announcement, IssueReport

logger = logging.getLogger(__name__)

from .common import *  # noqa: F401,F403

def _feedback_summary():
    """All-time consignee feedback aggregates for the analytics dashboard."""
    fb_qs       = Feedback.objects.all()
    fb_total    = fb_qs.count()
    fb_avg      = fb_qs.aggregate(avg=Avg('rating'))['avg']
    fb_positive = fb_qs.filter(rating__gte=4).count()
    summary = {
        'total':        fb_total,
        'avg_rating':   round(float(fb_avg), 1) if fb_avg else 0,
        'positive':     fb_positive,
        'positive_pct': round(fb_positive / fb_total * 100, 1) if fb_total else 0,
    }
    summary['filled_stars'] = int(round(summary['avg_rating'])) if fb_total else 0
    summary['star_rows'] = [
        {'value': i, 'filled': i <= summary['filled_stars']}
        for i in range(1, 6)
    ]
    return summary


def _shipment_type_counts(all_shipments):
    """All-time shipment counts per transport mode."""
    return {
        'air':  all_shipments.filter(shipment_type='air').count(),
        'lcl':  all_shipments.filter(shipment_type='lcl').count(),
        'fcl':  all_shipments.filter(shipment_type='fcl').count(),
    }


def _currency_breakdown(chart_ids):
    """Invoice-currency usage breakdown (respects chart filters via chart_ids)."""
    cur_colors = {
        'USD': '#3B82F6', 'EUR': '#8B5CF6', 'JPY': '#F59E0B',
        'HKD': '#EC4899', 'CNY': '#EF4444', 'GBP': '#14B8A6', 'SGD': '#22C55E',
    }
    cur_qs = (
        Shipment.objects.filter(id__in=chart_ids)
        .exclude(invoice_currency='')
        .values('invoice_currency')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    currency_total = sum(r['count'] for r in cur_qs)
    currency_breakdown = [
        {
            'code':  r['invoice_currency'] or 'USD',
            'count': r['count'],
            'pct':   round(r['count'] / currency_total * 100, 1) if currency_total else 0,
            'color': cur_colors.get(r['invoice_currency'] or 'USD', '#94A3B8'),
        }
        for r in cur_qs
    ]
    return {
        'currency_breakdown':    currency_breakdown,
        'currency_total':        currency_total,
        'currency_chart_labels': json.dumps([r['code']  for r in currency_breakdown]),
        'currency_chart_data':   json.dumps([r['count'] for r in currency_breakdown]),
        'currency_chart_colors': json.dumps([r['color'] for r in currency_breakdown]),
    }


def _cost_by_type(date_from, date_to, declarant_filter):
    """Average/total landed cost per shipment mode (respects date + declarant filters)."""
    cost_qs = DutyComputation.objects.filter(total_landed_cost__isnull=False)
    if date_from:
        cost_qs = cost_qs.filter(shipment__submitted_at__date__gte=date_from)
    if date_to:
        cost_qs = cost_qs.filter(shipment__submitted_at__date__lte=date_to)
    if declarant_filter:
        cost_qs = cost_qs.filter(shipment__declarant__username=declarant_filter)

    cost_type_meta = [
        ('air',  'Air',  '#F59E0B'),
        ('lcl',  'LCL',  '#38BDF8'),
        ('fcl',  'FCL',  '#8B5CF6'),
    ]
    cost_by_type = []
    for code, label, color in cost_type_meta:
        agg = cost_qs.filter(shipment__shipment_type=code).aggregate(
            avg=Avg('total_landed_cost'),
            total=Sum('total_landed_cost'),
            count=Count('id'),
            min_val=Min('total_landed_cost'),
            max_val=Max('total_landed_cost'),
        )
        cost_by_type.append({
            'code': code, 'label': label, 'color': color,
            'avg':   round(float(agg['avg'] or 0), 2),
            'total': round(float(agg['total'] or 0), 2),
            'count': agg['count'],
            'min_val': round(float(agg['min_val'] or 0), 2),
            'max_val': round(float(agg['max_val'] or 0), 2),
        })
    return {
        'cost_by_type':    cost_by_type,
        'cost_bar_labels': json.dumps([r['label'] for r in cost_by_type]),
        'cost_bar_data':   json.dumps([r['avg'] for r in cost_by_type]),
        'cost_bar_colors': json.dumps([r['color'] for r in cost_by_type]),
    }


def _estimate_vs_fan(date_from, date_to, declarant_filter):
    """Compare computed ECDT estimates with available FAN/SAD assessment values."""
    comp_qs = DutyComputation.objects.select_related('shipment').filter(
        shipment__documents__document_type='sad',
    ).distinct()
    if date_from:
        comp_qs = comp_qs.filter(shipment__submitted_at__date__gte=date_from)
    if date_to:
        comp_qs = comp_qs.filter(shipment__submitted_at__date__lte=date_to)
    if declarant_filter:
        comp_qs = comp_qs.filter(shipment__declarant__username=declarant_filter)

    fan_docs = {}
    for doc in (
        ShipmentDocument.objects
        .filter(shipment_id__in=comp_qs.values_list('shipment_id', flat=True), document_type='sad')
        .order_by('shipment_id', '-uploaded_at')
    ):
        fan_docs.setdefault(doc.shipment_id, doc)

    def _amount(value):
        raw = re.sub(r'[^0-9.\-]', '', str(value or ''))
        if not raw:
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    def _fan_amount(data, key):
        raw = data.get(key, {}) if isinstance(data, dict) else {}
        if isinstance(raw, dict):
            return _amount(raw.get('value'))
        return _amount(raw)

    totals = {
        'customs_duty': {'estimate': Decimal('0'), 'actual': Decimal('0'), 'count': 0},
        'vat': {'estimate': Decimal('0'), 'actual': Decimal('0'), 'count': 0},
        'total_payable': {'estimate': Decimal('0'), 'actual': Decimal('0'), 'count': 0},
    }
    shipment_rows = []

    for comp in comp_qs:
        doc = fan_docs.get(comp.shipment_id)
        if not doc or not doc.ocr_fields_json:
            continue
        try:
            data = json.loads(doc.ocr_fields_json)
        except (TypeError, ValueError):
            continue

        actual_cud = _fan_amount(data, 'customs_duty')
        actual_vat = _fan_amount(data, 'vat')
        actual_total = _fan_amount(data, 'total_payable')
        if actual_total is None:
            taxes = _fan_amount(data, 'total_taxes')
            fees = _fan_amount(data, 'total_fees')
            if taxes is not None and fees is not None:
                actual_total = taxes + fees

        estimates = {
            'customs_duty': comp.customs_duty,
            'vat': comp.vat_amount,
            'total_payable': comp.boc_payable,
        }
        actuals = {
            'customs_duty': actual_cud,
            'vat': actual_vat,
            'total_payable': actual_total,
        }

        has_actual = False
        for key, actual in actuals.items():
            estimate = estimates.get(key)
            if estimate is None or actual is None:
                continue
            totals[key]['estimate'] += Decimal(estimate)
            totals[key]['actual'] += actual
            totals[key]['count'] += 1
            has_actual = True

        if has_actual and estimates['total_payable'] is not None and actual_total is not None:
            variance = actual_total - Decimal(estimates['total_payable'])
            shipment_rows.append({
                'hawb': comp.shipment.hawb_number,
                'estimate': round(float(estimates['total_payable']), 2),
                'actual': round(float(actual_total), 2),
                'variance': round(float(variance), 2),
                'variance_pct': round(float(variance / actual_total * Decimal('100')), 1) if actual_total else 0,
            })

    metric_meta = [
        ('customs_duty', 'Customs Duty'),
        ('vat', 'VAT'),
        ('total_payable', 'Total Payable'),
    ]
    comparison_rows = []
    for key, label in metric_meta:
        count = totals[key]['count']
        estimate_avg = totals[key]['estimate'] / count if count else Decimal('0')
        actual_avg = totals[key]['actual'] / count if count else Decimal('0')
        diff = actual_avg - estimate_avg
        comparison_rows.append({
            'key': key,
            'label': label,
            'count': count,
            'estimate_avg': round(float(estimate_avg), 2),
            'actual_avg': round(float(actual_avg), 2),
            'diff': round(float(diff), 2),
            'diff_pct': round(float(diff / actual_avg * Decimal('100')), 1) if actual_avg else 0,
        })

    compared_shipments = len(shipment_rows)
    avg_abs_variance_pct = (
        round(sum(abs(r['variance_pct']) for r in shipment_rows) / compared_shipments, 1)
        if compared_shipments else 0
    )
    shipment_rows.sort(key=lambda r: abs(r['variance']), reverse=True)

    return {
        'fan_comparison_rows': comparison_rows,
        'fan_compared_shipments': compared_shipments,
        'fan_avg_abs_variance_pct': avg_abs_variance_pct,
        'fan_largest_variances': shipment_rows[:5],
        'fan_chart_labels': json.dumps([r['label'] for r in comparison_rows]),
        'fan_chart_estimated': json.dumps([r['estimate_avg'] for r in comparison_rows]),
        'fan_chart_actual': json.dumps([r['actual_avg'] for r in comparison_rows]),
    }


def _status_breakdown(chart_ids, chart_total):
    """Per-status counts -> wireframe pipeline rows (respects chart filters)."""
    status_colors = {
        'incoming':    '#f59e0b', 'arrived':    '#3b82f6', 'computed':    '#8b5cf6',
        'approved':    '#22c55e', 'rejected':   '#ef4444', 'for_revision':'#f97316',
        'lodgement':   '#38bdf8', 'ongoing':    '#64748b', 'assessed':    '#14b8a6',
        'paid':        '#84cc16', 'released':   '#22d3ee', 'billed':      '#a855f7',
    }
    status_counts_raw = {
        r['status']: r['count']
        for r in (
            Shipment.objects.filter(id__in=chart_ids)
            .values('status')
            .annotate(count=Count('id'))
        )
    }
    status_rows = []
    for key, label in Shipment.STATUS_CHOICES:
        count = status_counts_raw.get(key, 0)
        status_rows.append({
            'key': key, 'label': label, 'count': count,
            'pct': round(count / chart_total * 100, 1) if chart_total else 0,
            'color': status_colors.get(key, '#475569'),
        })
    # Dashboard display order: 4 rows x 3 columns, matching the supervisor wireframe.
    pipeline_order = [
        'incoming', 'approved', 'assessed',
        'arrived', 'for_revision', 'paid',
        'rejected', 'lodgement', 'released',
        'computed', 'ongoing', 'billed',
    ]
    status_map = {r['key']: r for r in status_rows}
    pipeline_rows = [status_map[k] for k in pipeline_order if k in status_map]
    max_status = max((r['count'] for r in pipeline_rows), default=1) or 1
    for row in pipeline_rows:
        row['bar_pct'] = round(row['count'] / max_status * 100) if max_status > 0 else 0
    status_rows_sorted = sorted(pipeline_rows, key=lambda r: r['count'], reverse=True)

    # Add subtitle + display label to each pipeline row for the Status Overview cards.
    status_meta = {
        'incoming':     {'subtitle': 'Awaits Declarant Assignment'},
        'arrived':      {'subtitle': 'Awaits ECDT Processing'},
        'computed':     {'subtitle': 'Awaits Consignee Approval'},
        'for_revision': {'subtitle': 'Returned for Revision'},
        'rejected':     {'subtitle': 'Docs Not Complete Update From Declarant'},
        'approved':     {'subtitle': 'Proceeding to Lodgement'},
        'lodgement':    {'subtitle': 'Filed with BOC'},
        'ongoing':      {'subtitle': 'Lined Up for Final Assessment'},
        'assessed':     {'subtitle': 'Awaits Payment of D/T'},
        'paid':         {'subtitle': 'Awaits CNTR Discharge & Delivery'},
        'released':     {'subtitle': 'Awaits Final Billing'},
        'billed':       {'subtitle': 'Shipment Fully Processed End-to-End'},
    }
    display_labels = {'for_revision': 'Revision', 'rejected': 'Flags'}
    for row in pipeline_rows:
        row['subtitle'] = status_meta.get(row['key'], {}).get('subtitle', '')
        row['display_label'] = display_labels.get(row['key'], row['label'])
    return {'pipeline_rows': pipeline_rows, 'status_rows_sorted': status_rows_sorted}


def _wmcda_scoreboard(advisory_qs):
    """WMCDA recommendation scoreboard (counts, %, avg score, rank)."""
    wmcda_meta = [
        ('air',  'Air Freight',  '#f59e0b', 'AIR'),
        ('lcl',  'LCL Sea',      '#38bdf8', 'LCL'),
        ('fcl',  'FCL Sea',      '#8b5cf6', 'FCL'),
    ]
    wmcda_total = advisory_qs.filter(recommended_type__isnull=False).count()
    type_counts = {
        r['recommended_type']: r['cnt']
        for r in advisory_qs.values('recommended_type').annotate(cnt=Count('id'))
        if r['recommended_type']
    }
    avg_agg = advisory_qs.aggregate(
        avg_air=Avg('air_score'), avg_lcl=Avg('lcl_score'),
        avg_fcl=Avg('fcl_score'),
    )
    wmcda_scoreboard = []
    for key, label, color, icon in wmcda_meta:
        count     = type_counts.get(key, 0)
        pct       = round(count / wmcda_total * 100, 1) if wmcda_total else 0
        avg_score = round(float(avg_agg.get(f'avg_{key}') or 0) * 100, 1)
        wmcda_scoreboard.append({
            'key': key, 'label': label, 'color': color, 'icon': icon,
            'count': count, 'pct': pct, 'avg_score': avg_score,
        })
    wmcda_scoreboard.sort(key=lambda x: x['count'], reverse=True)
    rank_labels = ['1st', '2nd', '3rd']
    for i, row in enumerate(wmcda_scoreboard):
        row['rank'] = rank_labels[i] if i < len(rank_labels) else f'{i+1}th'
    wmcda_max = wmcda_scoreboard[0]['count'] if wmcda_scoreboard else 1
    return {
        'wmcda_scoreboard': wmcda_scoreboard,
        'wmcda_max':        wmcda_max,
        'wmcda_total':      wmcda_total,
    }


def _wmcda_comparison(advisory_qs):
    """Declared shipment type vs WMCDA recommendation agreement matrix."""
    cmp_meta = [('air', 'Air Freight'), ('lcl', 'LCL Sea'), ('fcl', 'FCL Sea')]
    cmp_keys = [k for k, _ in cmp_meta]
    cmp_cross = {d: {r: 0 for r in cmp_keys} for d in cmp_keys}
    for r in (advisory_qs
              .exclude(recommended_type__isnull=True)
              .exclude(shipment__shipment_type__isnull=True)
              .values('shipment__shipment_type', 'recommended_type')
              .annotate(cnt=Count('id'))):
        d, rec = r['shipment__shipment_type'], r['recommended_type']
        if d in cmp_cross and rec in cmp_cross[d]:
            cmp_cross[d][rec] += r['cnt']
    comparison_rows = []
    cmp_total = cmp_match = 0
    for d, dlabel in cmp_meta:
        row_total = sum(cmp_cross[d].values())
        row_match = cmp_cross[d][d]
        cmp_total += row_total
        cmp_match += row_match
        comparison_rows.append({
            'declared': dlabel, 'declared_key': d,
            'total': row_total, 'match': row_match,
            'match_pct': round(row_match / row_total * 100) if row_total else 0,
            'cells': [
                {'key': rk, 'count': cmp_cross[d][rk], 'is_match': rk == d}
                for rk in cmp_keys
            ],
        })
    return {
        'wmcda_comparison_rows':      comparison_rows,
        'wmcda_comparison_agreement': round(cmp_match / cmp_total * 100) if cmp_total else 0,
        'wmcda_comparison_total':     cmp_total,
    }


def _declarant_performance(chart_ids, declarants):
    """Per-declarant processing volume, avg speed, approval quality.

    Batch-loads StatusLog rows for the filtered shipments to avoid N+1 queries.
    """
    perf_logs = (
        StatusLog.objects
        .filter(
            shipment_id__in=chart_ids,
            new_status__in=['computed', 'arrived', 'approved', 'for_revision', 'rejected'],
        )
        .values('shipment_id', 'new_status', 'changed_at')
        .order_by('shipment_id', 'changed_at')
    )
    ship_declarant = dict(
        Shipment.objects.filter(id__in=chart_ids).values_list('id', 'declarant_id')
    )
    dec_logs = defaultdict(lambda: defaultdict(list))  # dec_id -> status -> [log_dicts]
    for log in perf_logs:
        dec_id = ship_declarant.get(log['shipment_id'])
        if dec_id:
            dec_logs[dec_id][log['new_status']].append(log)

    declarant_data = []
    for dec in declarants:
        logs_by_status = dec_logs.get(dec.id, {})

        # First computed log per shipment
        computed_map = {}
        for log in logs_by_status.get('computed', []):
            sid = log['shipment_id']
            if sid not in computed_map or log['changed_at'] < computed_map[sid]['changed_at']:
                computed_map[sid] = log

        # Most recent arrived log per shipment (for speed calculation)
        arrived_map = {}
        for log in logs_by_status.get('arrived', []):
            sid = log['shipment_id']
            if sid not in arrived_map or log['changed_at'] > arrived_map[sid]['changed_at']:
                arrived_map[sid] = log

        durations = []
        for sid, c_log in computed_map.items():
            a_log = arrived_map.get(sid)
            if a_log and a_log['changed_at'] <= c_log['changed_at']:
                durations.append(c_log['changed_at'] - a_log['changed_at'])

        total_comp       = len(computed_map)
        ecdt_approved    = len(logs_by_status.get('approved', []))
        revised_rejected = (
            len(logs_by_status.get('for_revision', []))
            + len(logs_by_status.get('rejected', []))
        )
        avg_hours = None
        if durations:
            avg_hours = round(
                sum(d.total_seconds() for d in durations) / len(durations) / 3600, 1
            )
        declarant_data.append({
            'name':             dec.get_full_name() or dec.username,
            'username':         dec.username,
            'total_processed':  total_comp,
            'avg_hours':        avg_hours,
            'ecdt_approved':    ecdt_approved,
            'revised_rejected': revised_rejected,
            'approval_rate':    round(ecdt_approved / total_comp * 100, 1) if total_comp else 0,
        })
    return declarant_data


def _urgency_distribution(chart_qs):
    """Urgency mix (normalising the legacy 'normal' alias to 'standard')."""
    raw = chart_qs.values('urgency').annotate(count=Count('id'))
    umap = {}
    for r in raw:
        key = 'standard' if r['urgency'] in ('normal', 'standard', None) else r['urgency']
        umap[key] = umap.get(key, 0) + r['count']
    urgency_counts = [
        {'key': 'standard', 'label': 'Standard', 'color': '#3b82f6', 'count': umap.get('standard', 0)},
        {'key': 'priority', 'label': 'Priority', 'color': '#f59e0b', 'count': umap.get('priority', 0)},
        {'key': 'urgent',   'label': 'Urgent',   'color': '#f97316', 'count': umap.get('urgent', 0)},
        {'key': 'rush',     'label': 'Rush',     'color': '#ef4444', 'count': umap.get('rush', 0)},
    ]
    return {
        'urgency_counts':       urgency_counts,
        'urgency_total':        sum(u['count'] for u in urgency_counts),
        'urgency_chart_labels': json.dumps([u['label'] for u in urgency_counts]),
        'urgency_chart_data':   json.dumps([u['count'] for u in urgency_counts]),
        'urgency_chart_colors': json.dumps([u['color'] for u in urgency_counts]),
    }


def _monthly_overview(all_shipments, declarant_filter, overview_range):
    """Monthly submission counts for the overview line chart (full year or 6m)."""
    def _add_months(value, months):
        month_index = value.month - 1 + months
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        return value.replace(year=year, month=month, day=1)

    def _period_date(value):
        return value.date() if hasattr(value, 'date') else value

    today = timezone.localdate()
    overview_qs = all_shipments
    if declarant_filter:
        overview_qs = overview_qs.filter(declarant__username=declarant_filter)

    if overview_range == '6m':
        start = _add_months(today.replace(day=1), -5)
        end = _add_months(today.replace(day=1), 1) - timedelta(days=1)
    else:
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)

    rows = list(
        overview_qs
        .filter(submitted_at__date__gte=start, submitted_at__date__lte=end)
        .annotate(period=TruncMonth('submitted_at'))
        .values('period')
        .annotate(count=Count('id'))
        .order_by('period')
    )
    period_map = {
        _period_date(r['period']).replace(day=1): r['count']
        for r in rows if r['period']
    }
    labels, data = [], []
    cursor = start.replace(day=1)
    while cursor <= end:
        labels.append(cursor.strftime('%b %Y'))
        data.append(period_map.get(cursor, 0))
        cursor = _add_months(cursor, 1)
    return {
        'monthly_chart_labels': json.dumps(labels),
        'monthly_chart_data':   json.dumps(data),
    }


def _due_date_buckets(chart_qs):
    """Pre-clearance SLA countdown buckets for active (pre-assessment) shipments."""
    done_statuses = ['assessed', 'paid', 'released', 'billed']
    today = timezone.now().date()
    d1 = d3 = d5 = d5plus = 0
    active_qs = chart_qs.exclude(status__in=done_statuses)
    due_total = active_qs.count()
    for s in active_qs.values('urgency', 'submitted_at'):
        alloc     = _urgency_days_for(s['urgency'])
        deadline  = _add_business_days(s['submitted_at'], alloc)
        remaining = _business_days_diff(today, deadline)
        if remaining <= 1:
            d1 += 1
        elif remaining <= 3:
            d3 += 1
        elif remaining <= 5:
            d5 += 1
        else:
            d5plus += 1
    return {
        'due_date_data': {
            'one_day': d1, 'three_days': d3,
            'five_days': d5, 'over_five': d5plus,
            'total': due_total,
        },
        'due_date_chart_data':   json.dumps([d1, d3, d5, d5plus]),
        'due_date_chart_labels': json.dumps(['1 Day Left', '3 Days Left', '5 Days Left', '5+ Days Left']),
        'due_date_chart_colors': json.dumps(['#dc0000', '#f75b5b', '#f9a1a1', '#ffd6d6']),
    }


def _wmcda_bar_chart(wmcda_scoreboard):
    """Vertical WMCDA bar chart data in fixed LCL / Air / FCL order."""
    bar_order = [
        ('lcl',  'LCL Sea',      '#38bdf8'),
        ('air',  'Air Freight',  '#f59e0b'),
        ('fcl',  'FCL Sea',      '#8b5cf6'),
    ]
    wmap = {r['key']: r for r in wmcda_scoreboard}
    return {
        'wmcda_bar_labels': json.dumps([b[1] for b in bar_order]),
        'wmcda_bar_data':   json.dumps([wmap.get(b[0], {}).get('count', 0) for b in bar_order]),
        'wmcda_bar_colors': json.dumps([b[2] for b in bar_order]),
        'wmcda_bar_keys':   json.dumps([b[0] for b in bar_order]),
    }


def _top_declarant(declarant_data):
    """Top performer by processing volume, then approval quality (or None)."""
    eligible = [d for d in declarant_data if d['total_processed'] > 0]
    if not eligible:
        return None
    top = max(eligible, key=lambda d: (d['total_processed'], d['approval_rate'], d['ecdt_approved']))
    name_parts = [part for part in top['name'].split() if part]
    top['initials'] = ''.join(part[0] for part in name_parts[:2]).upper()
    return top




__all__ = [
    '_feedback_summary', '_shipment_type_counts', '_currency_breakdown',
    '_cost_by_type', '_estimate_vs_fan', '_status_breakdown', '_wmcda_scoreboard',
    '_wmcda_comparison', '_declarant_performance', '_urgency_distribution',
    '_monthly_overview', '_due_date_buckets', '_wmcda_bar_chart', '_top_declarant',
]
