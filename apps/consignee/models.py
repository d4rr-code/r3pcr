from django.db import models


class Feedback(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    consignee  = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='feedbacks'
    )
    shipment   = models.OneToOneField(
        'shipments.Shipment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='feedback'
    )
    rating     = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    comment    = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.consignee.get_full_name()} — {self.rating}★"
