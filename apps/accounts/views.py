from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from .models import User, OTP

# ─── Login View ───────────────────────────────────────────
def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            # Generate and send OTP
            otp_code = OTP.generate_code()
            OTP.objects.create(user=user, code=otp_code)
            
            # Store user id in session for OTP verification
            request.session['pre_auth_user_id'] = user.id
            
            # Send OTP via Gmail
            try:
                send_mail(
                    subject='R3-PCR Login OTP',
                    message=f'Your OTP code is: {otp_code}\nThis expires in 10 minutes.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=f'''
                        <div style="font-family: Arial, sans-serif; max-width: 400px; margin: 0 auto;">
                            <h2 style="color: #3b82f6;">R3-PCR System</h2>
                            <p>Hello <strong>{user.first_name or user.username}</strong>,</p>
                            <p>Your OTP code is:</p>
                            <h1 style="color: #3b82f6; letter-spacing: 8px;">{otp_code}</h1>
                            <p>This code expires in <strong>10 minutes</strong>.</p>
                            <p style="color: #94a3b8; font-size: 12px;">
                                If you did not request this, please ignore this email.
                            </p>
                        </div>
                    '''
                )
            except Exception as e:
                print(f"Email error: {e}")
                print(f"OTP for {user.username}: {otp_code}")
            
            messages.success(request, 'OTP sent to your email.')
            return redirect('accounts:verify_otp')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'accounts/login.html')


# ─── OTP Verification View ─────────────────────────────────
def verify_otp_view(request):
    user_id = request.session.get('pre_auth_user_id')
    
    if not user_id:
        return redirect('accounts:login')
    
    if request.method == 'POST':
        entered_code = request.POST.get('otp_code')
        
        try:
            user = User.objects.get(id=user_id)
            otp = OTP.objects.filter(
                user=user, 
                is_used=False
            ).latest('created_at')
            
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


# ─── Logout View ───────────────────────────────────────────
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('accounts:login')


# ─── Role-based Redirect ───────────────────────────────────
def redirect_by_role(user):
    if user.role == 'consignee':
        return redirect('/consignee/dashboard/')
    elif user.role == 'declarant':
        return redirect('/declarant/dashboard/')
    elif user.role == 'supervisor':
        return redirect('/supervisor/dashboard/')
    else:
        return redirect('accounts:login')