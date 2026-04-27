from django.contrib import admin
from .models import Shipment, ShipmentDocument, HSCode, ShipmentHSCode, StatusLog

@admin.register(HSCode)
class HSCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'description', 'duty_rate', 'unit', 'chapter', 'is_active']
    search_fields = ['code', 'description']
    list_filter = ['is_active', 'chapter']
    list_editable = ['duty_rate', 'is_active']

@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ['hawb_number', 'consignee', 'declarant', 'status', 'submitted_at']
    search_fields = ['hawb_number']
    list_filter = ['status', 'urgency']

@admin.register(ShipmentDocument)
class ShipmentDocumentAdmin(admin.ModelAdmin):
    list_display = ['shipment', 'document_type', 'uploaded_at']

@admin.register(StatusLog)
class StatusLogAdmin(admin.ModelAdmin):
    list_display = ['shipment', 'old_status', 'new_status', 'changed_by', 'changed_at']