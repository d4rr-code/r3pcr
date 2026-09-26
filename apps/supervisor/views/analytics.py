import json
import logging
from collections import defaultdict
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Avg, Sum, Min, Max, Q
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse
from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback
from apps.supervisor.audit import log_audit

logger = logging.getLogger(__name__)

from .common import *  # noqa: F401,F403
from .analytics_sections import *  # noqa: F401,F403

@login_required
@supervisor_required
def dashboard(request):
    """Unified analytics/command-centre page."""
    return _analytics_context_response(request)


def _analytics_report_data(request):
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    declarant_filter = request.GET.get('declarant', '').strip()

    qs = Shipment.objects.select_related('consignee', 'declarant').all()
    if date_from:
        qs = qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(submitted_at__date__lte=date_to)
    if declarant_filter:
        qs = qs.filter(declarant__username=declarant_filter)

    status_rows = []
    status_counts = {
        row['status']: row['count']
        for row in qs.values('status').annotate(count=Count('id'))
    }
    total = sum(status_counts.values())
    for key, label in Shipment.STATUS_CHOICES:
        count = status_counts.get(key, 0)
        status_rows.append([label, count, f'{round(count / total * 100, 1) if total else 0}%'])

    type_rows = []
    type_counts = {
        row['shipment_type']: row['count']
        for row in qs.values('shipment_type').annotate(count=Count('id'))
    }
    for key, label in Shipment.SHIPMENT_TYPE_CHOICES:
        count = type_counts.get(key, 0)
        type_rows.append([label, count, f'{round(count / total * 100, 1) if total else 0}%'])

    urgency_map = defaultdict(int)
    for row in qs.values('urgency').annotate(count=Count('id')):
        key = 'standard' if row['urgency'] in ('normal', 'standard', None) else row['urgency']
        urgency_map[key] += row['count']
    urgency_labels = dict(Shipment.URGENCY_CHOICES)
    urgency_rows = []
    for key in ('standard', 'priority', 'urgent', 'rush'):
        count = urgency_map.get(key, 0)
        urgency_rows.append([urgency_labels.get(key, key.title()), count, f'{round(count / total * 100, 1) if total else 0}%'])

    ids = qs.values_list('id', flat=True)
    advisory_qs = ShippingAdvisory.objects.filter(shipment_id__in=ids)
    wmcda_labels = {'air': 'Air Freight', 'lcl': 'LCL Sea', 'fcl': 'FCL Sea'}
    wmcda_counts = {
        row['recommended_type']: row['count']
        for row in advisory_qs.values('recommended_type').annotate(count=Count('id'))
        if row['recommended_type']
    }
    wmcda_total = sum(wmcda_counts.values())
    wmcda_avg = advisory_qs.aggregate(
        avg_air=Avg('air_score'), avg_lcl=Avg('lcl_score'),
        avg_fcl=Avg('fcl_score'),
    )
    wmcda_rows = []
    for key, label in wmcda_labels.items():
        count = wmcda_counts.get(key, 0)
        wmcda_rows.append([
            label,
            count,
            f'{round(count / wmcda_total * 100, 1) if wmcda_total else 0}%',
            f'{round(float(wmcda_avg.get(f"avg_{key}") or 0) * 100, 1)}%',
        ])

    currency_counts = list(
        qs.exclude(invoice_currency='')
        .values('invoice_currency')
        .annotate(count=Count('id'))
        .order_by('-count')
    )
    currency_total = sum(row['count'] for row in currency_counts)
    currency_rows = []
    for row in currency_counts:
        count = row['count']
        currency_rows.append([
            row['invoice_currency'] or 'USD',
            count,
            f'{round(count / currency_total * 100, 1) if currency_total else 0}%',
        ])

    cost_rows = []
    cost_qs = DutyComputation.objects.filter(shipment_id__in=ids, total_landed_cost__isnull=False)
    cost_aggregates = {
        row['shipment__shipment_type']: row
        for row in cost_qs.values('shipment__shipment_type').annotate(
            count=Count('id'),
            avg=Avg('total_landed_cost'),
            total=Sum('total_landed_cost'),
            min_val=Min('total_landed_cost'),
            max_val=Max('total_landed_cost'),
        )
    }
    for key, label in Shipment.SHIPMENT_TYPE_CHOICES:
        agg = cost_aggregates.get(key, {})
        cost_rows.append([
            label,
            agg.get('count', 0),
            round(float(agg.get('avg') or 0), 2),
            round(float(agg.get('total') or 0), 2),
            round(float(agg.get('min_val') or 0), 2),
            round(float(agg.get('max_val') or 0), 2),
        ])

    computed_statuses = [
        'computed', 'approved', 'lodgement', 'ongoing',
        'assessed', 'paid', 'released', 'billed',
    ]
    declarant_counts = {
        row['declarant_id']: row
        for row in qs.exclude(declarant_id=None).values('declarant_id').annotate(
            assigned=Count('id'),
            computed=Count('id', filter=Q(status__in=computed_statuses)),
            completed=Count('id', filter=Q(status='billed')),
            revision_flags=Count(
                'id', filter=Q(status__in=['for_revision', 'rejected'])
            ),
        )
    }
    declarant_rows = []
    for dec in User.objects.filter(role='declarant').order_by('first_name', 'username'):
        counts = declarant_counts.get(dec.id, {})
        assigned = counts.get('assigned', 0)
        computed = counts.get('computed', 0)
        completed = counts.get('completed', 0)
        revision_flags = counts.get('revision_flags', 0)
        if assigned or computed or completed or revision_flags:
            declarant_rows.append([
                dec.get_full_name() or dec.username,
                assigned,
                computed,
                completed,
                revision_flags,
                f'{round(completed / assigned * 100, 1) if assigned else 0}%',
            ])

    feedback_qs = Feedback.objects.filter(shipment_id__in=ids)
    feedback = feedback_qs.aggregate(
        total=Count('id'),
        avg=Avg('rating'),
        positive=Count('id', filter=Q(rating__gte=4)),
    )
    feedback_total = feedback['total']
    feedback_avg = feedback['avg']
    feedback_positive = feedback['positive']

    recent_rows = []
    for s in qs.order_by('-submitted_at')[:100]:
        recent_rows.append([
            s.hawb_number,
            s.consignee.get_full_name() or s.consignee.username,
            s.get_shipment_type_display() or '',
            s.get_status_display(),
            s.get_urgency_display(),
            s.submitted_at.strftime('%Y-%m-%d'),
        ])

    from .intelligence import _workload_forecast
    workload_forecast = _workload_forecast(
        list(qs),
        forecast_periods=3,
        forecast_unit='month',
        forecast_year=timezone.localdate().year,
        forecast_model='all',
    )
    forecast_available = workload_forecast['forecast_available']
    forecast_summary_rows = [
        ['Forecast Period', workload_forecast['forecast_label']],
        [
            'Projected Incoming Workload',
            workload_forecast['projected_period_total'] if forecast_available else 'Unavailable',
        ],
        [
            'Expected Range',
            f"{workload_forecast['projected_low']} - {workload_forecast['projected_high']}"
            if forecast_available else 'Unavailable',
        ],
        ['Workload Level', workload_forecast['level'] or 'Unavailable'],
        ['Confidence', workload_forecast['confidence']],
        ['Forecast Model', workload_forecast['displayed_model']['label']],
        [
            'Trend vs Previous Period',
            f"{workload_forecast['trend_pct']}%" if forecast_available else 'Unavailable',
        ],
        ['Active Backlog', workload_forecast['active_backlog']],
        ['Interpretation', workload_forecast['interpretation']],
        ['Recommended Action', workload_forecast['action']],
    ]
    forecast_period_rows = [
        [row['label'], row['date_range'], row['count']]
        for row in workload_forecast['period_rows']
    ]
    forecast_model_rows = [
        [row['label'], row['total'], row['mae_label'], row['status']]
        for row in workload_forecast['model_comparison']
    ]

    report_notes = [
        ['Decision Support Scope', 'Analytics summarize operational records and support supervisor planning; they do not replace customs-broker judgment.'],
        ['Exchange Rates', 'Duties and landed-cost figures use the manually maintained rates saved by supervisors.'],
        ['Shipping Type Advisory', 'MCDA results compare Air Freight, LCL, and FCL as advisory outputs.'],
        ['Forecast Limitation', 'Incoming workload forecasts are planning estimates based on available shipment history, not guaranteed future volume.'],
    ]

    active_user_counts = {
        row['role']: row['count']
        for row in User.objects.filter(
            role__in=['consignee', 'declarant'],
            is_active=True,
            is_pending_approval=False,
        ).values('role').annotate(count=Count('id'))
    }

    return {
        'generated_at': timezone.localtime().strftime('%Y-%m-%d %H:%M'),
        'total': total,
        'filters': {
            'date_from': date_from or 'All',
            'date_to': date_to or 'All',
            'declarant': declarant_filter or 'All',
        },
        'summary': [
            ['Total Shipments', total],
            ['Active Users', sum(active_user_counts.values())],
            ['Consignees', active_user_counts.get('consignee', 0)],
            ['Declarants', active_user_counts.get('declarant', 0)],
            ['MCDA Advisories', wmcda_total],
            ['Feedback Count', feedback_total],
            ['Average Feedback Rating', round(float(feedback_avg or 0), 1)],
            ['Positive Feedback %', f'{round(feedback_positive / feedback_total * 100, 1) if feedback_total else 0}%'],
            ['Projected Incoming Workload', workload_forecast['projected_period_total']],
            ['Forecast Confidence', workload_forecast['confidence']],
        ],
        'tables': [
            ('Report Notes', ['Area', 'Note'], report_notes),
            ('Incoming Workload Forecast', ['Metric', 'Value'], forecast_summary_rows),
            ('Forecast Periods', ['Period', 'Type', 'Incoming'], forecast_period_rows),
            ('Forecast Model Comparison', ['Model', 'Forecast Total', 'Backtest Error', 'Status'], forecast_model_rows),
            ('Status Pipeline', ['Status', 'Count', 'Share'], status_rows),
            ('Shipment Types', ['Type', 'Count', 'Share'], type_rows),
            ('Urgency Distribution', ['Urgency', 'Count', 'Share'], urgency_rows),
            ('MCDA Recommendations', ['Shipping Type', 'Recommended Count', 'Share', 'Average Score'], wmcda_rows),
            ('Currency Usage', ['Currency', 'Count', 'Share'], currency_rows),
            ('Landed Cost By Shipping Type', ['Shipping Type', 'Computations', 'Average PHP', 'Total PHP', 'Min PHP', 'Max PHP'], cost_rows),
            ('Declarant Performance', ['Declarant', 'Assigned', 'Computed+', 'Billed', 'Revision/Rejected', 'Completion %'], declarant_rows),
            ('Recent Shipments', ['HAWB', 'Consignee', 'Shipping Type', 'Status', 'Urgency', 'Submitted'], recent_rows),
        ],
    }


