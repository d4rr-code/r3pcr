from django.db import models


class SystemConfig(models.Model):
    key = models.CharField(max_length=50, unique=True)
    value = models.CharField(max_length=2000)
    label = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )

    def __str__(self):
        return f'{self.key}: {self.value}'

    @classmethod
    def get(cls, key, default=''):
        try:
            return cls.objects.get(key=key).value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set(cls, key, value, label='', user=None):
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={'value': value, 'label': label, 'updated_by': user},
        )
        return obj


class Announcement(models.Model):
    CATEGORY_CHOICES = [
        ('boc',      'BOC Update'),
        ('customs',  'Customs Reminder'),
        ('shipment', 'Shipment Notice'),
        ('general',  'General'),
    ]

    CATEGORY_COLORS = {
        'boc':      '#3b82f6',
        'customs':  '#f59e0b',
        'shipment': '#8b5cf6',
        'general':  '#22c55e',
    }

    AUDIENCE_CHOICES = [
        ('all', 'All Users'),
        ('consignee', 'Consignees Only'),
        ('declarant', 'Declarants Only'),
    ]

    title      = models.CharField(max_length=200)
    content    = models.TextField()
    category   = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='general')
    target_audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default='all')
    is_active  = models.BooleanField(default=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='announcements',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def color(self):
        return self.CATEGORY_COLORS.get(self.category, '#64748b')

    def target_roles(self):
        if self.target_audience == 'all':
            return ['consignee', 'declarant']
        return [self.target_audience]


class IssueReport(models.Model):
    CATEGORY_CHOICES = [
        ('login_account', 'Login or Account'),
        ('shipment_submission', 'Shipment Submission'),
        ('document_upload', 'Document Upload'),
        ('ocr_extraction', 'OCR / Extraction'),
        ('duty_computation', 'Duty Computation'),
        ('wmcda_advisory', 'MCDA Advisory'),
        ('notifications_email', 'Notifications or Email'),
        ('dashboard_analytics', 'Dashboard / Analytics'),
        ('page_display_ui', 'Page Display / UI'),
        ('other', 'Other'),
    ]

    LOCATION_CHOICES = [
        ('dashboard', 'Dashboard'),
        ('new_submission', 'New Submission'),
        ('my_submissions', 'My Submissions'),
        ('process_shipment', 'Process Shipment'),
        ('ecdt_workspace', 'ECDT Workspace'),
        ('notifications', 'Notifications'),
        ('system_reference', 'System Reference'),
        ('other', 'Other'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('urgent', 'Urgent'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_review', 'In Review'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]

    reporter = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='issue_reports',
    )
    reporter_role = models.CharField(max_length=20)
    related_shipment = models.ForeignKey(
        'shipments.Shipment',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='issue_reports',
    )
    category = models.CharField(max_length=40, choices=CATEGORY_CHOICES)
    location = models.CharField(max_length=40, choices=LOCATION_CHOICES)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    title = models.CharField(max_length=160)
    description = models.TextField()
    attachment = models.FileField(upload_to='issue_reports/', null=True, blank=True)
    supervisor_note = models.TextField(blank=True)
    handled_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='handled_issue_reports',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_category_display()} - {self.title}'

    # Locations a reporter may file against, scoped to their role's own pages.
    # Categories (OCR, Duty Computation, MCDA, etc.) stay shared across roles.
    ROLE_LOCATIONS = {
        'consignee': ['dashboard', 'new_submission', 'my_submissions',
                      'notifications', 'system_reference', 'other'],
        'declarant': ['dashboard', 'process_shipment', 'ecdt_workspace',
                      'notifications', 'system_reference', 'other'],
    }

    @classmethod
    def locations_for_role(cls, role):
        """LOCATION_CHOICES filtered to the pages a given role actually uses.
        Unknown roles get the full list (e.g. supervisor)."""
        allowed = cls.ROLE_LOCATIONS.get(role)
        if not allowed:
            return list(cls.LOCATION_CHOICES)
        return [c for c in cls.LOCATION_CHOICES if c[0] in allowed]

    @classmethod
    def cross_role_summary(cls, exclude_user=None, limit=50):
        """Read-only summary of issues reported by consignees + declarants,
        for cross-role visibility (location + type + status only)."""
        qs = cls.objects.filter(reporter_role__in=['consignee', 'declarant'])
        if exclude_user is not None:
            qs = qs.exclude(reporter=exclude_user)
        return qs.order_by('-created_at')[:limit]
