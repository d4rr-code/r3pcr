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

    # Global inputs
    total_freight = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_insurance = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    duty_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Total EXW USD (sum of all item EXW)
    declared_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # Per-item breakdown as JSON
    items_json = models.TextField(blank=True, null=True)

    # Summary results (PHP)
    dutiable_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Taxable Value — sum of all D/V PHP'
    )
    customs_duty = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Total CUD'
    )
    vat_base = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    vat_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    brokerage_fee = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    ipf = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Import Processing Fee'
    )
    total_landed_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    # Misc charges (declarant inputs — override-able)
    bank_charges   = models.DecimalField(max_digits=15, decimal_places=2, default=0,
                                         help_text='Bank charges (if any)')

    # Port / terminal charges (declarant inputs — override-able)
    arrastre       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    wharfage       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    csf_usd        = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text='Container Service Fee in USD (FCL only)'
    )
    container_type = models.CharField(
        max_length=10, blank=True, default='',
        help_text='20ft or 40ft (FCL only)'
    )

    computed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True
    )
    computed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Computation for {self.shipment.hawb_number}"

    @property
    def total_duties_and_taxes(self):
        """CUD + VAT — the portion paid to Bureau of Customs."""
        cud = self.customs_duty or 0
        vat = self.vat_amount or 0
        total = cud + vat
        return total if total else None

    @property
    def boc_payable(self):
        """Full BOC counter payment: CUD + VAT + IPF + CDS (fixed ₱130)."""
        from decimal import Decimal
        cud = self.customs_duty or Decimal('0')
        vat = self.vat_amount  or Decimal('0')
        ipf = self.ipf         or Decimal('0')
        cds = Decimal('130')
        return round(cud + vat + ipf + cds, 2)

    @property
    def csf_php(self):
        """CSF converted to PHP using stored exchange rate."""
        return (self.csf_usd or 0) * (self.exchange_rate or 0)

    def get_items(self):
        import json
        if self.items_json:
            return json.loads(self.items_json)
        return []


class ShippingAdvisory(models.Model):
    SHIPPING_TYPE_CHOICES = [
        ('lcl', 'LCL - Less Container Load'),
        ('fcl', 'FCL - Full Container Load'),
        ('air', 'Air Freight'),
        ('land', 'Land Freight'),
    ]

    shipment = models.OneToOneField(
        Shipment, on_delete=models.CASCADE, related_name='shipping_advisory'
    )
    gross_weight = models.DecimalField(max_digits=10, decimal_places=2)
    cargo_volume = models.DecimalField(max_digits=10, decimal_places=2)
    declared_value = models.DecimalField(max_digits=15, decimal_places=2)
    urgency_level = models.CharField(max_length=10)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2)

    lcl_score  = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    fcl_score  = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    air_score  = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    land_score = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)

    recommended_type = models.CharField(
        max_length=10, choices=SHIPPING_TYPE_CHOICES, null=True, blank=True
    )

    # Declarant override / advisory to consignee
    declarant_recommendation = models.CharField(
        max_length=10, choices=SHIPPING_TYPE_CHOICES, null=True, blank=True
    )
    declarant_note = models.TextField(null=True, blank=True)

    computed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True
    )
    computed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Advisory for {self.shipment.hawb_number} → {self.recommended_type}"
