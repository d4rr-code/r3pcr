import logging
import re
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from apps.accounts.models import User
from apps.accounts.views import _validate_phone_number

logger = logging.getLogger(__name__)

from .common import *  # noqa: F401,F403

@login_required
@supervisor_required
def user_management(request):
    users   = User.objects.filter(is_pending_approval=False).order_by('role', 'username')
    pending = User.objects.filter(is_pending_approval=True).order_by('date_joined')
    user_stats = {
        'total': users.count(),
        'consignees': users.filter(role='consignee').count(),
        'declarants': users.filter(role='declarant').count(),
        'active': users.filter(is_active=True).count(),
        'inactive': users.filter(is_active=False).count(),
    }
    return render(request, 'supervisor/users.html', {
        'users':   users,
        'pending': pending,
        'user_stats': user_stats,
    })


@login_required
@supervisor_required
def approve_registration(request, user_id):
    user = get_object_or_404(User, id=user_id, is_pending_approval=True)
    if request.method == 'POST':
        if not user.email_verified:
            messages.error(request, f'Cannot approve {user.username} until the email address is verified.')
            return redirect('supervisor:users')

        user.is_active           = True
        user.is_pending_approval = False
        user.save()

        if user.email:
            _send_mail_async(
                subject='R3-PCR - Account Approved',
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
                subject='R3-PCR - Registration Not Approved',
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
    form_data = {}
    if request.method == 'POST':
        username   = request.POST.get('username', '').strip()
        email      = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        role       = request.POST.get('role', '').strip()
        phone      = request.POST.get('phone_number', '').strip()
        company    = request.POST.get('company_name', '').strip()
        password   = request.POST.get('password', '')
        confirm    = request.POST.get('confirm_password', '')
        form_data  = request.POST

        errors = []
        if not all([first_name, last_name, username, email, role, password, confirm]):
            errors.append('Please complete all required fields.')
        if role not in dict(User.ROLE_CHOICES):
            errors.append('Please select a valid role.')
        if User.objects.filter(username=username).exists():
            errors.append('Username already taken.')
        if User.objects.filter(email=email).exists():
            errors.append('Email already registered.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        phone_error = _validate_phone_number(phone)
        if phone_error:
            errors.append(phone_error)
        if not re.search(r'[a-z]', password):
            errors.append('Password must include at least one lowercase letter.')
        if not re.search(r'[A-Z]', password):
            errors.append('Password must include at least one uppercase letter.')
        if not re.search(r'\d', password):
            errors.append('Password must include at least one number.')
        if not re.search(r'[^A-Za-z0-9]', password):
            errors.append('Password must include at least one special character.')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            User.objects.create_user(
                username=username, email=email,
                first_name=first_name, last_name=last_name,
                role=role, phone_number=phone, company_name=company,
                password=password,
            )
            messages.success(request, f'User {username} created.')
            return redirect('supervisor:users')

    return render(request, 'supervisor/add_user.html', {
        'mode': 'add',
        'form_data': form_data,
        'role_choices': User.ROLE_CHOICES,
    })


@login_required
@supervisor_required
def edit_user(request, user_id):
    edited_user = get_object_or_404(User, id=user_id)
    form_data = None

    if request.method == 'POST':
        username   = request.POST.get('username', '').strip()
        email      = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        phone      = request.POST.get('phone_number', '').strip()
        company    = request.POST.get('company_name', '').strip()
        form_data  = request.POST

        errors = []
        if not all([first_name, last_name, username, email]):
            errors.append('Please complete all required fields.')
        if User.objects.filter(username=username).exclude(pk=edited_user.pk).exists():
            errors.append('Username already taken.')
        if User.objects.filter(email=email).exclude(pk=edited_user.pk).exists():
            errors.append('Email already registered.')
        phone_error = _validate_phone_number(phone)
        if phone_error:
            errors.append(phone_error)

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            edited_user.first_name = first_name
            edited_user.last_name = last_name
            edited_user.email = email
            edited_user.phone_number = phone
            edited_user.company_name = company
            edited_user.username = username
            edited_user.save()
            messages.success(request, f'User {edited_user.username} updated.')
            return redirect('supervisor:users')

    return render(request, 'supervisor/add_user.html', {
        'mode': 'edit',
        'edited_user': edited_user,
        'form_data': form_data,
        'role_choices': User.ROLE_CHOICES,
    })


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


#  Analytics (merged command centre) 
