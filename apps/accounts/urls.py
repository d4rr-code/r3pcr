from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/',      views.login_view,       name='login'),
    path('register/',   views.register_view,    name='register'),
    path('register/verify-email/', views.verify_registration_email, name='verify_registration_email'),
    path('register/resend-otp/', views.resend_registration_otp, name='resend_registration_otp'),
    path('verify-otp/', views.verify_otp_view,  name='verify_otp'),
    path('logout/',     views.logout_view,       name='logout'),
    path('settings/',        views.account_settings, name='settings'),
    path('forgot-password/', views.forgot_password,  name='forgot_password'),
    path('forgot-username/', views.forgot_username,  name='forgot_username'),
    path('reset-password/',  views.reset_password,   name='reset_password'),
    path('resend-otp/',      views.resend_otp,        name='resend_otp'),
]
