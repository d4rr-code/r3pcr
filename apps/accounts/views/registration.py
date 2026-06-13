"""Self-registration for consignees (creates an inactive, pending-approval
account and notifies supervisors)."""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.conf import settings
from ..models import User
from .common import _send_mail_async, redirect_by_role
from .validators import (
    _generate_username, _validate_profile_fields, _validate_password_strength,
    _normalize_phone_number,
)


def register_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        first_name   = request.POST.get('first_name', '').strip()
        last_name    = request.POST.get('last_name', '').strip()
        email        = request.POST.get('email', '').strip()
        password     = request.POST.get('password', '')
        password2    = request.POST.get('password2', '')
        company_name = request.POST.get('company_name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()

        # ── Auto-generate username ──
        username = _generate_username(first_name, last_name)

        # ── Validation ──
        errors = _validate_profile_fields(first_name, last_name, email, phone_number, company_name)
        errors.extend(_validate_password_strength(password))
        if password != password2:
            errors.append('Passwords do not match.')
        if email and User.objects.filter(email=email).exists():
            errors.append('Email already registered.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'accounts/register.html', {
                'form_data': request.POST,
            })

        # Store the phone in canonical 09xxxxxxxxx form.
        phone_number = _normalize_phone_number(phone_number)

        # ── Create inactive user pending supervisor approval ──
        User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role='consignee',
            company_name=company_name,
            phone_number=phone_number,
            is_active=False,
            is_pending_approval=True,
        )

        # ── Notify all supervisors ──
        supervisors = User.objects.filter(role='supervisor', is_active=True)
        for sup in supervisors:
            _send_mail_async(
                subject='R3-PCR — New Registration Pending Approval',
                message=(
                    f'{first_name} {last_name} ({username}) has registered '
                    f'and is awaiting your approval.\n\n'
                    f'Email: {email}\nCompany: {company_name or "—"}'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[sup.email],
                html_message=f'''
                    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
                        <h2 style="color:#3b82f6;">R3-PCR — New Registration</h2>
                        <p>A new consignee has registered and needs approval:</p>
                        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
                            <tr><td style="color:#94a3b8;padding:4px 0;">Name</td>
                                <td style="font-weight:600;">{first_name} {last_name}</td></tr>
                            <tr><td style="color:#94a3b8;padding:4px 0;">Username</td>
                                <td>{username}</td></tr>
                            <tr><td style="color:#94a3b8;padding:4px 0;">Email</td>
                                <td>{email}</td></tr>
                            <tr><td style="color:#94a3b8;padding:4px 0;">Company</td>
                                <td>{company_name or "—"}</td></tr>
                        </table>
                        <p>Log in to the supervisor panel to approve or reject.</p>
                    </div>
                ''',
                log_tag=f'new registration notify to {sup.username}',
            )

        messages.success(
            request,
            f'Registration submitted! Your username is "{username}". '
            'Your account is pending supervisor approval — you will receive an email once approved.'
        )
        return redirect('accounts:login')

    return render(request, 'accounts/register.html')
