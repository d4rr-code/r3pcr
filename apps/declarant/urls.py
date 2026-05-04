from django.urls import path
from . import views

app_name = 'declarant'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('queue/', views.queue_manager, name='queue'),
    path('claim/<int:shipment_id>/', views.claim_shipment, name='claim'),
    path('process/<int:shipment_id>/', views.process_shipment, name='process'),
    path('update-status/<int:shipment_id>/', views.update_status, name='update_status'),
    path('payment/<int:shipment_id>/', views.payment_confirmation, name='payment'),
    path('boc/<int:shipment_id>/', views.boc_tracking, name='boc'),
]
