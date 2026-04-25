from django.urls import path
from . import views

app_name = 'declarant'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('queue/', views.queue_manager, name='queue'),
    path('claim/<int:shipment_id>/', views.claim_shipment, name='claim'),
    path('process/<int:shipment_id>/', views.process_shipment, name='process'),
]