from django.contrib.auth.models import AbstractUser
from django.db import models

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