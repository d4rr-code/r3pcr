"""Password reset (via emailed OTP) and username recovery."""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.conf import settings
from ..models import User, OTP
from .common import _send_mail_async


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
