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