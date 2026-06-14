from django.contrib.auth.models import AbstractUser
from django.db import models
import random
from django.utils import timezone


class User(AbstractUser):
    ROLE_CHOICES = [
        ('consignee', 'Consignee'),
        ('declarant', 'Declarant'),
        ('supervisor', 'Supervisor'),
    ]

    # Override AbstractUser's first_name/last_name (max_length 150 → 50)
    first_name = models.CharField(max_length=50, blank=True)
    last_name  = models.CharField(max_length=50, blank=True)

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='consignee'
    )
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    company_name = models.CharField(max_length=100, blank=True)
    email_verified      = models.BooleanField(default=True)
    otp_enabled         = models.BooleanField(default=True)
    is_pending_approval = models.BooleanField(default=False)
    is_active           = models.BooleanField(default=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.username} ({self.role})"

    def is_consignee(self):
        return self.role == 'consignee'

    def is_declarant(self):
        return self.role == 'declarant'

    def is_supervisor(self):
        return self.role == 'supervisor'


class OTP(models.Model):
    user       = models.ForeignKey(User, on_delete=models.CASCADE)
    code       = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used    = models.BooleanField(default=False)

    def is_valid(self):
        expiry_time = self.created_at + timezone.timedelta(minutes=10)
        return not self.is_used and timezone.now() < expiry_time

    @staticmethod
    def generate_code():
        return str(random.randint(100000, 999999))

    def __str__(self):
        return f"OTP for {self.user.username} - {self.code}"


class EmailVerification(models.Model):
    """A click-to-confirm token for verifying an email during registration,
    before the User account exists. Backed by the DB so the confirmation link
    works even when opened in a different tab or device."""
    email       = models.EmailField()
    token       = models.CharField(max_length=64, unique=True, db_index=True)
    is_verified = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def is_expired(self, minutes=30):
        return timezone.now() > self.created_at + timezone.timedelta(minutes=minutes)

    def __str__(self):
        return f"{self.email} - {'verified' if self.is_verified else 'pending'}"
