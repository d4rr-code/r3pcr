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
            otp_code = OTP.generate_code()
            OTP.objects.create(user=user, code=otp_code)
            request.session['pre_auth_user_id'] = user.id

            try:
                send_mail(
                    subject='R3-PCR Login OTP',
                    message=f'Your OTP code is: {otp_code}\nExpires in 10 minutes.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=['darfrancis33@gmail.com'],
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
        user = request.user

        if action == 'profile':
            user.first_name = request.POST.get('first_name', '').strip()
            user.last_name = request.POST.get('last_name', '').strip()
            email = request.POST.get('email', '').strip()
            phone = request.POST.get('phone_number', '').strip()

            if email and email != user.email:
                if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                    messages.error(request, 'Email already in use by another account.')
                    return redirect('accounts:settings')
                user.email = email

            user.phone_number = phone
            user.save()
            messages.success(request, 'Profile updated.')

        elif action == 'password':
            old_password = request.POST.get('old_password', '')
            new_password = request.POST.get('new_password', '')
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
