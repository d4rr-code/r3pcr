from django.db import models
from apps.accounts.models import User
from apps.shipments.models import Shipment

class Notification(models.Model):
    TYPE_CHOICES = [
        ('submission', 'New Submission'),
        ('status_update', 'Status Update'),
        ('computation', 'Computation Complete'),
        ('advisory', 'Shipping Advisory'),
        ('payment', 'Payment Required'),
        ('approved', 'Shipment Approved'),
        ('rejected', 'Shipment Rejected'),
        ('general', 'General'),
    ]

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    shipment = models.ForeignKey(
        Shipment,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='notifications'
    )
    notification_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='general'
    )
    title = models.CharField(max_length=100)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient.username} - {self.title}"