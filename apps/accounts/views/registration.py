"""Self-registration for consignees with email OTP verification."""
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from ..models import OTP, User
from .common import _send_mail_async, logger, redirect_by_role
from .validators import (
    _generate_username,
    _validate_password_strength,
    _validate_profile_fields,
)


def _is_local_email_backend():
    return (
        'console' in getattr(settings, 'EMAIL_BACKEND', '')
        or getattr(settings, 'REGISTRATION_EMAIL_DEV_LINKS', False)
    )


def _send_registration_otp(request, user):
    OTP.objects.filter(user=user, is_used=False).update(is_used=True)
    otp_code = OTP.generate_code()
    OTP.objects.create(user=user, code=otp_code)

    if _is_local_email_backend():
        logger.info('[DEV REGISTRATION OTP] User: %s | Code: %s', user.username, otp_code)
        messages.info(request, f'[DEV] Registration OTP: {otp_code} - check your terminal.')
        return

    _send_mail_async(
        subject='R3-PCR Email Verification OTP',
        message=f'Your R3-PCR registration OTP is: {otp_code}\nExpires in 10 minutes.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=f'''
            <div style="font-family:Arial,sans-serif;max-width:420px;margin:0 auto;">
                <h2 style="color:#3b82f6;">R3-PCR Email Verification</h2>
                <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                <p>Use this OTP to verify your email address:</p>
                <h1 style="color:#3b82f6;letter-spacing:8px;">{otp_code}</h1>
                <p>Expires in <strong>10 minutes</strong>.</p>
                <p style="color:#64748b;font-size:12px;">
                    Your account will still need supervisor approval after email verification.
                </p>
            </div>
        ''',
        log_tag=f'registration OTP for {user.username}',
    )
    messages.success(request, 'Registration OTP sent to your email.')


def _notify_supervisors(user):
    supervisors = User.objects.filter(role='supervisor', is_active=True)
    for sup in supervisors:
        _send_mail_async(
            subject='R3-PCR - New Registration Pending Approval',
            message=(
                f'{user.get_full_name() or user.username} ({user.username}) has registered '
                f'and is awaiting approval.\n\n'
                f'Email: {user.email}\n'
                f'Email verified: {"Yes" if user.email_verified else "No"}\n'
                f'Company: {user.company_name or "-"}'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[sup.email],
            html_message=f'''
                <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;">
                    <h2 style="color:#3b82f6;">R3-PCR - New Registration</h2>
                    <p>A new consignee has registered and needs approval:</p>
                    <table style="width:100%;border-collapse:collapse;margin:16px 0;">
                        <tr><td style="color:#94a3b8;padding:4px 0;">Name</td>
                            <td style="font-weight:600;">{user.get_full_name() or user.username}</td></tr>
                        <tr><td style="color:#94a3b8;padding:4px 0;">Username</td>
                            <td>{user.username}</td></tr>
                        <tr><td style="color:#94a3b8;padding:4px 0;">Email</td>
                            <td>{user.email}</td></tr>
                        <tr><td style="color:#94a3b8;padding:4px 0;">Email Verified</td>
                            <td>{"Yes" if user.email_verified else "No"}</td></tr>
                        <tr><td style="color:#94a3b8;padding:4px 0;">Company</td>
                            <td>{user.company_name or "-"}</td></tr>
                    </table>
                    <p>Log in to the supervisor panel to approve or reject.</p>
                </div>
            ''',
            log_tag=f'new registration notify to {sup.username}',
        )


def register_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        company_name = request.POST.get('company_name', '').strip()

        username = _generate_username(first_name, last_name)
        errors = _validate_profile_fields(first_name, last_name, email, '', company_name)
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

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role='consignee',
            company_name=company_name,
            email_verified=False,
            is_active=False,
            is_pending_approval=True,
        )

        request.session['pending_registration_user_id'] = user.id
        _send_registration_otp(request, user)
        _notify_supervisors(user)
        messages.success(
            request,
            f'Registration submitted! Your username is "{username}". Verify your email to continue.'
        )
        return redirect('accounts:verify_registration_email')

    return render(request, 'accounts/register.html')


def verify_registration_email(request):
    user_id = request.session.get('pending_registration_user_id')
    if not user_id:
        messages.error(request, 'Registration verification session expired. Please register again or log in if already approved.')
        return redirect('accounts:register')

    if request.method == 'POST':
        entered_code = request.POST.get('otp_code', '').strip()
        try:
            user = User.objects.get(id=user_id, is_pending_approval=True)
            otp = OTP.objects.filter(user=user, is_used=False).latest('created_at')
        except (User.DoesNotExist, OTP.DoesNotExist):
            messages.error(request, 'Verification code expired. Please request a new OTP.')
            return redirect('accounts:verify_registration_email')

        if otp.is_valid() and otp.code == entered_code:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            user.email_verified = True
            user.save(update_fields=['email_verified', 'updated_at'])
            request.session.pop('pending_registration_user_id', None)
            messages.success(
                request,
                'Email verified. Your account is now waiting for supervisor approval.'
            )
            return redirect('accounts:login')

        messages.error(request, 'Invalid or expired OTP. Please try again.')

    dev_otp = None
    if _is_local_email_backend():
        try:
            otp = OTP.objects.filter(user_id=user_id, is_used=False).latest('created_at')
            if otp.is_valid():
                dev_otp = otp.code
        except OTP.DoesNotExist:
            pass

    return render(request, 'accounts/verify_otp.html', {
        'dev_otp': dev_otp,
        'otp_title': 'Verify your Email',
        'otp_subtitle': 'Enter the 6-digit OTP sent to your registration email. It expires in 10 minutes.',
        'resend_url_name': 'accounts:resend_registration_otp',
        'back_url_name': 'accounts:register',
        'back_label': 'Back to Registration',
    })


def resend_registration_otp(request):
    user_id = request.session.get('pending_registration_user_id')
    if not user_id:
        messages.error(request, 'Registration verification session expired.')
        return redirect('accounts:register')

    try:
        user = User.objects.get(id=user_id, is_pending_approval=True)
    except User.DoesNotExist:
        messages.error(request, 'Registration could not be found.')
        return redirect('accounts:register')

    _send_registration_otp(request, user)
    return redirect('accounts:verify_registration_email')
