from datetime import timedelta

from django.db import models
from django.utils import timezone
from apps.accounts.models import User

class Shipment(models.Model):
    STATUS_CHOICES = [
        ('incoming', 'Incoming'),
        ('arrived', 'Arrived'),
        ('computed', 'Computed'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('for_revision', 'For Revision'),
        ('lodgement', 'Lodgement'),
        ('ongoing', 'Ongoing'),
        ('assessed', 'Assessed'),
        ('paid', 'Paid'),
        ('released', 'Released'),
        ('billed', 'Billed'),
    ]
    SYSTEM_STATUS_KEYS = {'incoming', 'arrived', 'computed', 'approved', 'rejected', 'for_revision'}
    MANUAL_STATUS_KEYS = {'lodgement', 'ongoing', 'assessed', 'paid', 'released', 'billed'}
    MANUAL_STATUS_CHOICES = [
        ('lodgement', 'Lodgement'),
        ('ongoing', 'Ongoing'),
        ('assessed', 'Assessed'),
        ('paid', 'Paid'),
        ('released', 'Released'),
        ('billed', 'Billed'),
    ]

    SHIPMENT_TYPE_CHOICES = [
        ('air',  'Air Freight'),
        ('lcl',  'LCL - Less Container Load'),
        ('fcl',  'FCL - Full Container Load'),
    ]
    KPI_TARGET_DAYS = {
        'air': (2, 3),
        'lcl': (4, 5),
        'fcl': (3, 4),
    }
    KPI_COMPLETE_STATUSES = {'released', 'billed'}

    URGENCY_CHOICES = [
        ('standard', 'Standard'),
        ('priority', 'Priority'),
        ('urgent',   'Urgent'),
        ('rush',     'Rush / Time-Critical'),
        ('normal',   'Standard'),   # legacy alias — kept for existing records
    ]

    IMPORT_TYPE_CHOICES = [
        # ── CMTA / BOC-based classifications ──────────────────────────────
        ('commercial',    'Commercial / Trade Goods'),
        ('personal',      'Personal Effects & Household Goods'),
        ('balikbayan',    'Balikbayan Box (RA 10021)'),
        ('samples',       'Samples / No Commercial Value'),
        ('machinery',     'Machinery & Equipment (Capital Goods)'),
        ('raw_materials', 'Raw Materials & Inputs'),
        ('ecommerce',     'Online Purchase / E-Commerce'),
        # ── legacy keys — kept for existing records ────────────────────────
        ('balik_bayan',   'Balik Bayan'),
        ('courier',       'Courier'),
        ('sample',        'Sample / Free of Charge'),
        ('diplomatic',    'Diplomatic'),
    ]

    # Core fields
    hawb_number = models.CharField(max_length=100, unique=True)
    consignee = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='shipments'
    )
    declarant = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='assigned_shipments'
    )

    # Shipment details
    import_type = models.CharField(
        max_length=20, 
        choices=IMPORT_TYPE_CHOICES,
        default='commercial'
    )
    shipment_type = models.CharField(
        max_length=10, 
        choices=SHIPMENT_TYPE_CHOICES, 
        blank=True, null=True
    )
    urgency = models.CharField(
        max_length=10, 
        choices=URGENCY_CHOICES, 
        default='normal'
    )
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='incoming'
    )

    # Cargo details
    description = models.TextField(blank=True, null=True)
    quantity = models.DecimalField(
        max_digits=10, decimal_places=2, 
        blank=True, null=True
    )
    gross_weight = models.DecimalField(
        max_digits=10, decimal_places=2, 
        blank=True, null=True
    )
    estimated_arrival_date = models.DateField(blank=True, null=True)
    container_number = models.CharField(max_length=100, blank=True, null=True)
    job_order_reference = models.CharField(max_length=100, blank=True, null=True)
    
    CURRENCY_CHOICES = [
        ('USD', 'US Dollar (USD)'),
        ('EUR', 'Euro (EUR)'),
        ('JPY', 'Japanese Yen (JPY)'),
        ('HKD', 'Hong Kong Dollar (HKD)'),
        ('CNY', 'Chinese Yuan (CNY)'),
        ('GBP', 'British Pound (GBP)'),
        ('SGD', 'Singapore Dollar (SGD)'),
    ]

    # Financial details
    invoice_currency = models.CharField(
        max_length=10,
        choices=CURRENCY_CHOICES,
        default='USD',
        blank=True,
        help_text='Currency of the commercial invoice'
    )
    declared_value = models.DecimalField(
        max_digits=15, decimal_places=2,
        blank=True, null=True
    )
    freight_cost = models.DecimalField(
        max_digits=15, decimal_places=2,
        blank=True, null=True
    )
    insurance_cost = models.DecimalField(
        max_digits=15, decimal_places=2,
        blank=True, null=True
    )
    
    # Payment receipt (uploaded by consignee)
    payment_receipt = models.FileField(
        upload_to='payment_receipts/',
        blank=True, null=True,
        help_text='Payment receipt uploaded by consignee'
    )
    payment_receipt_uploaded_at = models.DateTimeField(blank=True, null=True)

    # BOC details
    boc_reference = models.CharField(max_length=100, blank=True, null=True)
    boc_status = models.CharField(max_length=50, blank=True, null=True)

    # Document Deficiency Flag
    has_deficiency = models.BooleanField(default=False)
    deficiency_type = models.CharField(max_length=50, blank=True, null=True)
    deficiency_notes = models.TextField(blank=True, null=True)
    deficiency_flagged_at = models.DateTimeField(blank=True, null=True)

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    # Tracks the last date an overdue email was sent (prevents daily spam)
    overdue_notified_at = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.hawb_number} - {self.consignee.username}"

    @property
    def kpi_target_days(self):
        return self.KPI_TARGET_DAYS.get(self.shipment_type or '')

    @property
    def kpi_target_label(self):
        target = self.kpi_target_days
        if not target:
            return ''
        return f'{target[0]}-{target[1]} days'

    @property
    def kpi_base_date(self):
        if self.estimated_arrival_date:
            return self.estimated_arrival_date
        if self.submitted_at:
            return timezone.localtime(self.submitted_at).date()
        return None

    @property
    def kpi_base_label(self):
        return 'Estimated arrival' if self.estimated_arrival_date else 'Submission date'

    @property
    def kpi_eta_start(self):
        base_date = self.kpi_base_date
        target = self.kpi_target_days
        if not base_date or not target:
            return None
        return base_date + timedelta(days=target[0])

    @property
    def kpi_eta_end(self):
        base_date = self.kpi_base_date
        target = self.kpi_target_days
        if not base_date or not target:
            return None
        return base_date + timedelta(days=target[1])

    @property
    def kpi_days_remaining(self):
        eta_end = self.kpi_eta_end
        if not eta_end:
            return None
        return (eta_end - timezone.localdate()).days

    @property
    def kpi_timing_status(self):
        if not self.kpi_eta_end:
            return 'unknown'
        if self.status in self.KPI_COMPLETE_STATUSES:
            return 'complete'
        days_remaining = self.kpi_days_remaining
        if days_remaining is not None and days_remaining < 0:
            return 'delayed'
        if self.kpi_eta_start and timezone.localdate() >= self.kpi_eta_start:
            return 'due_soon'
        return 'on_track'

    @property
    def kpi_timing_label(self):
        return {
            'unknown': 'No KPI ETA',
            'complete': 'Complete',
            'delayed': 'Delayed',
            'due_soon': 'Due Soon',
            'on_track': 'On Track',
        }.get(self.kpi_timing_status, 'No KPI ETA')

    @property
    def kpi_timing_help(self):
        status = self.kpi_timing_status
        days_remaining = self.kpi_days_remaining
        if status == 'delayed' and days_remaining is not None:
            overdue_days = abs(days_remaining)
            return f'{overdue_days} day{"s" if overdue_days != 1 else ""} past KPI target'
        if status == 'due_soon':
            if days_remaining == 0:
                return 'KPI target ends today'
            return f'{days_remaining} day{"s" if days_remaining != 1 else ""} left in KPI window'
        if status == 'on_track' and self.kpi_eta_start:
            days_to_window = (self.kpi_eta_start - timezone.localdate()).days
            return f'{days_to_window} day{"s" if days_to_window != 1 else ""} before KPI window'
        if status == 'complete':
            return 'Shipment has reached released or billed status'
        return ''


