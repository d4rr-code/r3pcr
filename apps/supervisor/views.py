import logging
import re
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, F, ExpressionWrapper, DurationField
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

# ─── HS Code Section / Chapter Hierarchy ─────────────────────────────────────
_HS_SECTIONS = [
    (1,  'I',     'Live Animals; Animal Products',                  list(range(1, 6))),
    (2,  'II',    'Vegetable Products',                             list(range(6, 15))),
    (3,  'III',   'Animal or Vegetable Fats and Oils',             [15]),
    (4,  'IV',    'Prepared Foodstuffs; Beverages; Tobacco',        list(range(16, 25))),
    (5,  'V',     'Mineral Products',                               list(range(25, 28))),
    (6,  'VI',    'Chemical or Allied Industry Products',            list(range(28, 39))),
    (7,  'VII',   'Plastics and Rubber',                            [39, 40]),
    (8,  'VIII',  'Raw Hides, Leather, Furskins',                  list(range(41, 44))),
    (9,  'IX',    'Wood, Cork, Straw',                             list(range(44, 47))),
    (10, 'X',     'Pulp of Wood, Paper, Paperboard',               list(range(47, 50))),
    (11, 'XI',    'Textiles and Textile Articles',                 list(range(50, 64))),
    (12, 'XII',   'Footwear, Headgear, Umbrellas',                 list(range(64, 68))),
    (13, 'XIII',  'Articles of Stone, Ceramics, Glass',            list(range(68, 71))),
    (14, 'XIV',   'Precious Stones, Precious Metals',              [71]),
    (15, 'XV',    'Base Metals and Articles',                      list(range(72, 84))),
    (16, 'XVI',   'Machinery and Mechanical Appliances',           [84, 85]),
    (17, 'XVII',  'Vehicles, Aircraft, Vessels',                   list(range(86, 90))),
    (18, 'XVIII', 'Optical, Photographic, Medical Instruments',    list(range(90, 93))),
    (19, 'XIX',   'Arms and Ammunition',                            [93]),
    (20, 'XX',    'Miscellaneous Manufactured Articles',            list(range(94, 97))),
    (21, 'XXI',   'Works of Art, Collectors Pieces, Antiques',     [97]),
]

def _chapter_num(chapter_str):
    """Extract numeric chapter from 'Chapter 84', '84', 'Chapter 01', '01', etc."""
    if not chapter_str:
        return None
    m = re.search(r'\d+', str(chapter_str))
    return int(m.group()) if m else None

logger = logging.getLogger(__name__)


def _send_mail_async(subject, message, from_email, recipient_list, html_message=None, log_tag=''):
    """Send email in a daemon thread — never blocks the HTTP response."""
    def _send():
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_message,
            )
        except Exception as e:
            print(f'[EMAIL ERROR] {log_tag}: {e}')
    threading.Thread(target=_send, daemon=True).start()
from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, StatusLog
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback
from apps.notifications.utils import notify_shipment_status_change
from .models import SystemConfig, Announcement


def supervisor_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != 'supervisor':
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
@supervisor_required
def dashboard(request):
    """Redirect to the unified analytics/command-centre page."""
    return redirect('supervisor:analytics')


# ─── User Management ─────────────────────────────────────────────────────────

@login_required
@supervisor_required
def user_management(request):
    users   = User.objects.filter(is_pending_approval=False).order_by('role', 'username')
    pending = User.objects.filter(is_pending_approval=True).order_by('date_joined')
    return render(request, 'supervisor/users.html', {
        'users':   users,
        'pending': pending,
    })


