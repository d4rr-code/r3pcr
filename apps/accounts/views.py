import re
import threading
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.mail import send_mail
from django.core.cache import cache
from django.conf import settings
from .models import User, OTP


# ─── Brute-force throttling ───────────────────────────────────────────────────
LOGIN_FAIL_LIMIT  = 8           # failed password attempts per account...
LOGIN_FAIL_WINDOW = 15 * 60     # ...within this many seconds before a cooloff
OTP_ATTEMPT_LIMIT = 5           # wrong OTP codes before the login is reset


# ─── Field validation helpers ─────────────────────────────────────────────────

def _normalize_phone_number(phone):
    """Reduce any PH mobile entry to the canonical 11-digit 09xxxxxxxxx form.
    Accepts +63/63 prefixes and stray spaces/dashes; returns '' if unusable."""
    if not phone:
        return ''
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('63') and len(digits) == 12:
        digits = '0' + digits[2:]      # 639xxxxxxxxx -> 09xxxxxxxxx
    return digits


def _validate_phone_number(phone):
    if not phone:
        return None
    digits = _normalize_phone_number(phone)
    if not re.fullmatch(r'09\d{9}', digits):
        return 'Enter a valid PH mobile number in the format 09xxxxxxxxx (11 digits).'
    return None


def _validate_profile_fields(first_name, last_name, email, phone='', company=''):
    """Return a list of error strings; empty list means all fields are valid."""
    errors = []

    # First name
    if not first_name:
        errors.append('First name is required.')
    elif len(first_name) < 2:
        errors.append('First name must be at least 2 characters.')
    elif len(first_name) > 30:
        errors.append('First name cannot exceed 30 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", first_name):
        errors.append('First name may only contain letters, spaces, hyphens, and apostrophes.')

    # Last name
    if not last_name:
        errors.append('Last name is required.')
    elif len(last_name) < 2:
        errors.append('Last name must be at least 2 characters.')
    elif len(last_name) > 30:
        errors.append('Last name cannot exceed 30 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", last_name):
        errors.append('Last name may only contain letters, spaces, hyphens, and apostrophes.')

    # Email — use Django's validator for robust format checking
    if not email:
        errors.append('Email address is required.')
    else:
        from django.core.validators import validate_email as _dj_validate_email
        from django.core.exceptions import ValidationError as _DjVErr
        try:
            _dj_validate_email(email)
        except _DjVErr:
            errors.append('Enter a valid email address (e.g. juandelacruz@gmail.com).')

    # Phone (optional) — Philippine format: 09XX-XXX-XXXX or +639XX-XXX-XXXX
    if phone:
        phone_error = _validate_phone_number(phone)
        if phone_error:
            errors.append(phone_error)

    # Company (optional)
    if company and len(company) > 100:
        errors.append('Company name cannot exceed 100 characters.')

    return errors


def _validate_password_strength(password):
    """Enforce the character-class rules shown on the registration form, plus
    Django's configured validators (common/numeric/similarity) as a backstop.
    Returns a list of error strings."""
    errors = []
    if len(password) < 8:
        errors.append('Password must be at least 8 characters.')
    if not re.search(r'[A-Z]', password):
        errors.append('Password must include at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        errors.append('Password must include at least one lowercase letter.')
    if not re.search(r'[0-9]', password):
        errors.append('Password must include at least one number.')
    if not re.search(r'[^A-Za-z0-9]', password):
        errors.append('Password must include at least one special character.')

    if not errors:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as _DjVErr
        try:
            validate_password(password)
        except _DjVErr as ve:
            errors.extend(ve.messages)
    return errors


def _send_mail_async(subject, message, from_email, recipient_list, html_message=None, log_tag=''):
    """Send email in the background with retry + logging (delegates to the
    shared, hardened helper). Signature kept for existing call sites."""
    from apps.notifications.email import send_email_async
    send_email_async(subject, message, recipient_list, html_message=html_message,
                     from_email=from_email, log_tag=log_tag)


# ─── Login ────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        # ── Brute-force throttle (per account, with cooloff) ──
        fail_key = f'login_fail:{(username or "").strip().lower()}'
        if cache.get(fail_key, 0) >= LOGIN_FAIL_LIMIT:
            messages.error(request,
                'Too many failed login attempts for this account. '
                'Please wait about 15 minutes and try again.')
            return render(request, 'accounts/login.html')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            cache.delete(fail_key)               # reset on success
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

            # Only suppress real sending when using the console backend (or the
            # explicit dev-link flag) — NOT merely because DEBUG is on, so OTP
            # emails actually send once a real backend (Gmail/Resend) is set.
            _is_local = ('console' in getattr(settings, 'EMAIL_BACKEND', '')
                         or getattr(settings, 'REGISTRATION_EMAIL_DEV_LINKS', False))
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
            attempts = cache.get(fail_key, 0) + 1
            cache.set(fail_key, attempts, LOGIN_FAIL_WINDOW)
            remaining = max(0, LOGIN_FAIL_LIMIT - attempts)
            msg = 'Invalid username or password.'
            if remaining <= 3:
                msg += f' {remaining} attempt(s) left before a temporary lockout.'
            messages.error(request, msg)

    return render(request, 'accounts/login.html')


# ─── OTP Verification ─────────────────────────────────────────────────────────

def verify_otp_view(request):
    user_id = request.session.get('pre_auth_user_id')
    if not user_id:
        return redirect('accounts:login')

    if request.method == 'POST':
        # ── Brute-force cap on the 6-digit code ──
        attempts = request.session.get('otp_attempts', 0)
        if attempts >= OTP_ATTEMPT_LIMIT:
            OTP.objects.filter(user_id=user_id, is_used=False).update(is_used=True)
            request.session.pop('pre_auth_user_id', None)
            request.session.pop('otp_attempts', None)
            messages.error(request, 'Too many incorrect codes. Please log in again.')
            return redirect('accounts:login')

        entered_code = request.POST.get('otp_code')
        try:
            user = User.objects.get(id=user_id)
            otp = OTP.objects.filter(user=user, is_used=False).latest('created_at')
            if otp.is_valid() and otp.code == entered_code:
                otp.is_used = True
                otp.save()
                login(request, user)
                request.session.pop('pre_auth_user_id', None)
                request.session.pop('otp_attempts', None)
                messages.success(request, f'Welcome, {user.first_name or user.username}!')
                return redirect_by_role(user)
            else:
                attempts += 1
                request.session['otp_attempts'] = attempts
                remaining = max(0, OTP_ATTEMPT_LIMIT - attempts)
                messages.error(request, f'Invalid or expired OTP. {remaining} attempt(s) left.')
        except (User.DoesNotExist, OTP.DoesNotExist):
            messages.error(request, 'Something went wrong. Please try again.')
            return redirect('accounts:login')

    # ── DEV HINT: surface OTP on screen ONLY in local/dev (never in production,
    #    where it would leak the code and defeat the whole OTP step) ──
    dev_otp = None
    _is_local = ('console' in getattr(settings, 'EMAIL_BACKEND', '')
                 or getattr(settings, 'REGISTRATION_EMAIL_DEV_LINKS', False))
    if _is_local:
        try:
            _hint_otp = OTP.objects.filter(user_id=user_id, is_used=False).latest('created_at')
            if _hint_otp.is_valid():
                dev_otp = _hint_otp.code
        except Exception:
            pass

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
    """Auto-generate a compact unique username: first initial + last name,
    lowercased and length-capped. e.g. 'Juan Dela Cruz' -> 'jdelacruz'.
    Falls back to fuller name parts when the result would be too short."""
    first = re.sub(r'[^a-z0-9]', '', (first_name or '').lower())
    last  = re.sub(r'[^a-z0-9]', '', (last_name or '').lower())

    base = (first[:1] + last) if (first and last) else (first or last)
    base = base[:15]                       # not too long
    if len(base) < 5:                      # not too short
        base = (first + last)[:15] or base
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


# ─── Registration email confirmation link (no user yet) ──────────────────────

def _registration_is_local():
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    return 'console' in backend or getattr(settings, 'REGISTRATION_EMAIL_DEV_LINKS', False)


def _email_is_verified(request, email):
    """True if the session's pending token is verified and matches this email."""
    from .models import EmailVerification
    token = request.session.get('reg_verify_token')
    if not token or not email:
        return False
    ev = EmailVerification.objects.filter(token=token).first()
    return bool(ev and ev.is_verified and ev.email.lower() == email.lower())


def send_verification_email(request):
    """AJAX: email a one-click confirmation link to the address on the
    registration form. The token is stored in the DB (and bound to the
    session) so clicking the link from any tab/device confirms it."""
    from django.http import JsonResponse
    from django.core.validators import validate_email as _dj_validate_email
    from django.core.exceptions import ValidationError as _DjVErr
    from django.urls import reverse
    from .models import EmailVerification
    import secrets

    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=405)

    email = request.POST.get('email', '').strip()
    if not email:
        return JsonResponse({'ok': False, 'error': 'Please enter your email address first.'})
    try:
        _dj_validate_email(email)
    except _DjVErr:
        return JsonResponse({'ok': False, 'error': 'Enter a valid email address (e.g. juandelacruz@gmail.com).'})
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({'ok': False, 'error': 'This email is already registered.'})

    token = secrets.token_urlsafe(32)
    EmailVerification.objects.create(email=email.lower(), token=token)
    request.session['reg_verify_token'] = token

    link = request.build_absolute_uri(
        reverse('accounts:confirm_email', args=[token])
    )

    payload = {'ok': True, 'message': f'A verification link has been sent to {email}. '
                                      'Open it to confirm your email, then return here.'}
    if _registration_is_local():
        print(f'\n{"="*60}\n[DEV VERIFY EMAIL] {email}\n[DEV VERIFY LINK] {link}\n{"="*60}\n')
        payload['dev_link'] = link            # surfaced on-screen for local testing
    else:
        try:
            send_mail(
                subject='R3-PCR - Verify your email',
                message=(f'Confirm your email to complete your R3-PCR registration:\n\n{link}\n\n'
                         f'This link expires in 30 minutes. If you did not request this, ignore this email.'),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=f'''
                    <div style="font-family:Arial,sans-serif;max-width:440px;margin:0 auto;">
                        <h2 style="color:#3b82f6;">Verify your email</h2>
                        <p>Confirm your email address to complete your R3-PCR registration.</p>
                        <p style="margin:24px 0;">
                            <a href="{link}" style="background:#1d4ed8;color:#fff;text-decoration:none;
                               padding:12px 22px;border-radius:8px;font-weight:600;display:inline-block;">
                               Verify Email
                            </a>
                        </p>
                        <p style="color:#64748b;font-size:12px;">Or paste this link into your browser:<br>{link}</p>
                        <p style="color:#94a3b8;font-size:12px;">This link expires in 30 minutes.
                           If you did not request this, ignore this email.</p>
                    </div>
                ''',
            )
        except Exception as e:
            EmailVerification.objects.filter(token=token).delete()
            request.session.pop('reg_verify_token', None)
            print(f'[EMAIL ERROR] registration verify email for {email}: {e}')
            return JsonResponse({
                'ok': False,
                'error': 'Could not send verification email. Please check the email service settings and try again.',
            }, status=500)
    return JsonResponse(payload)


def confirm_email(request, token):
    """GET landing for the email link — marks the token verified and shows
    a confirmation page directing the user back to the registration tab."""
    from .models import EmailVerification
    ev = EmailVerification.objects.filter(token=token).first()

    status = 'invalid'
    email = ''
    if ev:
        email = ev.email
        if ev.is_verified:
            status = 'ok'
        elif ev.is_expired():
            status = 'expired'
        else:
            ev.is_verified = True
            ev.verified_at = timezone.now()
            ev.save(update_fields=['is_verified', 'verified_at'])
            status = 'ok'

    return render(request, 'accounts/confirm_email.html', {'status': status, 'email': email})


def check_email_verified(request):
    """AJAX poll: report whether the session's pending email has been confirmed."""
    from django.http import JsonResponse
    from .models import EmailVerification
    token = request.session.get('reg_verify_token')
    ev = EmailVerification.objects.filter(token=token).first() if token else None
    return JsonResponse({
        'verified': bool(ev and ev.is_verified),
        'email': ev.email if ev else '',
    })


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