def _pdf_col_widths(table_data, avail_width):
    """Distribute the available page width across columns in proportion to each
    column's widest cell, so report tables fill the page (left-aligned) instead
    of shrinking to content and floating in the centre. Every column keeps a
    sensible minimum share so narrow columns stay readable."""
    ncols = len(table_data[0])
    natural = [1] * ncols
    for row in table_data:
        for i in range(ncols):
            cell = row[i] if i < len(row) else ''
            natural[i] = max(natural[i], len(str(cell if cell is not None else '')))
    # Apply a floor so a very short column (e.g. a count) still reads cleanly.
    floor = max(natural) * 0.18
    natural = [max(n, floor) for n in natural]
    total = sum(natural)
    return [avail_width * (n / total) for n in natural]


@login_required
@supervisor_required
def analytics_export(request):
    fmt = (request.GET.get('format') or 'xlsx').lower()
    data = _analytics_report_data(request)
    filename_date = timezone.localtime().strftime('%Y%m%d')
    log_audit(
        'report_download',
        f'Supervisor downloaded analytics report ({fmt}).',
        request=request,
        details={'format': fmt, 'filters': data['filters']},
    )

    from .reports import build_report_meta, format_filters, resolve_report_period

    report_title = 'R3-PCR Analytics Report'
    report_meta = build_report_meta(
        request,
        # Label only — filtering still uses the date_from/date_to already
        # resolved by _analytics_report_data above.
        period=resolve_report_period(request),
        filters=format_filters([('Declarant', data['filters']['declarant'])]),
        total=data['total'],
    )

    if fmt == 'csv':
        from .reports import csv_multi_section_response

        return csv_multi_section_response(
            'R3PCR_Analytics_Report', report_title, report_meta,
            [('Executive Summary', ['Metric', 'Value'], data['summary'])] + list(data['tables']),
        )

    if fmt == 'pdf':
        from io import BytesIO
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=24, leftMargin=24, topMargin=24, bottomMargin=24)
        styles = getSampleStyleSheet()
        cell_style = ParagraphStyle('cell', fontName='Helvetica', fontSize=8, leading=10)
        from .reports import pdf_meta_flowables

        story = [
            Paragraph(report_title, styles['Title']),
            Spacer(1, 6),
            pdf_meta_flowables(report_meta, doc.width),
            Spacer(1, 14),
        ]
        for title, headers, rows in [('Executive Summary', ['Metric', 'Value'], data['summary'])] + data['tables']:
            story.append(Paragraph(title, styles['Heading2']))
            body = rows or [['No data', ''] + [''] * (len(headers) - 2)]
            col_widths = _pdf_col_widths([headers] + body, doc.width)
            # Header cells stay plain strings (styled by TableStyle); body cells
            # are wrapped Paragraphs so long text wraps within its column.
            table_data = [list(headers)] + [
                [Paragraph(str(c if c is not None else ''), cell_style)
                 for c in (list(row) + [''] * (len(headers) - len(row)))]
                for row in body
            ]
            tbl = Table(table_data, colWidths=col_widths, repeatRows=1, hAlign='LEFT')
            tbl.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1B3358')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#DCE5EF')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8FAFC')]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            story.extend([tbl, Spacer(1, 12)])
        doc.build(story)
        response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="R3PCR_Analytics_Report_{filename_date}.pdf"'
        return response

    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = 'Summary'
    header_fill = PatternFill('solid', fgColor='1B3358')
    header_font = Font(color='FFFFFF', bold=True)

    ws.append([report_title])
    ws['A1'].font = Font(bold=True, size=16)
    for label, value in report_meta:
        ws.append([f'{label}:', value])
    ws.append([])

    summary_header_row = ws.max_row + 1
    ws.append(['Metric', 'Value'])
    for cell in ws[summary_header_row]:
        cell.fill = header_fill
        cell.font = header_font
    for row in data['summary']:
        ws.append(row)

    for title, headers, rows in data['tables']:
        sheet = wb.create_sheet(title[:31])
        sheet.append(headers)
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center')
        for row in rows:
            sheet.append(row)
        for col in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 45)

    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 45)

    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="R3PCR_Analytics_Report_{filename_date}.xlsx"'
    return response


