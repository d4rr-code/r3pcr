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
    
    role = models.CharField(
        max_length=20, 
        choices=ROLE_CHOICES, 
        default='consignee'
    )
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.username} ({self.role})"

    def is_consignee(self):
        return self.role == 'consignee'

    def is_declarant(self):
        return self.role == 'declarant'

    def is_supervisor(self):
        return self.role == 'supervisor'


class OTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        # OTP expires after 10 minutes
        expiry_time = self.created_at + timezone.timedelta(minutes=10)
        return not self.is_used and timezone.now() < expiry_time

    @staticmethod
    def generate_code():
        return str(random.randint(100000, 999999))

    def __str__(self):
        return f"OTP for {self.user.username} - {self.code}"