class ShipmentDocument(models.Model):
    DOCUMENT_TYPE_CHOICES = [
        ('invoice', 'Commercial Invoice'),
        ('packing_list', 'Packing List'),
        ('airway_bill', 'Airway Bill / Bill of Lading'),
        ('sad', 'FAN Document'),
        ('payment_proof', 'Payment Proof / BOC Receipt'),
        ('release_doc', 'Release / Delivery Document'),
        ('billing_doc', 'Final Billing Document'),
        ('receipt', 'Billing Receipt / Payment Proof'),
        ('other', 'Other Supporting Document'),
    ]

    shipment = models.ForeignKey(
        Shipment, 
        on_delete=models.CASCADE, 
        related_name='documents'
    )
    document_type = models.CharField(
        max_length=20, 
        choices=DOCUMENT_TYPE_CHOICES
    )
    file = models.FileField(upload_to='shipment_documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    ocr_text = models.TextField(blank=True, null=True)
    ocr_fields_json = models.TextField(blank=True, null=True)
    ocr_quality = models.CharField(max_length=10, blank=True, null=True)
    ocr_ran_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.document_type} - {self.shipment.hawb_number}"


class HSCode(models.Model):
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField()
    duty_rate = models.DecimalField(max_digits=5, decimal_places=2)
    unit = models.CharField(max_length=20, blank=True, null=True)
    chapter = models.CharField(max_length=10, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.description[:50]}"

    def duty_rate_for(self, schedule=None):
        if schedule:
            rate = self.schedule_rates.filter(schedule=schedule).first()
            if rate:
                return rate.duty_rate
        return self.duty_rate


