import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, F, ExpressionWrapper, DurationField
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)
from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, StatusLog
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback
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
    all_shipments = Shipment.objects.all()

    q         = request.GET.get('q', '').strip()
    status_f  = request.GET.get('status', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to   = request.GET.get('date_to', '').strip()

    shipments = all_shipments.order_by('-submitted_at')
    if q:
        shipments = shipments.filter(
            hawb_number__icontains=q
        ) | all_shipments.filter(
            consignee__first_name__icontains=q
        ) | all_shipments.filter(
            consignee__last_name__icontains=q
        ) | all_shipments.filter(
            consignee__username__icontains=q
        )
        shipments = shipments.order_by('-submitted_at')
    if status_f:
        shipments = shipments.filter(status=status_f)
    if date_from:
        shipments = shipments.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments = shipments.filter(submitted_at__date__lte=date_to)

    context = {
        'total':            all_shipments.count(),
        'pending':          all_shipments.filter(status='pending').count(),
        'in_review':        all_shipments.filter(status='in_review').count(),
        'approved':         all_shipments.filter(status='approved').count(),
        'rejected':         all_shipments.filter(status='rejected').count(),
        'recent':           shipments,
        'total_users':      User.objects.count(),
        'total_consignees': User.objects.filter(role='consignee').count(),
        'total_declarants': User.objects.filter(role='declarant').count(),
        'q':         q,
        'status_f':  status_f,
        'date_from': date_from,
        'date_to':   date_to,
    }
    return render(request, 'supervisor/dashboard.html', context)


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
            try:
                send_mail(
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
                )
            except Exception as ex:
                print(f'Approval email error: {ex}')

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
            try:
                send_mail(
                    subject='R3-PCR — Registration Not Approved',
                    message=(
                        f'Hello {name},\n\nUnfortunately your R3-PCR registration was not approved. '
                        f'Please contact the administrator for more information.'
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                )
            except Exception as ex:
                print(f'Rejection email error: {ex}')
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


# ─── Analytics ────────────────────────────────────────────────────────────────

@login_required
@supervisor_required
def analytics(request):
    # ── Filters ──────────────────────────────────────────────────────────────
    date_from      = request.GET.get('date_from', '').strip()
    date_to        = request.GET.get('date_to', '').strip()
    declarant_filter = request.GET.get('declarant', '').strip()

    shipments = Shipment.objects.all()

    if date_from:
        shipments = shipments.filter(submitted_at__date__gte=date_from)
    if date_to:
        shipments = shipments.filter(submitted_at__date__lte=date_to)
    if declarant_filter:
        shipments = shipments.filter(declarant__username=declarant_filter)

    total      = shipments.count()
    declarants = User.objects.filter(role='declarant')

    # Declarant performance
    declarant_data = []
    for d in declarants:
        d_ships   = shipments.filter(declarant=d)
        claimed   = d_ships.count()                                # assigned to them
        processed = d_ships.exclude(status__in=['pending','draft']).count()
        approved  = d_ships.filter(status='approved').count()
        rejected  = d_ships.filter(status='rejected').count()
        in_review = d_ships.filter(status='in_review').count()
        computed  = DutyComputation.objects.filter(shipment__declarant=d).count()
        rate      = round((approved / claimed * 100), 1) if claimed > 0 else 0

        approved_with_time = d_ships.filter(status='approved', processed_at__isnull=False)
        avg_days = None
        if approved_with_time.exists():
            deltas = [
                (s.processed_at - s.submitted_at).days
                for s in approved_with_time if s.processed_at
            ]
            if deltas:
                avg_days = round(sum(deltas) / len(deltas), 1)

        declarant_data.append({
            'name':          d.get_full_name() or d.username,
            'username':      d.username,
            'claimed':       claimed,
            'processed':     processed,
            'in_review':     in_review,
            'approved':      approved,
            'rejected':      rejected,
            'computed':      computed,
            'approval_rate': rate,
            'avg_days':      avg_days,
        })

    # Status breakdown
    status_counts = {
        'pending':     shipments.filter(status='pending').count(),
        'in_review':   shipments.filter(status='in_review').count(),
        'for_payment': shipments.filter(status='for_payment').count(),
        'submitted':   shipments.filter(status='submitted').count(),
        'approved':    shipments.filter(status='approved').count(),
        'rejected':    shipments.filter(status='rejected').count(),
    }
    status_pcts = {
        k: round(v / total * 100, 1) if total > 0 else 0
        for k, v in status_counts.items()
    }

    # Top 5 HS codes
    top_hs = (
        DutyComputation.objects
        .filter(hs_code__isnull=False)
        .values('hs_code__code', 'hs_code__description', 'hs_code__duty_rate')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    # ── WMCDA Analytics ──
    advisories = ShippingAdvisory.objects.all()
    total_adv  = advisories.count()

    wmcda_air  = advisories.filter(recommended_type='air').count()
    wmcda_lcl  = advisories.filter(recommended_type='lcl').count()
    wmcda_fcl  = advisories.filter(recommended_type='fcl').count()
    wmcda_land = advisories.filter(recommended_type='land').count()

    avg_air = avg_lcl = avg_fcl = avg_land = None
    if total_adv > 0:
        avgs = advisories.aggregate(
            avg_air=Avg('air_score'),
            avg_lcl=Avg('lcl_score'),
            avg_fcl=Avg('fcl_score'),
            avg_land=Avg('land_score'),
        )
        avg_air  = round(float(avgs['avg_air']  or 0), 3)
        avg_lcl  = round(float(avgs['avg_lcl']  or 0), 3)
        avg_fcl  = round(float(avgs['avg_fcl']  or 0), 3)
        avg_land = round(float(avgs['avg_land'] or 0), 3)

    recent_advisories = (
        advisories
        .select_related('shipment', 'computed_by')
        .order_by('-computed_at')[:8]
    )

    # Scoreboard per shipment type (consignee-declared mode)
    mode_scoreboard = []
    for mode, label, emoji in [
        ('air',  'Air Freight', '✈️'),
        ('lcl',  'LCL',        '🚢'),
        ('fcl',  'FCL',        '📦'),
        ('land', 'Land Freight','🚛'),
    ]:
        mode_advs = advisories.filter(shipment__shipment_type=mode)
        count     = mode_advs.count()
        if count:
            m_avgs = mode_advs.aggregate(
                avg_air=Avg('air_score'),
                avg_lcl=Avg('lcl_score'),
                avg_fcl=Avg('fcl_score'),
                avg_land=Avg('land_score'),
            )
            rec_counts = mode_advs.values('recommended_type').annotate(n=Count('id')).order_by('-n')
            top_rec    = rec_counts[0] if rec_counts else None
            mode_scoreboard.append({
                'mode':    mode, 'label': label, 'emoji': emoji, 'count': count,
                'avg_air':  round(float(m_avgs['avg_air']  or 0), 3),
                'avg_lcl':  round(float(m_avgs['avg_lcl']  or 0), 3),
                'avg_fcl':  round(float(m_avgs['avg_fcl']  or 0), 3),
                'avg_land': round(float(m_avgs['avg_land'] or 0), 3),
                'top_rec':     top_rec['recommended_type'] if top_rec else None,
                'top_rec_pct': round(top_rec['n'] / count * 100) if top_rec else 0,
            })
        else:
            mode_scoreboard.append({
                'mode': mode, 'label': label, 'emoji': emoji, 'count': 0,
                'avg_air': 0, 'avg_lcl': 0, 'avg_fcl': 0, 'avg_land': 0,
                'top_rec': None, 'top_rec_pct': 0,
            })

    wmcda = {
        'total':           total_adv,
        'air':             wmcda_air,
        'lcl':             wmcda_lcl,
        'fcl':             wmcda_fcl,
        'land':            wmcda_land,
        'avg_air':         avg_air,
        'avg_lcl':         avg_lcl,
        'avg_fcl':         avg_fcl,
        'avg_land':        avg_land,
        'pct_air':         round(wmcda_air  / total_adv * 100) if total_adv > 0 else 0,
        'pct_lcl':         round(wmcda_lcl  / total_adv * 100) if total_adv > 0 else 0,
        'pct_fcl':         round(wmcda_fcl  / total_adv * 100) if total_adv > 0 else 0,
        'pct_land':        round(wmcda_land / total_adv * 100) if total_adv > 0 else 0,
        'recent':          recent_advisories,
        'mode_scoreboard': mode_scoreboard,
    }

    # ── Processing Time Analytics ──
    approved_ships = shipments.filter(status='approved', processed_at__isnull=False)
    processing_stats = {'avg': None, 'min': None, 'max': None, 'count': 0}
    if approved_ships.exists():
        deltas = [
            (s.processed_at - s.submitted_at).days
            for s in approved_ships if s.processed_at and s.submitted_at
        ]
        if deltas:
            processing_stats = {
                'avg':   round(sum(deltas) / len(deltas), 1),
                'min':   min(deltas),
                'max':   max(deltas),
                'count': len(deltas),
            }

    # Days-bucket distribution  (0-1 / 2-3 / 4-7 / 8+)
    buckets = {'fast': 0, 'normal': 0, 'slow': 0, 'very_slow': 0}
    if approved_ships.exists():
        for s in approved_ships:
            if s.processed_at and s.submitted_at:
                d = (s.processed_at - s.submitted_at).days
                if d <= 1:
                    buckets['fast'] += 1
                elif d <= 3:
                    buckets['normal'] += 1
                elif d <= 7:
                    buckets['slow'] += 1
                else:
                    buckets['very_slow'] += 1

    context = {
        'status_data':        status_counts,
        'status_pcts':        status_pcts,
        'declarant_data':     declarant_data,
        'total_shipments':    total,
        'top_hs':             list(top_hs),
        'wmcda':              wmcda,
        'processing_stats':   processing_stats,
        'processing_buckets': buckets,
        # Filters
        'date_from':          date_from,
        'date_to':            date_to,
        'declarant_filter':   declarant_filter,
        'declarants':         declarants,
    }
    return render(request, 'supervisor/analytics.html', context)


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

def _get_config():
    """Return a SimpleNamespace with all SystemConfig values as floats/strings."""
    from types import SimpleNamespace
    defaults = {
        'exchange_rate':  '59.1480',
        'vat_rate':       '12.00',
        'wmcda_w_cost':   '35',
        'wmcda_w_time':   '30',
        'wmcda_w_weight': '20',
        'wmcda_w_risk':   '15',
    }
    rows = {sc.key: sc.value for sc in SystemConfig.objects.all()}
    merged = {k: rows.get(k, v) for k, v in defaults.items()}
    return SimpleNamespace(**merged)


@login_required
@supervisor_required
def system_config(request):
    config   = _get_config()
    hs_codes = HSCode.objects.filter(is_active=True).order_by('code')

    if request.method == 'POST':
        # ── Save exchange rate + VAT ───────────────────────────────────────
        for key in ('exchange_rate', 'vat_rate',
                    'wmcda_w_cost', 'wmcda_w_time', 'wmcda_w_weight', 'wmcda_w_risk'):
            val = request.POST.get(key, '').strip()
            if val:
                SystemConfig.objects.update_or_create(
                    key=key,
                    defaults={'value': val, 'updated_by': request.user},
                )

        # ── Save HS code duty rates ────────────────────────────────────────
        hs_ids   = request.POST.getlist('hs_id[]')
        hs_rates = request.POST.getlist('hs_rate[]')
        for hs_id, rate in zip(hs_ids, hs_rates):
            try:
                hs       = HSCode.objects.get(id=int(hs_id))
                rate_val = float(rate)
                if not (0 <= rate_val <= 100):
                    messages.warning(
                        request,
                        f'Duty rate for HS {hs.code} must be between 0 and 100%. Skipped.'
                    )
                    continue
                hs.duty_rate = rate_val
                hs.save(update_fields=['duty_rate'])
            except (HSCode.DoesNotExist, ValueError):
                pass

        messages.success(request, 'Configuration saved.')
        return redirect('supervisor:config')

    return render(request, 'supervisor/config.html', {
        'config':   config,
        'hs_codes': hs_codes,
    })


# ─── Shipment Admin Actions ───────────────────────────────────────────────────

@login_required
@supervisor_required
def reset_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment   = get_object_or_404(Shipment, id=shipment_id)
        old_status = shipment.status
        hawb       = shipment.hawb_number

        shipment.status        = 'pending'
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
            new_status='pending',
            notes='Reset to pending by supervisor. Computation cleared.',
        )
        messages.success(request, f'Shipment {hawb} reset to Pending.')
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
