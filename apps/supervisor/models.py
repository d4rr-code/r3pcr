from django.db import models


class SystemConfig(models.Model):
    key = models.CharField(max_length=50, unique=True)
    value = models.CharField(max_length=200)
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

    title      = models.CharField(max_length=200)
    content    = models.TextField()
    category   = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='general')
    is_active  = models.BooleanField(default=True)
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