#  User Management 


@login_required
@supervisor_required
def analytics(request):
    return redirect('supervisor:dashboard')



def _analytics_filters(request, all_shipments):
    """Parse GET filters and build the chart + shipment-table querysets."""
    date_from        = request.GET.get('date_from', '').strip()
    date_to          = request.GET.get('date_to', '').strip()
    declarant_filter = request.GET.get('declarant', '').strip()
    overview_range   = request.GET.get('overview_range', 'year').strip().lower()
    if overview_range not in {'all', 'year', '6m'}:
        overview_range = 'year'

    chart_qs = all_shipments
    if date_from:
        chart_qs = chart_qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        chart_qs = chart_qs.filter(submitted_at__date__lte=date_to)
    if declarant_filter:
        chart_qs = chart_qs.filter(declarant__username=declarant_filter)

    return {
        'date_from': date_from, 'date_to': date_to,
        'declarant_filter': declarant_filter, 'overview_range': overview_range,
        'chart_qs': chart_qs,
    }


def _analytics_context_response(request):
    all_shipments = Shipment.objects.all()

    # Filters + chart/table querysets
    _f = _analytics_filters(request, all_shipments)
    date_from        = _f['date_from']
    date_to          = _f['date_to']
    declarant_filter = _f['declarant_filter']
    overview_range   = _f['overview_range']
    chart_qs         = _f['chart_qs']

    # KPI strip shipment counts respect the active analytics filters.
    filtered_shipments = chart_qs
    # Materialise chart_qs IDs once — reused for status, MCDA and declarant sections
    _chart_ids_qs = chart_qs.values_list('id', flat=True)

    # Status breakdown bar chart (respects chart filters)
    _status = _status_breakdown(_chart_ids_qs)
    pipeline_rows      = _status['pipeline_rows']
    status_rows_sorted = _status['status_rows_sorted']
    status_counts      = _status['status_counts']
    chart_total        = _status['chart_total']
    total_all          = chart_total

    # MCDA scoreboard + declared-vs-recommended agreement matrix
    advisory_qs = ShippingAdvisory.objects.filter(shipment_id__in=_chart_ids_qs)
    _sb = _wmcda_scoreboard(advisory_qs)
    wmcda_scoreboard = _sb['wmcda_scoreboard']
    wmcda_max        = _sb['wmcda_max']
    wmcda_total      = _sb['wmcda_total']
    _cmp = _wmcda_comparison(advisory_qs)
    wmcda_comparison_rows      = _cmp['wmcda_comparison_rows']
    wmcda_comparison_agreement = _cmp['wmcda_comparison_agreement']
    wmcda_comparison_total     = _cmp['wmcda_comparison_total']

    # Declarant Performance (respects chart filters)
    declarants = User.objects.filter(role='declarant').order_by('first_name', 'username')
    declarant_data = _declarant_performance(_chart_ids_qs, declarants)

    # ── Redesigned dashboard: new context variables ──────────────────────

    # Shipment type KPI counts respect the active analytics filters.
    shipment_type_counts = _shipment_type_counts(filtered_shipments)

    # Urgency distribution (respects chart filters)
    _urg = _urgency_distribution(chart_qs)
    urgency_counts       = _urg['urgency_counts']
    urgency_total        = _urg['urgency_total']
    urgency_chart_labels = _urg['urgency_chart_labels']
    urgency_chart_data   = _urg['urgency_chart_data']
    urgency_chart_colors = _urg['urgency_chart_colors']
    selected_month = (date_from[:7] if date_from else timezone.now().strftime('%Y-%m'))

    # Monthly submission overview line chart
    _overview = _monthly_overview(chart_qs, overview_range)
    monthly_chart_labels = _overview['monthly_chart_labels']
    monthly_chart_data   = _overview['monthly_chart_data']
    monthly_chart_has_data = _overview['monthly_chart_has_data']
    monthly_chart_caption = _overview['monthly_chart_caption']

    # Pre-clearance SLA countdown buckets
    _due = _due_date_buckets(chart_qs)
    due_date_data         = _due['due_date_data']
    due_date_chart_data   = _due['due_date_chart_data']
    due_date_chart_labels = _due['due_date_chart_labels']
    due_date_chart_colors = _due['due_date_chart_colors']

    # MCDA vertical bar chart (fixed LCL / Air / FCL order)
    _bar = _wmcda_bar_chart(wmcda_scoreboard)
    wmcda_bar_labels = _bar['wmcda_bar_labels']
    wmcda_bar_data   = _bar['wmcda_bar_data']
    wmcda_bar_colors = _bar['wmcda_bar_colors']
    wmcda_bar_keys   = _bar['wmcda_bar_keys']

    # Top performing declarant
    top_declarant = _top_declarant(declarant_data)

    # ── Currency usage breakdown ───────────────────────────────────────────────
    _cur = _currency_breakdown(_chart_ids_qs)
    currency_breakdown    = _cur['currency_breakdown']
    currency_total        = _cur['currency_total']
    currency_chart_labels = _cur['currency_chart_labels']
    currency_chart_data   = _cur['currency_chart_data']
    currency_chart_colors = _cur['currency_chart_colors']

    # Cost comparison by shipment type — avg/total landed cost per shipping type
    _cost = _cost_by_type(date_from, date_to, declarant_filter)
    cost_by_type    = _cost['cost_by_type']
    cost_bar_labels = _cost['cost_bar_labels']
    cost_bar_data   = _cost['cost_bar_data']
    cost_bar_colors = _cost['cost_bar_colors']
    cost_bar_keys   = _cost['cost_bar_keys']

    # Feedback summary respects the active analytics filters.
    feedback_summary = _feedback_summary(_chart_ids_qs)

    user_counts = {
        row['role']: row['count']
        for row in User.objects.filter(
            role__in=['consignee', 'declarant'],
            is_active=True,
            is_pending_approval=False,
        ).values('role').annotate(count=Count('id'))
    }
    analytics_config = {
        'urls': {
            'shipmentRecords': reverse('supervisor:shipment_records'),
            'statusCounts': reverse('supervisor:analytics_status_counts'),
        },
        'urgency': {
            'labels': json.loads(urgency_chart_labels),
            'data': json.loads(urgency_chart_data),
            'colors': json.loads(urgency_chart_colors),
        },
        'dueDate': {
            'labels': json.loads(due_date_chart_labels),
            'data': json.loads(due_date_chart_data),
            'colors': json.loads(due_date_chart_colors),
        },
        'monthly': {
            'labels': json.loads(monthly_chart_labels),
            'data': json.loads(monthly_chart_data),
            'hasData': monthly_chart_has_data,
        },
        'cost': {
            'keys': json.loads(cost_bar_keys),
            'labels': json.loads(cost_bar_labels),
            'data': json.loads(cost_bar_data),
            'colors': json.loads(cost_bar_colors),
        },
        'wmcda': {
            'keys': json.loads(wmcda_bar_keys),
            'labels': json.loads(wmcda_bar_labels),
            'data': json.loads(wmcda_bar_data),
            'colors': json.loads(wmcda_bar_colors),
        },
    }

    return render(request, 'supervisor/analytics.html', {
        # KPI strip
        'total_all':                  total_all,
        'total_incoming':             status_counts.get('incoming', 0),
        'total_arrived':              status_counts.get('arrived', 0),
        'total_computed':             status_counts.get('computed', 0),
        'total_approved':             status_counts.get('approved', 0),
        'total_rejected':             status_counts.get('rejected', 0),
        'total_users':                sum(user_counts.values()),
        'total_consignees':           user_counts.get('consignee', 0),
        'total_declarants':           user_counts.get('declarant', 0),
        # chart data
        'chart_total':        chart_total,
        'status_rows':        status_rows_sorted,
        'wmcda_scoreboard':   wmcda_scoreboard,
        'wmcda_max':          wmcda_max,
        'wmcda_total':        wmcda_total,
        'wmcda_comparison_rows':      wmcda_comparison_rows,
        'wmcda_comparison_agreement': wmcda_comparison_agreement,
        'wmcda_comparison_total':     wmcda_comparison_total,
        'declarant_data':     declarant_data,
        # filters
        'date_from':          date_from,
        'date_to':            date_to,
        'declarant_filter':   declarant_filter,
        'overview_range':     overview_range,
        'declarants':         declarants,
        # chart data
        'pipeline_rows':      pipeline_rows,
        # redesigned dashboard
        'shipment_type_counts':  shipment_type_counts,
        'urgency_counts':        urgency_counts,
        'urgency_total':         urgency_total,
        'urgency_chart_labels':  urgency_chart_labels,
        'urgency_chart_data':    urgency_chart_data,
        'urgency_chart_colors':  urgency_chart_colors,
        'due_date_data':         due_date_data,
        'due_date_chart_data':   due_date_chart_data,
        'due_date_chart_labels': due_date_chart_labels,
        'due_date_chart_colors': due_date_chart_colors,
        'monthly_chart_labels':  monthly_chart_labels,
        'monthly_chart_data':    monthly_chart_data,
        'monthly_chart_has_data': monthly_chart_has_data,
        'monthly_chart_caption': monthly_chart_caption,
        'top_declarant':         top_declarant,
        'feedback_summary':      feedback_summary,
        'selected_month':        selected_month,
        'wmcda_bar_labels':      wmcda_bar_labels,
        'wmcda_bar_data':        wmcda_bar_data,
        'wmcda_bar_colors':      wmcda_bar_colors,
        'wmcda_bar_keys':        wmcda_bar_keys,
        # cost comparison
        'cost_by_type':          cost_by_type,
        'cost_bar_labels':       cost_bar_labels,
        'cost_bar_data':         cost_bar_data,
        'cost_bar_colors':       cost_bar_colors,
        'cost_bar_keys':         cost_bar_keys,
        # currency analytics
        'currency_breakdown':      currency_breakdown,
        'currency_total':          currency_total,
        'currency_chart_labels':   currency_chart_labels,
        'currency_chart_data':     currency_chart_data,
        'currency_chart_colors':   currency_chart_colors,
        'analytics_config':         analytics_config,
    })


#  Live Status Counts (AJAX) 

@login_required
@supervisor_required
def analytics_status_counts(request):
    from django.http import JsonResponse
    qs = Shipment.objects.all()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    declarant_filter = request.GET.get('declarant', '').strip()
    if date_from:
        qs = qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(submitted_at__date__lte=date_to)
    if declarant_filter:
        qs = qs.filter(declarant__username=declarant_filter)
    status_totals = {
        row['status']: row['count']
        for row in qs.values('status').annotate(count=Count('id'))
    }
    counts = {
        key: {'count': status_totals.get(key, 0), 'label': label}
        for key, label in Shipment.STATUS_CHOICES
    }
    total = sum(status_totals.values())
    max_count = max(status_totals.values(), default=0)
    return JsonResponse({'counts': counts, 'total': total, 'max_count': max_count})


#  Supervisor Shipment Detail (read-only) 
