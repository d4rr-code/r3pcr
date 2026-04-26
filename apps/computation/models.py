from django.db import models
from apps.shipments.models import Shipment, HSCode

class DutyComputation(models.Model):
    shipment = models.OneToOneField(
        Shipment,
        on_delete=models.CASCADE,
        related_name='computation'
    )
    hs_code = models.ForeignKey(
        HSCode,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )

    # Input values
    declared_value = models.DecimalField(max_digits=15, decimal_places=2)
    freight_cost = models.DecimalField(max_digits=15, decimal_places=2)
    insurance_cost = models.DecimalField(max_digits=15, decimal_places=2)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4)
    duty_rate = models.DecimalField(max_digits=5, decimal_places=2)

    # Computed values
    dutiable_value = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True
    )
    customs_duty = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True
    )
    vat_base = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True
    )
    vat_amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True
    )
    total_landed_cost = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True
    )

    # Metadata
    computed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True
    )
    computed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Computation for {self.shipment.hawb_number}"


class ShippingAdvisory(models.Model):
    SHIPPING_TYPE_CHOICES = [
        ('lcl', 'LCL - Less Container Load'),
        ('fcl', 'FCL - Full Container Load'),
        ('air', 'Air Freight'),
    ]

    shipment = models.OneToOneField(
        Shipment,
        on_delete=models.CASCADE,
        related_name='shipping_advisory'
    )

    # Input criteria
    gross_weight = models.DecimalField(max_digits=10, decimal_places=2)
    cargo_volume = models.DecimalField(max_digits=10, decimal_places=2)
    declared_value = models.DecimalField(max_digits=15, decimal_places=2)
    urgency_level = models.CharField(max_length=10)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2)

    # WMCDA Scores
    lcl_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    fcl_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    air_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)

    # Result
    recommended_type = models.CharField(
        max_length=10,
        choices=SHIPPING_TYPE_CHOICES,
        null=True, blank=True
    )

    # Metadata
    computed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True
    )
    computed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Advisory for {self.shipment.hawb_number} → {self.recommended_type}"