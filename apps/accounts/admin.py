from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, OTP

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'email_verified', 'is_active']
    list_filter = ['role', 'email_verified', 'is_active']
    fieldsets = UserAdmin.fieldsets + (
        ('R3-PCR Info', {'fields': ('role', 'phone_number', 'email_verified')}),
    )

@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ['user', 'code', 'is_used', 'created_at']
