"""Login, OTP verification/resend, and logout."""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.core.cache import cache
from django.conf import settings
from ..models import User, OTP
from .common import logger, _send_mail_async, redirect_by_role


# ─── Brute-force throttling ───────────────────────────────────────────────────
LOGIN_FAIL_LIMIT  = 8           # failed password attempts per account...
LOGIN_FAIL_WINDOW = 15 * 60     # ...within this many seconds before a cooloff
OTP_ATTEMPT_LIMIT = 5           # wrong OTP codes before the login is reset


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
                # Local development: log OTP to terminal, no email sent
                logger.info('[DEV OTP] User: %s | Code: %s', user.username, otp_code)
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
    _show_otp_hint = _is_local or getattr(settings, 'LOGIN_OTP_SCREEN_HINT', False)
    if _show_otp_hint:
        try:
            _hint_otp = OTP.objects.filter(user_id=user_id, is_used=False).latest('created_at')
            if _hint_otp.is_valid():
                dev_otp = _hint_otp.code
        except OTP.DoesNotExist:
            pass

    return render(request, 'accounts/verify_otp.html', {'dev_otp': dev_otp})


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


def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('accounts:login')
