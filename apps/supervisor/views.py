import logging
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, F, ExpressionWrapper, DurationField
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

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
        'incoming':         all_shipments.filter(status='incoming').count(),
        'arrived':          all_shipments.filter(status='arrived').count(),
        'computed':         all_shipments.filter(status='computed').count(),
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

    total = shipments.count()
    status_labels = dict(Shipment.STATUS_CHOICES)
    status_colors = {
        'incoming': '#f59e0b',
        'arrived': '#3b82f6',
        'computed': '#8b5cf6',
        'approved': '#22c55e',
        'rejected': '#ef4444',
        'for_revision': '#f97316',
        'lodgement': '#38bdf8',
        'ongoing': '#64748b',
        'assessed': '#14b8a6',
        'paid': '#84cc16',
        'released': '#22c55e',
        'billed': '#a855f7',
    }
    status_rows = []
    for key, label in Shipment.STATUS_CHOICES:
        count = shipments.filter(status=key).count()
        status_rows.append({
            'key': key,
            'label': label,
            'count': count,
            'pct': round(count / total * 100, 1) if total else 0,
            'color': status_colors.get(key, '#475569'),
        })

    advisory_base = ShippingAdvisory.objects.filter(shipment__in=shipments)
    wmcda_scoreboard = [
        {
            'key': key,
            'label': label,
            'count': advisory_base.filter(recommended_type=key).count(),
        }
        for key, label in [('lcl', 'LCL'), ('fcl', 'FCL'), ('air', 'Air Freight')]
    ]
    max_wmcda = max([row['count'] for row in wmcda_scoreboard] or [0])
    for row in wmcda_scoreboard:
        row['pct'] = round(row['count'] / max_wmcda * 100) if max_wmcda else 0

    declarants = User.objects.filter(role='declarant').order_by('first_name', 'username')
    declarant_data = []
    for declarant in declarants:
        d_shipments = shipments.filter(declarant=declarant)
        computed_logs = (
            StatusLog.objects
            .filter(shipment__in=d_shipments, new_status='computed')
            .select_related('shipment')
            .order_by('changed_at')
        )
        computed_by_shipment = {}
        for log in computed_logs:
            computed_by_shipment.setdefault(log.shipment_id, log)

        durations = []
        for shipment_id, computed_log in computed_by_shipment.items():
            arrived_log = (
                StatusLog.objects
                .filter(shipment_id=shipment_id, new_status='arrived', changed_at__lte=computed_log.changed_at)
                .order_by('-changed_at')
                .first()
            )
            if arrived_log:
                durations.append(computed_log.changed_at - arrived_log.changed_at)

        avg_hours = None
        if durations:
            avg_seconds = sum(delta.total_seconds() for delta in durations) / len(durations)
            avg_hours = round(avg_seconds / 3600, 1)

        total_computed = len(computed_by_shipment)
        approved = d_shipments.filter(status='approved').count()
        approval_rate = round(approved / total_computed * 100, 1) if total_computed else 0

        declarant_data.append({
            'name': declarant.get_full_name() or declarant.username,
            'username': declarant.username,
            'total_processed': total_computed,
            'avg_hours': avg_hours,
            'approved': approved,
            'approval_rate': approval_rate,
        })

    return render(request, 'supervisor/analytics.html', {
        'status_rows': status_rows,
        'wmcda_scoreboard': wmcda_scoreboard,
        'declarant_data': declarant_data,
        'total_shipments': total,
        'date_from': date_from,
        'date_to': date_to,
        'declarant_filter': declarant_filter,
        'declarants': declarants,
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

    # Gather "last updated" metadata for key configs to display in template
    config_meta = {
        row.key: row
        for row in SystemConfig.objects.filter(
            key__in=['exchange_rate', 'vat_rate',
                     'wmcda_w_cost', 'wmcda_w_time', 'wmcda_w_weight', 'wmcda_w_risk']
        ).select_related('updated_by')
    }

    return render(request, 'supervisor/config.html', {
        'config':      config,
        'hs_codes':    hs_codes,
        'config_meta': config_meta,
    })


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