class TariffSchedule(models.Model):
    RATE_BASIS_CHOICES = [
        ('mfn', 'MFN'),
        ('preferential', 'Preferential'),
        ('other', 'Other'),
    ]

    name = models.CharField(max_length=160, unique=True)
    code = models.SlugField(max_length=80, unique=True)
    rate_basis = models.CharField(max_length=20, choices=RATE_BASIS_CHOICES, default='mfn')
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    source_file = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    imported_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_tariff_schedules',
    )
    imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_active', '-effective_from', 'name']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            TariffSchedule.objects.exclude(pk=self.pk).update(is_active=False)

    def __str__(self):
        return self.name

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).first()


class HSCodeRate(models.Model):
    hs_code = models.ForeignKey(
        HSCode,
        on_delete=models.CASCADE,
        related_name='schedule_rates',
    )
    schedule = models.ForeignKey(
        TariffSchedule,
        on_delete=models.CASCADE,
        related_name='rates',
    )
    duty_rate = models.DecimalField(max_digits=8, decimal_places=4)
    source_row = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='updated_hs_code_rates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('hs_code', 'schedule')
        ordering = ['hs_code__code']

    def __str__(self):
        return f'{self.hs_code.code} - {self.schedule.name}: {self.duty_rate}%'


class ShipmentHSCode(models.Model):
    shipment = models.ForeignKey(
        Shipment, 
        on_delete=models.CASCADE, 
        related_name='hs_codes'
    )
    hs_code = models.ForeignKey(
        HSCode, 
        on_delete=models.CASCADE
    )
    is_suggested = models.BooleanField(default=False)
    is_confirmed = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.shipment.hawb_number} - {self.hs_code.code}"


class StatusLog(models.Model):
    shipment = models.ForeignKey(
        Shipment, 
        on_delete=models.CASCADE, 
        related_name='status_logs'
    )
    changed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True
    )
    old_status = models.CharField(max_length=20)
    new_status = models.CharField(max_length=20)
    notes = models.TextField(blank=True, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.shipment.hawb_number}: {self.old_status} → {self.new_status}"
