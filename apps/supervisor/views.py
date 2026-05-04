from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, F, ExpressionWrapper, DurationField
from django.utils import timezone
from apps.accounts.models import User
from apps.shipments.models import Shipment, HSCode, StatusLog
from apps.computation.models import DutyComputation
from .models import SystemConfig


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

    # ── Filters ──
    q          = request.GET.get('q', '').strip()
    status_f   = request.GET.get('status', '').strip()
    date_from  = request.GET.get('date_from', '').strip()
    date_to    = request.GET.get('date_to', '').strip()

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
        # pass filter state back to template
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
    users = User.objects.all().order_by('role', 'username')
    return render(request, 'supervisor/users.html', {'users': users})


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
    shipments  = Shipment.objects.all()
    total      = shipments.count()
    declarants = User.objects.filter(role='declarant')

    # Declarant performance
    declarant_data = []
    for d in declarants:
        d_ships   = shipments.filter(declarant=d)
        total_d   = d_ships.count()
        approved  = d_ships.filter(status='approved').count()
        rejected  = d_ships.filter(status='rejected').count()
        rate      = round((approved / total_d * 100), 1) if total_d > 0 else 0

        # Average processing time (days) for approved
        approved_with_time = d_ships.filter(
            status='approved', processed_at__isnull=False
        )
        avg_days = None
        if approved_with_time.exists():
            deltas = [
                (s.processed_at - s.submitted_at).days
                for s in approved_with_time
                if s.processed_at
            ]
            if deltas:
                avg_days = round(sum(deltas) / len(deltas), 1)

        declarant_data.append({
            'name':         d.get_full_name() or d.username,
            'total':        total_d,
            'approved':     approved,
            'rejected':     rejected,
            'approval_rate': rate,
            'avg_days':     avg_days,
        })

    # Status breakdown with percentages for CSS bars
    status_counts = {
        'pending':    shipments.filter(status='pending').count(),
        'in_review':  shipments.filter(status='in_review').count(),
        'for_payment': shipments.filter(status='for_payment').count(),
        'submitted':  shipments.filter(status='submitted').count(),
        'approved':   shipments.filter(status='approved').count(),
        'rejected':   shipments.filter(status='rejected').count(),
    }
    status_pcts = {
        k: round(v / total * 100, 1) if total > 0 else 0
        for k, v in status_counts.items()
    }

    # Top 5 most-used HS codes
    top_hs = (
        DutyComputation.objects
        .filter(hs_code__isnull=False)
        .values('hs_code__code', 'hs_code__description', 'hs_code__duty_rate')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    context = {
        'status_data':    status_counts,
        'status_pcts':    status_pcts,
        'declarant_data': declarant_data,
        'total_shipments': total,
        'top_hs':         list(top_hs),
    }
    return render(request, 'supervisor/analytics.html', context)


# ─── System Configuration ─────────────────────────────────────────────────────

@login_required
@supervisor_required
def system_config(request):
    if request.method == 'POST':
        try:
            fields_to_save = {
                'exchange_rate': ('Exchange Rate (USD→PHP)',  None),
                'vat_rate':      ('VAT Rate (%)',              None),
                'wmcda_w_cost':  ('WMCDA Weight — Cost',       None),
                'wmcda_w_time':  ('WMCDA Weight — Time',       None),
                'wmcda_w_weight':('WMCDA Weight — Weight/Vol', None),
                'wmcda_w_risk':  ('WMCDA Weight — Risk',       None),
            }
            for key, (label, _) in fields_to_save.items():
                val = request.POST.get(key, '').strip()
                if val:
                    float(val)  # validate numeric
                    SystemConfig.set(key, val, label, request.user)

            # Validate WMCDA weights sum = 100
            try:
                wsum = sum(
                    float(request.POST.get(k, '0') or '0')
                    for k in ('wmcda_w_cost', 'wmcda_w_time', 'wmcda_w_weight', 'wmcda_w_risk')
                )
                if abs(wsum - 100) > 0.5:
                    messages.warning(
                        request,
                        f'WMCDA weights sum to {wsum:.1f}% — they should total 100%.'
                    )
            except ValueError:
                pass

            # HS code duty rate updates
            hs_ids   = request.POST.getlist('hs_id[]')
            hs_rates = request.POST.getlist('hs_rate[]')
            updated_hs = 0
            for hs_id, rate in zip(hs_ids, hs_rates):
                if hs_id and rate:
                    try:
                        hs = HSCode.objects.get(id=hs_id)
                        hs.duty_rate = rate
                        hs.save()
                        updated_hs += 1
                    except (HSCode.DoesNotExist, ValueError):
                        pass

            messages.success(
                request,
                f'Configuration saved. {updated_hs} HS code rate(s) updated.'
            )
        except ValueError as e:
            messages.error(request, f'Invalid value: {e}')

        return redirect('supervisor:config')

    config = {
        'exchange_rate':  SystemConfig.get('exchange_rate',   '59.1480'),
        'vat_rate':       SystemConfig.get('vat_rate',        '12'),
        'wmcda_w_cost':   SystemConfig.get('wmcda_w_cost',    '35'),
        'wmcda_w_time':   SystemConfig.get('wmcda_w_time',    '30'),
        'wmcda_w_weight': SystemConfig.get('wmcda_w_weight',  '20'),
        'wmcda_w_risk':   SystemConfig.get('wmcda_w_risk',    '15'),
    }
    hs_codes = HSCode.objects.filter(is_active=True).order_by('code')
    return render(request, 'supervisor/config.html', {
        'config': config, 'hs_codes': hs_codes,
    })


# ─── Shipment Admin Actions ───────────────────────────────────────────────────

@login_required
@supervisor_required
def reset_shipment(request, shipment_id):
    if request.method == 'POST':
        shipment   = get_object_or_404(Shipment, id=shipment_id)
        old_status = shipment.status
        hawb       = shipment.hawb_number

        shipment.status         = 'pending'
        shipment.declarant      = None
        shipment.boc_reference  = None
        shipment.boc_status     = None
        shipment.processed_at   = None
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
        shipment.delete()
        messages.success(request, f'Shipment {hawb} permanently deleted.')
    return redirect('supervisor:dashboard')
