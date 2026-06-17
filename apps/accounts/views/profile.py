"""Account settings: profile, username, and password self-service updates."""
import re
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from ..models import User
from .validators import _validate_profile_fields


@login_required
def account_settings(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        user   = request.user

        if action == 'profile':
            first_name = request.POST.get('first_name', '').strip()
            last_name  = request.POST.get('last_name',  '').strip()
            email      = request.POST.get('email', '').strip()
            phone      = request.POST.get('phone_number', '').strip()
            company    = request.POST.get('company_name', '').strip()

            errors = _validate_profile_fields(first_name, last_name, email, phone, company)
            if errors:
                for e in errors:
                    messages.error(request, e)
                return redirect('accounts:settings')

            if email != user.email:
                if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                    messages.error(request, 'Email already in use by another account.')
                    return redirect('accounts:settings')
                user.email = email

            user.first_name   = first_name
            user.last_name    = last_name
            user.phone_number = phone or None
            user.company_name = company
            user.save()
            messages.success(request, 'Profile updated.')

        elif action == 'username':
            new_username = request.POST.get('new_username', '').strip()
            if not new_username:
                messages.error(request, 'Username cannot be empty.')
            elif len(new_username) < 3:
                messages.error(request, 'Username must be at least 3 characters.')
            elif not re.match(r'^[a-zA-Z0-9_.]+$', new_username):
                messages.error(request, 'Username can only contain letters, numbers, underscores, and dots.')
            elif new_username == user.username:
                messages.error(request, 'That is already your current username.')
            elif User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                messages.error(request, 'Username already taken. Please choose another.')
            else:
                user.username = new_username
                user.save()
                messages.success(request, f'Username changed to "{new_username}" successfully.')

        elif action == 'password':
            old_password     = request.POST.get('old_password', '')
            new_password     = request.POST.get('new_password', '')
            confirm_password = request.POST.get('confirm_password', '')

            if not user.check_password(old_password):
                messages.error(request, 'Current password is incorrect.')
            elif new_password != confirm_password:
                messages.error(request, 'New passwords do not match.')
            elif len(new_password) < 8:
                messages.error(request, 'Password must be at least 8 characters.')
            else:
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully.')

        return redirect('accounts:settings')

    role = request.user.role
    if role == 'consignee':
        template = 'consignee/settings.html'
    elif role == 'supervisor':
        template = 'supervisor/settings.html'
    elif role == 'declarant':
        template = 'declarant/settings.html'
    else:
        template = 'accounts/settings.html'
    return render(request, template)
