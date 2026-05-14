from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from .models import User, OTP


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

            try:
                send_mail(
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
                )
            except Exception as e:
                print(f'Email error: {e}')
                print(f'OTP for {user.username}: {otp_code}')

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

    return render(request, 'accounts/verify_otp.html')


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
        errors = []
        if not all([first_name, last_name, email, password]):
            errors.append('All required fields must be filled in.')
        if password != password2:
            errors.append('Passwords do not match.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if User.objects.filter(email=email).exists():
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
            try:
                send_mail(
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
                )
            except Exception as ex:
                print(f'Supervisor notification error: {ex}')

        messages.success(
            request,
            f'Registration submitted! Your username is <strong>{username}</strong>. '
            'Your account is pending supervisor approval — you will receive an email once approved.'
        )
        return redirect('accounts:login')

    return render(request, 'accounts/register.html')


# ─── Forgot Password ─────────────────────────────────────────────────────────

def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            otp_code = OTP.generate_code()
            OTP.objects.create(user=user, code=otp_code)
            request.session['reset_user_id'] = user.id
            try:
                send_mail(
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
                )
            except Exception as e:
                print(f'Password reset email error: {e}')
            messages.success(request, 'OTP sent to your email. Check your inbox.')
            return redirect('accounts:reset_password')
        except User.DoesNotExist:
            messages.error(request, 'No active account found with that email.')

    return render(request, 'accounts/forgot_password.html')


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
            user.first_name  = request.POST.get('first_name', '').strip()
            user.last_name   = request.POST.get('last_name', '').strip()
            email            = request.POST.get('email', '').strip()
            phone            = request.POST.get('phone_number', '').strip()
            company          = request.POST.get('company_name', '').strip()

            if email and email != user.email:
                if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                    messages.error(request, 'Email already in use by another account.')
                    return redirect('accounts:settings')
                user.email = email

            user.phone_number = phone
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

    return render(request, 'accounts/settings.html')


# ─── Role-based Redirect ──────────────────────────────────────────────────────

def redirect_by_role(user):
    if user.role == 'consignee':
        return redirect('/consignee/dashboard/')
    elif user.role == 'declarant':
        return redirect('/declarant/dashboard/')
    elif user.role == 'supervisor':
        return redirect('/supervisor/dashboard/')
    return redirect('accounts:login')
