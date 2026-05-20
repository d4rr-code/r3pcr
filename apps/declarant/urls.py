from django.urls import path
from . import views

app_name = 'declarant'

urlpatterns = [
    path('dashboard/',                            views.dashboard,          name='dashboard'),
    path('preview/<int:shipment_id>/',            views.shipment_preview,   name='preview'),
    path('queue/', views.queue_manager, name='queue'),
    path('claim/<int:shipment_id>/', views.claim_shipment, name='claim'),
    path('process/<int:shipment_id>/', views.process_shipment, name='process'),
    path('update-status/<int:shipment_id>/', views.update_status, name='update_status'),
    path('update-shipping-mode/<int:shipment_id>/', views.update_shipping_mode, name='update_shipping_mode'),
    path('payment/<int:shipment_id>/', views.payment_confirmation, name='payment'),
    path('boc/<int:shipment_id>/', views.boc_tracking, name='boc'),
    path('flag-deficiency/<int:shipment_id>/', views.flag_deficiency, name='flag_deficiency'),
]
