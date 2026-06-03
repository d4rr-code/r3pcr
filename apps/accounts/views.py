import re
import threading
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from .models import User, OTP


# ─── Field validation helpers ─────────────────────────────────────────────────

def _validate_profile_fields(first_name, last_name, email, phone='', company=''):
    """Return a list of error strings; empty list means all fields are valid."""
    errors = []

    # First name
    if not first_name:
        errors.append('First name is required.')
    elif len(first_name) < 2:
        errors.append('First name must be at least 2 characters.')
    elif len(first_name) > 50:
        errors.append('First name cannot exceed 50 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", first_name):
        errors.append('First name may only contain letters, spaces, hyphens, and apostrophes.')

    # Last name
    if not last_name:
        errors.append('Last name is required.')
    elif len(last_name) < 2:
        errors.append('Last name must be at least 2 characters.')
    elif len(last_name) > 50:
        errors.append('Last name cannot exceed 50 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", last_name):
        errors.append('Last name may only contain letters, spaces, hyphens, and apostrophes.')

    # Email
    if not email:
        errors.append('Email address is required.')
    elif not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$', email):
        errors.append('Enter a valid email address.')

    # Phone (optional) — Philippine format: 09XX-XXX-XXXX or +639XX-XXX-XXXX
    if phone:
        digits_only = re.sub(r'[\s\-().+]', '', phone)
        if not re.match(r'^[0-9+][0-9\s\-().]*$', phone):
            errors.append('Phone number must start with 0 or + and contain only digits, spaces, hyphens, or parentheses.')
        elif not digits_only.isdigit():
            errors.append('Phone number contains invalid characters.')
        elif len(digits_only) < 10:
            errors.append('Phone number must have at least 10 digits. PH format: 09XX-XXX-XXXX or +639XX-XXX-XXXX.')
        elif len(digits_only) > 13:
            errors.append('Phone number cannot exceed 13 digits.')

    # Company (optional)
    if company and len(company) > 100:
        errors.append('Company name cannot exceed 100 characters.')

    return errors


def _send_mail_async(subject, message, from_email, recipient_list, html_message=None, log_tag=''):
    """Send email in a daemon thread so the HTTP response is never blocked."""
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
    t = threading.Thread(target=_send, daemon=True)
    t.start()


# ─── Login ────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # ── Remember Me ──
            if not request.POST.get('remember_me'):
                request.session.set_expiry(0)        # expires when browser closes
            else:
                request.session.set_expiry(30 * 24 * 3600)  # 30 days

            # ── OTP bypass: log in directly if otp_enabled is False ──
            if not user.otp_enabled:
                login(request, user)
                messages.success(request, f'Welcome, {user.first_name or user.username}!')
                return redirect_by_role(user)

            # ── Normal OTP flow ──
            otp_code = OTP.generate_code()
            OTP.objects.create(user=user, code=otp_code)
            request.session['pre_auth_user_id'] = user.id

            _is_local = settings.DEBUG or 'console' in getattr(settings, 'EMAIL_BACKEND', '')
            if _is_local:
                # Local development: print OTP to terminal, no email sent
                print(f'\n{"="*40}')
                print(f'[DEV OTP] User: {user.username}')
                print(f'[DEV OTP] Code: {otp_code}')
                print(f'{"="*40}\n')
                messages.info(request, f'[DEV] OTP: {otp_code} — check your terminal.')
            else:
                _send_mail_async(
                    subject='R3-PCR Login OTP',
                    message=f'Your OTP code is: {otp_code}\nExpires in 10 minutes.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=f'''
                        <div style="font-family:Arial,sans-serif;max-width:400px;margin:0 auto;">
                            <h2 style="color:#3b82f6;">R3-PCR System</h2>
                            <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                            <p>Your OTP code is:</p>
                            <h1 style="color:#3b82f6;letter-spacing:8px;">{otp_code}</h1>
                            <p>Expires in <strong>10 minutes</strong>.</p>
                            <p style="color:#94a3b8;font-size:12px;">
                                If you did not request this, ignore this email.
                            </p>
                        </div>
                    ''',
                    log_tag=f'login OTP for {user.username}',
                )
                messages.success(request, 'OTP sent to your email.')

            return redirect('accounts:verify_otp')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'accounts/login.html')


# ─── OTP Verification ─────────────────────────────────────────────────────────

def verify_otp_view(request):
    user_id = request.session.get('pre_auth_user_id')
    if not user_id:
        return redirect('accounts:login')

    if request.method == 'POST':
        entered_code = request.POST.get('otp_code')
        try:
            user = User.objects.get(id=user_id)
            otp = OTP.objects.filter(user=user, is_used=False).latest('created_at')
            if otp.is_valid() and otp.code == entered_code:
                otp.is_used = True
                otp.save()
                login(request, user)
                del request.session['pre_auth_user_id']
                messages.success(request, f'Welcome, {user.first_name or user.username}!')
                return redirect_by_role(user)
            else:
                messages.error(request, 'Invalid or expired OTP.')
        except (User.DoesNotExist, OTP.DoesNotExist):
            messages.error(request, 'Something went wrong. Please try again.')
            return redirect('accounts:login')

    # ── DEV HINT: surface OTP on screen so testers don't need inbox access ──
    dev_otp = None
    try:
        _hint_user = User.objects.get(id=user_id)
        _hint_otp  = OTP.objects.filter(user=_hint_user, is_used=False).latest('created_at')
        if _hint_otp.is_valid():
            dev_otp = _hint_otp.code
    except Exception:
        pass
    # ── remove the three lines above + dev_otp context key when going live ──

    return render(request, 'accounts/verify_otp.html', {'dev_otp': dev_otp})


# ─── Resend OTP ───────────────────────────────────────────────────────────────

def resend_otp(request):
    user_id = request.session.get('pre_auth_user_id')
    if not user_id:
        return redirect('accounts:login')
    try:
        user = User.objects.get(id=user_id)
        otp_code = OTP.generate_code()
        OTP.objects.create(user=user, code=otp_code)
        _send_mail_async(
            subject='R3-PCR Login OTP (Resent)',
            message=f'Your new OTP code is: {otp_code}\nExpires in 10 minutes.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=f'''
                <div style="font-family:Arial,sans-serif;max-width:400px;margin:0 auto;">
                    <h2 style="color:#3b82f6;">R3-PCR System</h2>
                    <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                    <p>Your new OTP code is:</p>
                    <h1 style="color:#3b82f6;letter-spacing:8px;">{otp_code}</h1>
                    <p>Expires in <strong>10 minutes</strong>.</p>
                </div>
            ''',
            log_tag=f'resend OTP for {user.username}',
        )
        messages.success(request, 'A new OTP has been sent to your email.')
    except User.DoesNotExist:
        messages.error(request, 'Session expired. Please log in again.')
        return redirect('accounts:login')
    return redirect('accounts:verify_otp')


# ─── Self-Registration (Consignee) ────────────────────────────────────────────

def _generate_username(first_name, last_name):
    """Auto-generate a unique username: firstname.lastname, firstname.lastname2, ..."""
    import re
    base = re.sub(r'[^a-z0-9]', '', f'{first_name}{last_name}'.lower())
    if not base:
        base = 'user'
    username = base
    counter  = 2
    while User.objects.filter(username=username).exists():
        username = f'{base}{counter}'
        counter += 1
    return username


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
        if password != password2:
            errors.append('Passwords do not match.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if email and User.objects.filter(email=email).exists():
            errors.append('Email already registered.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'accounts/register.html', {
                'form_data': request.POST,
            })

        # ── Create inactive user pending supervisor approval ──
        user = User.objects.create_user(
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


# ─── Forgot Password ─────────────────────────────────────────────────────────

def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                messages.error(request, 'No account found with that email address.')
            elif not user.is_active:
                messages.error(
                    request,
                    'Your account is still pending supervisor approval. '
                    'You cannot reset your password until your account is activated.'
                )
            else:
                otp_code = OTP.generate_code()
                OTP.objects.create(user=user, code=otp_code)
                request.session['reset_user_id'] = user.id
                _send_mail_async(
                    subject='R3-PCR — Password Reset OTP',
                    message=f'Your password reset OTP is: {otp_code}\nExpires in 10 minutes.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=f'''
                        <div style="font-family:Arial,sans-serif;max-width:400px;margin:0 auto;">
                            <h2 style="color:#1f3d66;">R3-PCR Password Reset</h2>
                            <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                            <p>Your password reset code is:</p>
                            <h1 style="color:#1f3d66;letter-spacing:8px;">{otp_code}</h1>
                            <p>Expires in <strong>10 minutes</strong>.</p>
                            <p style="color:#94a3b8;font-size:12px;">If you did not request this, ignore this email.</p>
                        </div>
                    ''',
                    log_tag=f'password reset OTP for {user.username}',
                )
                messages.success(request, 'OTP sent to your email. Check your inbox.')
                return redirect('accounts:reset_password')
        except Exception:
            messages.error(request, 'Something went wrong. Please try again later.')

    return render(request, 'accounts/forgot_password.html')


# ─── Forgot Username ──────────────────────────────────────────────────────────

def forgot_username(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.filter(email__iexact=email).first()
            if user:
                _send_mail_async(
                    subject='R3-PCR — Your Username',
                    message=f'Your R3-PCR username is: {user.username}',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=f'''
                        <div style="font-family:Arial,sans-serif;max-width:400px;margin:0 auto;">
                            <h2 style="color:#1f3d66;">R3-PCR — Username Recovery</h2>
                            <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                            <p>Your account username is:</p>
                            <h2 style="color:#1f3d66;letter-spacing:3px;font-family:monospace;">{user.username}</h2>
                            <p>Use this to log in at r3pcr.site.</p>
                            <p style="color:#94a3b8;font-size:12px;">If you did not request this, ignore this email.</p>
                        </div>
                    ''',
                    log_tag=f'username recovery for {user.username}',
                )
                messages.success(
                    request,
                    f'Your username is "{user.username}". We also sent it to your registered email.'
                )
            else:
                messages.error(request, 'No account found with that email address.')
        except Exception:
            messages.error(request, 'Something went wrong. Please try again later.')

    return render(request, 'accounts/forgot_username.html')


def reset_password(request):
    user_id = request.session.get('reset_user_id')
    if not user_id:
        return redirect('accounts:forgot_password')

    if request.method == 'POST':
        otp_code    = request.POST.get('otp_code', '').strip()
        new_password = request.POST.get('new_password', '')
        confirm      = request.POST.get('confirm_password', '')

        if new_password != confirm:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'accounts/reset_password.html')
        if len(new_password) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
            return render(request, 'accounts/reset_password.html')

        try:
            user = User.objects.get(id=user_id)
            otp  = OTP.objects.filter(user=user, is_used=False).latest('created_at')
            if otp.is_valid() and otp.code == otp_code:
                otp.is_used = True
                otp.save()
                user.set_password(new_password)
                user.save()
                del request.session['reset_user_id']
                messages.success(request, 'Password reset successful. You can now log in.')
                return redirect('accounts:login')
            else:
                messages.error(request, 'Invalid or expired OTP.')
        except (User.DoesNotExist, OTP.DoesNotExist):
            messages.error(request, 'Something went wrong. Please try again.')
            return redirect('accounts:forgot_password')

    return render(request, 'accounts/reset_password.html')


# ─── Logout ───────────────────────────────────────────────────────────────────

def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('accounts:login')


# ─── Account Settings ─────────────────────────────────────────────────────────

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
            import re
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


# ─── Role-based Redirect ──────────────────────────────────────────────────────

def redirect_by_role(user):
    if user.role == 'consignee':
        return redirect('/consignee/dashboard/')
    elif user.role == 'declarant':
        return redirect('/declarant/dashboard/')
    elif user.role == 'supervisor':
        return redirect('/supervisor/dashboard/')
    return redirect('accounts:login')
