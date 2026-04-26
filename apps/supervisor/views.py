from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.accounts.models import User
from apps.shipments.models import Shipment
from apps.computation.models import DutyComputation

@login_required
def dashboard(request):
    # Only supervisors can access
    if request.user.role != 'supervisor':
        return redirect('accounts:login')

    shipments = Shipment.objects.all()
    total = shipments.count()
    pending = shipments.filter(status='pending').count()
    in_review = shipments.filter(status='in_review').count()
    approved = shipments.filter(status='approved').count()
    rejected = shipments.filter(status='rejected').count()

    # Recent shipments
    recent = shipments.order_by('-submitted_at')[:10]

    # User counts
    total_users = User.objects.count()
    total_consignees = User.objects.filter(role='consignee').count()
    total_declarants = User.objects.filter(role='declarant').count()

    context = {
        'total': total,
        'pending': pending,
        'in_review': in_review,
        'approved': approved,
        'rejected': rejected,
        'recent': recent,
        'total_users': total_users,
        'total_consignees': total_consignees,
        'total_declarants': total_declarants,
    }
    return render(request, 'supervisor/dashboard.html', context)

@login_required
def user_management(request):
    if request.user.role != 'supervisor':
        return redirect('accounts:login')

    users = User.objects.all().order_by('role', 'username')
    return render(request, 'supervisor/users.html', {'users': users})

@login_required
def add_user(request):
    if request.user.role != 'supervisor':
        return redirect('accounts:login')

    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        role = request.POST.get('role')
        password = request.POST.get('password')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Email already exists.')
        else:
            User.objects.create_user(
                username=username,
                email=email,
                first_name=first_name,
                last_name=last_name,
                role=role,
                password=password
            )
            messages.success(request, f'User {username} created successfully!')
            return redirect('supervisor:users')

    return render(request, 'supervisor/add_user.html')

@login_required
def toggle_user(request, user_id):
    if request.user.role != 'supervisor':
        return redirect('accounts:login')

    user = get_object_or_404(User, id=user_id)
    if user != request.user:
        user.is_active = not user.is_active
        user.save()
        status = 'activated' if user.is_active else 'deactivated'
        messages.success(request, f'User {user.username} {status}.')
    else:
        messages.error(request, 'You cannot deactivate yourself.')

    return redirect('supervisor:users')

@login_required
def analytics(request):
    if request.user.role != 'supervisor':
        return redirect('accounts:login')

    shipments = Shipment.objects.all()

    # Status breakdown
    status_data = {
        'pending': shipments.filter(status='pending').count(),
        'in_review': shipments.filter(status='in_review').count(),
        'approved': shipments.filter(status='approved').count(),
        'rejected': shipments.filter(status='rejected').count(),
    }

    # Declarant performance
    declarants = User.objects.filter(role='declarant')
    declarant_data = []
    for d in declarants:
        declarant_data.append({
            'name': d.get_full_name() or d.username,
            'total': shipments.filter(declarant=d).count(),
            'approved': shipments.filter(declarant=d, status='approved').count(),
            'rejected': shipments.filter(declarant=d, status='rejected').count(),
        })

    context = {
        'status_data': status_data,
        'declarant_data': declarant_data,
        'total_shipments': shipments.count(),
    }
    return render(request, 'supervisor/analytics.html', context)