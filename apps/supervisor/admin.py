from django.contrib import admin
from .models import SystemConfig, AuditLog

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('key', 'value', 'label', 'updated_at')
    search_fields = ('key',)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'action', 'user', 'user_role', 'shipment', 'summary')
    list_filter = ('action', 'user_role', 'created_at')
    search_fields = ('summary', 'user__username', 'shipment__hawb_number', 'target_type', 'target_id')
    readonly_fields = (
        'user', 'user_role', 'action', 'shipment', 'target_type', 'target_id',
        'summary', 'details', 'ip_address', 'user_agent', 'created_at',
    )