@login_required
@supervisor_required
def approve_registration(request, user_id):
    user = get_object_or_404(User, id=user_id, is_pending_approval=True)
    if request.method == 'POST':
        user.is_active           = True
        user.is_pending_approval = False
        user.save()

        if user.email:
            _send_mail_async(
                subject='R3-PCR — Account Approved',
                message=(
                    f'Hello {user.first_name or user.username},\n\n'
                    f'Your R3-PCR account has been approved. '
                    f'You can now log in.\n\nUsername: {user.username}'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=f'''
                    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">
                        <h2 style="color:#22c55e;">Account Approved!</h2>
                        <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                        <p>Your R3-PCR account has been <strong style="color:#22c55e;">approved</strong>.
                           You can now log in.</p>
                        <p><strong>Username:</strong> {user.username}</p>
                        <p style="color:#94a3b8;font-size:12px;margin-top:20px;">
                            R3-PCR Pre-Clearance Decision Support System
                        </p>
                    </div>
                ''',
                log_tag=f'approval email to {user.username}',
            )

        messages.success(request, f'Account for {user.username} approved and activated.')
    return redirect('supervisor:users')


@login_required
@supervisor_required
def reject_registration(request, user_id):
    user = get_object_or_404(User, id=user_id, is_pending_approval=True)
    if request.method == 'POST':
        username = user.username
        email    = user.email
        name     = user.first_name or username
        if email:
            _send_mail_async(
                subject='R3-PCR — Registration Not Approved',
                message=(
                    f'Hello {name},\n\nUnfortunately your R3-PCR registration was not approved. '
                    f'Please contact the administrator for more information.'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                log_tag=f'rejection email to {username}',
            )
        user.delete()
        messages.warning(request, f'Registration for {username} rejected and removed.')
    return redirect('supervisor:users')


@login_required
@supervisor_required
def add_user(request):
    if request.method == 'POST':
        username   = request.POST.get('username', '').strip()
        email      = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        role       = request.POST.get('role')
        password   = request.POST.get('password')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already taken.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
        else:
            User.objects.create_user(
                username=username, email=email,
                first_name=first_name, last_name=last_name,
                role=role, password=password,
            )
            messages.success(request, f'User {username} created.')
            return redirect('supervisor:users')

    return render(request, 'supervisor/add_user.html')


@login_required
@supervisor_required
def toggle_user(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user == request.user:
        messages.error(request, 'You cannot deactivate yourself.')
    else:
        user.is_active = not user.is_active
        user.save()
        state = 'activated' if user.is_active else 'deactivated'
        messages.success(request, f'User {user.username} {state}.')
    return redirect('supervisor:users')


# ─── Analytics (merged command centre) ───────────────────────────────────────

@login_required
@supervisor_required
def analytics(request):
    all_shipments = Shipment.objects.all()

    # ── Chart/KPI filters (date range + declarant) ────────────────────────────
    date_from        = request.GET.get('date_from', '').strip()
    date_to          = request.GET.get('date_to', '').strip()
    declarant_filter = request.GET.get('declarant', '').strip()

    chart_qs = all_shipments
    if date_from:
        chart_qs = chart_qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        chart_qs = chart_qs.filter(submitted_at__date__lte=date_to)
    if declarant_filter:
        chart_qs = chart_qs.filter(declarant__username=declarant_filter)

    chart_total = chart_qs.count()

    # ── Shipment table filters (search + status + date) ───────────────────────
    q        = request.GET.get('q', '').strip()
    status_f = request.GET.get('status_f', '').strip()

    table_qs = all_shipments.order_by('-submitted_at')
    if q:
        table_qs = (
            all_shipments.filter(hawb_number__icontains=q)
            | all_shipments.filter(consignee__first_name__icontains=q)
            | all_shipments.filter(consignee__last_name__icontains=q)
            | all_shipments.filter(consignee__username__icontains=q)
        ).order_by('-submitted_at')
    if status_f:
        table_qs = table_qs.filter(status=status_f)
    if date_from:
        table_qs = table_qs.filter(submitted_at__date__gte=date_from)
    if date_to:
        table_qs = table_qs.filter(submitted_at__date__lte=date_to)

    # ── KPI strip (always all-time) ───────────────────────────────────────────
    total_all = all_shipments.count()
    total_computed_presented = (
        StatusLog.objects.filter(new_status='computed')
        .values('shipment_id').distinct().count()
    )
    total_consignee_approved = (
        StatusLog.objects.filter(new_status='approved')
        .values('shipment_id').distinct().count()
    )
    consignee_approval_rate = (
        round(total_consignee_approved / total_computed_presented * 100, 1)
        if total_computed_presented else 0
    )

    # ── Status breakdown bar chart (respects chart filters) ───────────────────
    _status_colors = {
        'incoming':    '#f59e0b', 'arrived':    '#3b82f6', 'computed':    '#8b5cf6',
        'approved':    '#22c55e', 'rejected':   '#ef4444', 'for_revision':'#f97316',
        'lodgement':   '#38bdf8', 'ongoing':    '#64748b', 'assessed':    '#14b8a6',
        'paid':        '#84cc16', 'released':   '#22d3ee', 'billed':      '#a855f7',
    }
    status_rows = []
    for key, label in Shipment.STATUS_CHOICES:
        count = chart_qs.filter(status=key).count()
        status_rows.append({
            'key': key, 'label': label, 'count': count,
            'pct': round(count / chart_total * 100, 1) if chart_total else 0,
            'color': _status_colors.get(key, '#475569'),
        })
    # Pipeline order (workflow sequence) — all 12 statuses
    _pipeline_order = [
        'incoming', 'arrived', 'computed', 'for_revision', 'rejected',
        'approved', 'lodgement', 'ongoing', 'assessed', 'paid', 'released', 'billed',
    ]
    _status_map = {r['key']: r for r in status_rows}
    pipeline_rows = [_status_map[k] for k in _pipeline_order if k in _status_map]
    # bar_pct relative to max for stacked bar tooltip (not used for width — CSS does that)
    max_status = max((r['count'] for r in pipeline_rows), default=1) or 1
    for row in pipeline_rows:
        row['bar_pct'] = round(row['count'] / max_status * 100) if max_status > 0 else 0
    # Keep sorted version for any legacy references
    status_rows_sorted = sorted(pipeline_rows, key=lambda r: r['count'], reverse=True)

    # Add icon + subtitle to each pipeline row for the Status Overview cards
    _status_meta = {
        'incoming':     {'icon': '📥', 'subtitle': 'Awaits Declarant Assignment'},
        'arrived':      {'icon': '📦', 'subtitle': 'Awaits ECDT Processing'},
        'computed':     {'icon': '🧮', 'subtitle': 'Awaits Consignee Approval'},
        'for_revision': {'icon': '🔄', 'subtitle': 'Returned for Revision'},
        'rejected':     {'icon': '❌', 'subtitle': 'Rejected by Consignee'},
        'approved':     {'icon': '✅', 'subtitle': 'Proceeding to Lodgement'},
        'lodgement':    {'icon': '📋', 'subtitle': 'Filed with BOC'},
        'ongoing':      {'icon': '⚙️',  'subtitle': 'Lined Up for Final Assessment'},
        'assessed':     {'icon': '📊', 'subtitle': 'Awaits Payment of D/T'},
        'paid':         {'icon': '💳', 'subtitle': 'Awaits CNTR Discharge & Delivery'},
        'released':     {'icon': '🚚', 'subtitle': 'Awaits Final Billing'},
        'billed':       {'icon': '🏁', 'subtitle': 'Shipment Fully Processed End-to-End'},
    }
    for row in pipeline_rows:
        meta = _status_meta.get(row['key'], {})
        row['icon']     = meta.get('icon', '●')
        row['subtitle'] = meta.get('subtitle', '')

    # ── WMCDA Scoreboard (respects chart filters) ─────────────────────────────
    _wmcda_meta = [
        ('air',  'Air Freight',  '#f59e0b', '✈️'),
        ('lcl',  'LCL Sea',      '#38bdf8', '🚢'),
        ('fcl',  'FCL Sea',      '#8b5cf6', '📦'),
    ]
    advisory_qs = ShippingAdvisory.objects.filter(shipment__in=chart_qs)
    wmcda_total = advisory_qs.filter(recommended_type__isnull=False).count()
    wmcda_scoreboard = []
    for key, label, color, icon in _wmcda_meta:
        count     = advisory_qs.filter(recommended_type=key).count()
        pct       = round(count / wmcda_total * 100, 1) if wmcda_total else 0
        avg_score = round(
            float(advisory_qs.aggregate(avg=Avg(f'{key}_score'))['avg'] or 0) * 100, 1
        )
        wmcda_scoreboard.append({
            'key': key, 'label': label, 'color': color, 'icon': icon,
            'count': count, 'pct': pct, 'avg_score': avg_score,
        })
    wmcda_scoreboard.sort(key=lambda x: x['count'], reverse=True)
    # Assign rank badges
    rank_labels = ['🥇 1st', '🥈 2nd', '🥉 3rd', '4th']
    for i, row in enumerate(wmcda_scoreboard):
        row['rank'] = rank_labels[i] if i < len(rank_labels) else f'{i+1}th'
    wmcda_max = wmcda_scoreboard[0]['count'] if wmcda_scoreboard else 1

    # ── Declarant Performance (respects chart filters) ────────────────────────
    declarants = User.objects.filter(role='declarant').order_by('first_name', 'username')
    declarant_data = []
    for dec in declarants:
        d_ships = chart_qs.filter(declarant=dec)
        computed_logs = (
            StatusLog.objects
            .filter(shipment__in=d_ships, new_status='computed')
            .select_related('shipment').order_by('changed_at')
        )
        computed_map = {}
        for log in computed_logs:
            computed_map.setdefault(log.shipment_id, log)

        durations = []
        for sid, c_log in computed_map.items():
            a_log = (
                StatusLog.objects
                .filter(shipment_id=sid, new_status='arrived',
                        changed_at__lte=c_log.changed_at)
                .order_by('-changed_at').first()
            )
            if a_log:
                durations.append(c_log.changed_at - a_log.changed_at)

        total_comp       = len(computed_map)
        # ECDT approved by consignee (total StatusLog events)
        ecdt_approved    = StatusLog.objects.filter(
            shipment__in=d_ships, new_status='approved'
        ).count()
        # Total revision/rejection events by consignee
        revised_rejected = StatusLog.objects.filter(
            shipment__in=d_ships, new_status__in=['for_revision', 'rejected']
        ).count()
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

    return render(request, 'supervisor/analytics.html', {
        # KPI strip
        'total_all':                  total_all,
        'total_incoming':             all_shipments.filter(status='incoming').count(),
        'total_arrived':              all_shipments.filter(status='arrived').count(),
        'total_computed':             all_shipments.filter(status='computed').count(),
        'total_approved':             all_shipments.filter(status='approved').count(),
        'total_rejected':             all_shipments.filter(status='rejected').count(),
        'total_users':                User.objects.count(),
        'total_consignees':           User.objects.filter(role='consignee').count(),
        'total_declarants':           User.objects.filter(role='declarant').count(),
        'consignee_approval_rate':    consignee_approval_rate,
        'total_computed_presented':   total_computed_presented,
        'total_consignee_approved':   total_consignee_approved,
        # chart data
        'chart_total':        chart_total,
        'status_rows':        status_rows_sorted,
        'wmcda_scoreboard':   wmcda_scoreboard,
        'wmcda_max':          wmcda_max,
        'wmcda_total':        wmcda_total,
        'declarant_data':     declarant_data,
        # filters
        'date_from':          date_from,
        'date_to':            date_to,
        'declarant_filter':   declarant_filter,
        'declarants':         declarants,
        # chart data
        'pipeline_rows':      pipeline_rows,
        # shipment table
        'recent':    table_qs,
        'q':         q,
        'status_f':  status_f,
    })


# ─── Live Status Counts (AJAX) ───────────────────────────────────────────────

@login_required
@supervisor_required
def analytics_status_counts(request):
    from django.http import JsonResponse
    qs = Shipment.objects.all()
    total = qs.count()
    counts = {}
    max_count = 0
    for key, label in Shipment.STATUS_CHOICES:
        c = qs.filter(status=key).count()
        counts[key] = {'count': c, 'label': label}
        if c > max_count:
            max_count = c
    return JsonResponse({'counts': counts, 'total': total, 'max_count': max_count})


# ─── Supervisor Shipment Detail (read-only) ───────────────────────────────────

@login_required
@supervisor_required
def shipment_detail(request, shipment_id):
    from apps.shipments.status_progress import build_status_progress, CONSIGNEE_STATUS_SUBLABELS
    shipment    = get_object_or_404(Shipment, id=shipment_id)
    advisory    = getattr(shipment, 'shipping_advisory', None)
    computation = getattr(shipment, 'computation', None)
    status_logs = shipment.status_logs.order_by('-changed_at')
    sad_document = shipment.documents.filter(document_type='sad').first()
    current_sublabel = CONSIGNEE_STATUS_SUBLABELS.get(shipment.status, '')

    explanation = wmcda_scores = wmcda_breakdown = None
    declared_score = declared_breakdown = declared_rating = None

    if advisory:
        try:
            from apps.computation.views import compute_wmcda
            wmcda_scores, _, wmcda_breakdown, explanation = compute_wmcda(
                float(advisory.gross_weight), float(advisory.cargo_volume),
                float(advisory.declared_value), advisory.urgency_level,
                float(advisory.distance_km),
            )
            if wmcda_scores and shipment.shipment_type:
                declared_score = wmcda_scores.get(shipment.shipment_type)
                if wmcda_breakdown:
                    declared_breakdown = wmcda_breakdown.get(shipment.shipment_type)
                if declared_score is not None:
                    if declared_score >= 0.80:   declared_rating = 'Excellent'
                    elif declared_score >= 0.65: declared_rating = 'Good'
                    elif declared_score >= 0.50: declared_rating = 'Fair'
                    else:                        declared_rating = 'Poor'
        except Exception:
            pass

    return render(request, 'supervisor/shipment_detail.html', {
        'shipment':           shipment,
        'advisory':           advisory,
        'computation':        computation,
        'status_logs':        status_logs,
        'explanation':        explanation,
        'wmcda_scores':       wmcda_scores,
        'wmcda_breakdown':    wmcda_breakdown,
        'declared_score':     declared_score,
        'declared_breakdown': declared_breakdown,
        'declared_rating':    declared_rating,
        'status_steps':       build_status_progress(shipment.status, 'consignee'),
        'sad_document':       sad_document,
        'current_sublabel':   current_sublabel,
    })


# ─── Memos & Announcements ────────────────────────────────────────────────────

@login_required
@supervisor_required
def list_memos(request):
    memos = Announcement.objects.all()
    return render(request, 'supervisor/memos.html', {'memos': memos})


@login_required
@supervisor_required
def create_memo(request):
    if request.method == 'POST':
        title    = request.POST.get('title', '').strip()
        content  = request.POST.get('content', '').strip()
        category = request.POST.get('category', 'general')
        if not title or not content:
            messages.error(request, 'Title and content are required.')
        else:
            Announcement.objects.create(
                title=title, content=content,
                category=category, created_by=request.user,
            )
            messages.success(request, f'Announcement "{title}" published.')
    return redirect('supervisor:memos')


@login_required
@supervisor_required
def delete_memo(request, memo_id):
    if request.method == 'POST':
        memo = get_object_or_404(Announcement, id=memo_id)
        title = memo.title
        memo.delete()
        messages.success(request, f'Announcement "{title}" deleted.')
    return redirect('supervisor:memos')


@login_required
@supervisor_required
def toggle_memo(request, memo_id):
    if request.method == 'POST':
        memo = get_object_or_404(Announcement, id=memo_id)
        memo.is_active = not memo.is_active
        memo.save()
        state = 'published' if memo.is_active else 'archived'
        messages.success(request, f'"{memo.title}" {state}.')
    return redirect('supervisor:memos')


# ─── System Configuration ────────────────────────────────────────────────────

_CURRENCY_KEYS = ['rate_USD', 'rate_EUR', 'rate_JPY', 'rate_HKD', 'rate_CNY', 'rate_GBP', 'rate_SGD']

_CURRENCY_META = [
    {'key': 'rate_USD', 'code': 'USD', 'name': 'US Dollar',       'symbol': '$',   'flag': '🇺🇸'},
    {'key': 'rate_EUR', 'code': 'EUR', 'name': 'Euro',             'symbol': '€',   'flag': '🇪🇺'},
    {'key': 'rate_JPY', 'code': 'JPY', 'name': 'Japanese Yen',     'symbol': '¥',   'flag': '🇯🇵'},
    {'key': 'rate_HKD', 'code': 'HKD', 'name': 'Hong Kong Dollar', 'symbol': 'HK$', 'flag': '🇭🇰'},
    {'key': 'rate_CNY', 'code': 'CNY', 'name': 'Chinese Yuan',     'symbol': '¥',   'flag': '🇨🇳'},
    {'key': 'rate_GBP', 'code': 'GBP', 'name': 'British Pound',    'symbol': '£',   'flag': '🇬🇧'},
    {'key': 'rate_SGD', 'code': 'SGD', 'name': 'Singapore Dollar', 'symbol': 'S$',  'flag': '🇸🇬'},
]


def _get_config():
    from types import SimpleNamespace
    defaults = {
        'exchange_rate':  '59.1480',   # Legacy USD→PHP key (kept for backward compat)
        'rate_USD':       '59.1480',
        'rate_EUR':       '65.0000',
        'rate_JPY':       '0.3900',
        'rate_HKD':       '7.5700',
        'rate_CNY':       '8.1500',
        'rate_GBP':       '74.5000',
        'rate_SGD':       '43.8000',
        'vat_rate':       '12.00',
        'wmcda_w_cost':   '35',
        'wmcda_w_time':   '30',
        'wmcda_w_weight': '20',
        'wmcda_w_risk':   '15',
    }
    rows   = {sc.key: sc.value for sc in SystemConfig.objects.all()}
    merged = {k: rows.get(k, v) for k, v in defaults.items()}
    return SimpleNamespace(**merged)


def _config_meta(keys):
    return {
        row.key: row
        for row in SystemConfig.objects.filter(key__in=keys).select_related('updated_by')
    }


@login_required
@supervisor_required
def config_home(request):
    """Landing page — 3 large buttons to sub-sections."""
    return render(request, 'supervisor/config.html')


@login_required
@supervisor_required
def config_global(request):
    config   = _get_config()
    all_keys = _CURRENCY_KEYS + ['exchange_rate', 'vat_rate']
    meta     = _config_meta(all_keys)

    if request.method == 'POST':
        for key in _CURRENCY_KEYS + ['vat_rate']:
            val = request.POST.get(key, '').strip()
            if val:
                SystemConfig.objects.update_or_create(
                    key=key, defaults={'value': val, 'updated_by': request.user}
                )
        # Keep legacy exchange_rate in sync with rate_USD
        usd_val = request.POST.get('rate_USD', '').strip()
        if usd_val:
            SystemConfig.objects.update_or_create(
                key='exchange_rate', defaults={'value': usd_val, 'updated_by': request.user}
            )
        messages.success(request, 'Global parameters saved.')
        return redirect('supervisor:config_global')

    # Build currency rows for template
    currency_rows = []
    for row in _CURRENCY_META:
        currency_rows.append({
            **row,
            'value': getattr(config, row['key'], '0.0000'),
            'meta':  meta.get(row['key']),
        })

    return render(request, 'supervisor/config_global.html', {
        'config':        config,
        'config_meta':   meta,
        'currency_rows': currency_rows,
    })


@login_required
@supervisor_required
def fetch_exchange_rates(request):
    """Fetch live PHP-based rates from Frankfurter API and save to SystemConfig."""
    from django.http import JsonResponse
    import json, urllib.request as urequest

    try:
        url = 'https://api.frankfurter.app/latest?from=PHP&to=USD,EUR,JPY,HKD,CNY,GBP,SGD'
        with urequest.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        rates_raw = data.get('rates', {})
        saved = {}
        _code_to_key = {c['code']: c['key'] for c in _CURRENCY_META}

        for code, rate_key in _code_to_key.items():
            php_per_foreign = rates_raw.get(code)
            if php_per_foreign:
                # Frankfurter: 1 PHP = X foreign → invert to: 1 foreign = Y PHP
                rate_val = round(1.0 / float(php_per_foreign), 4)
                SystemConfig.objects.update_or_create(
                    key=rate_key,
                    defaults={'value': str(rate_val), 'updated_by': request.user},
                )
                saved[code] = rate_val

        # Keep legacy exchange_rate in sync with USD
        if 'USD' in saved:
            SystemConfig.objects.update_or_create(
                key='exchange_rate',
                defaults={'value': str(saved['USD']), 'updated_by': request.user},
            )

        return JsonResponse({'ok': True, 'rates': saved})
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


@login_required
@supervisor_required
def config_wmcda(request):
    config = _get_config()
    meta   = _config_meta(['wmcda_w_cost', 'wmcda_w_time', 'wmcda_w_weight', 'wmcda_w_risk'])
    if request.method == 'POST':
        for key in ('wmcda_w_cost', 'wmcda_w_time', 'wmcda_w_weight', 'wmcda_w_risk'):
            val = request.POST.get(key, '').strip()
            if val:
                SystemConfig.objects.update_or_create(
                    key=key, defaults={'value': val, 'updated_by': request.user}
                )
        messages.success(request, 'WMCDA weights saved.')
        return redirect('supervisor:config_wmcda')
    return render(request, 'supervisor/config_wmcda.html', {'config': config, 'config_meta': meta})


@login_required
@supervisor_required
def config_hscodes_sections(request):
    """Show all 21 HS sections with chapter/code counts."""
    hs_list = HSCode.objects.filter(is_active=True).values('chapter')
    chapter_counts = {}
    for hs in hs_list:
        ch = _chapter_num(hs['chapter'])
        if ch:
            chapter_counts[ch] = chapter_counts.get(ch, 0) + 1

    sections = []
    for num, roman, title, chapters in _HS_SECTIONS:
        total_codes = sum(chapter_counts.get(ch, 0) for ch in chapters)
        has_data    = sum(1 for ch in chapters if chapter_counts.get(ch, 0) > 0)
        sections.append({
            'num': num, 'roman': roman, 'title': title,
            'total_chapters': len(chapters), 'chapters_with_codes': has_data,
            'total_codes': total_codes,
        })
    return render(request, 'supervisor/config_hscodes.html', {'sections': sections})


@login_required
@supervisor_required
def config_hscodes_section(request, section_num):
    """List chapters in one section."""
    section_data = next((s for s in _HS_SECTIONS if s[0] == section_num), None)
    if not section_data:
        messages.error(request, 'Section not found.')
        return redirect('supervisor:config_hscodes_sections')

    num, roman, title, chapters = section_data
    hs_list = HSCode.objects.filter(is_active=True).values('chapter', 'code')
    chapter_map = {}
    for hs in hs_list:
        ch = _chapter_num(hs['chapter'])
        if ch and ch in chapters:
            chapter_map.setdefault(ch, {'count': 0, 'samples': []})
            chapter_map[ch]['count'] += 1
            if len(chapter_map[ch]['samples']) < 3:
                chapter_map[ch]['samples'].append(hs['code'])

    chapter_list = [
        {
            'num': ch, 'num_str': str(ch).zfill(2),
            'count': chapter_map.get(ch, {}).get('count', 0),
            'samples': chapter_map.get(ch, {}).get('samples', []),
        }
        for ch in chapters
    ]
    return render(request, 'supervisor/config_hscodes_section.html', {
        'section_num': num, 'section_roman': roman, 'section_title': title,
        'chapters': chapter_list,
    })


@login_required
@supervisor_required
def config_hscodes_chapter(request, chapter_num):
    """View/edit all HS codes in a specific chapter."""
    section_data = next(
        ((num, roman, title) for num, roman, title, chs in _HS_SECTIONS if chapter_num in chs),
        (None, '', '')
    )
    section_num, section_roman, section_title = section_data

    all_hs   = list(HSCode.objects.filter(is_active=True).order_by('code'))
    hs_codes = [hs for hs in all_hs if _chapter_num(hs.chapter) == chapter_num]

    if request.method == 'POST':
        hs_ids   = request.POST.getlist('hs_id[]')
        hs_rates = request.POST.getlist('hs_rate[]')
        updated  = 0
        for hs_id, rate in zip(hs_ids, hs_rates):
            try:
                hs       = HSCode.objects.get(id=int(hs_id))
                rate_val = float(rate)
                if 0 <= rate_val <= 100:
                    hs.duty_rate = rate_val
                    hs.save(update_fields=['duty_rate'])
                    updated += 1
            except (HSCode.DoesNotExist, ValueError):
                pass
        messages.success(request, f'{updated} duty rate{"s" if updated != 1 else ""} saved.')
        return redirect('supervisor:config_hscodes_chapter', chapter_num=chapter_num)

    return render(request, 'supervisor/config_hscodes_chapter.html', {
        'chapter_num': chapter_num,
        'chapter_num_str': str(chapter_num).zfill(2),
        'section_num': section_num, 'section_roman': section_roman,
        'section_title': section_title, 'hs_codes': hs_codes,
    })


# Keep old URL working (redirect to new home)
@login_required
@supervisor_required
def system_config(request):
    return redirect('supervisor:config_home')


# ─── Shipment Admin Actions ───────────────────────────────────────────────────

@login_required
@supervisor_required
def reset_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment   = get_object_or_404(Shipment, id=shipment_id)
        old_status = shipment.status
        hawb       = shipment.hawb_number

        shipment.status        = 'incoming'
        shipment.declarant     = None
        shipment.boc_reference = None
        shipment.boc_status    = None
        shipment.processed_at  = None
        shipment.save()

        DutyComputation.objects.filter(shipment=shipment).delete()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status='incoming',
            notes='Reset to incoming by supervisor. Computation cleared.',
        )
        messages.success(request, f'Shipment {hawb} reset to Incoming.')
    return redirect('supervisor:dashboard')


@login_required
@supervisor_required
def update_shipment_status(request, shipment_id):
    if request.method == 'POST':
        shipment = get_object_or_404(Shipment, id=shipment_id)
        new_status = request.POST.get('status', '').strip()
        notes = request.POST.get('notes', '').strip()
        allowed = {'approved', 'rejected', 'for_revision'}

        if new_status not in allowed:
            messages.error(request, 'Invalid supervisor status.')
            return redirect('supervisor:dashboard')

        old_status = shipment.status
        shipment.status = new_status
        if new_status in {'approved', 'rejected'} and not shipment.processed_at:
            shipment.processed_at = timezone.now()
        shipment.save()

        StatusLog.objects.create(
            shipment=shipment,
            changed_by=request.user,
            old_status=old_status,
            new_status=new_status,
            notes=notes or f'Supervisor marked shipment {shipment.get_status_display()}.',
        )
        notify_shipment_status_change(
            shipment=shipment,
            old_status=old_status,
            new_status=new_status,
            changed_by=request.user,
            notes=notes,
        )
        messages.success(request, f'Shipment {shipment.hawb_number} marked {shipment.get_status_display()}.')

    return redirect('supervisor:dashboard')


@login_required
@supervisor_required
def delete_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment = get_object_or_404(Shipment, id=shipment_id)
        hawb     = shipment.hawb_number

        # Persist audit record to server logs BEFORE deleting.
        # StatusLog can't survive (CASCADE), so we write to the application log
        # which is retained by Railway and can be reviewed later.
        logger.warning(
            'AUDIT: Shipment %s (consignee=%s, status=%s) permanently deleted by supervisor %s at %s',
            hawb,
            shipment.consignee.username,
            shipment.status,
            request.user.username,
            timezone.now().isoformat(),
        )

        shipment.delete()
        messages.success(request, f'Shipment {hawb} permanently deleted.')
    return redirect('supervisor:dashboard')


# ─── Feedback Management ──────────────────────────────────────────────────────

@login_required
@supervisor_required
def manage_feedbacks(request):
    feedbacks = Feedback.objects.select_related('consignee', 'shipment').order_by('-created_at')
    return render(request, 'supervisor/feedbacks.html', {'feedbacks': feedbacks})


@login_required
@supervisor_required
def approve_feedback(request, feedback_id):
    if request.method == 'POST':
        fb = get_object_or_404(Feedback, id=feedback_id)
        fb.is_approved = True
        fb.save()
        messages.success(request, 'Feedback approved — it will now appear on the landing page.')
    return redirect('supervisor:feedbacks')


@login_required
@supervisor_required
def reject_feedback(request, feedback_id):
    if request.method == 'POST':
        fb = get_object_or_404(Feedback, id=feedback_id)
        fb.delete()
        messages.success(request, 'Feedback removed.')
    return redirect('supervisor:feedbacks')
