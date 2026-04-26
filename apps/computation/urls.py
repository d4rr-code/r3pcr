from django.urls import path
from . import views

app_name = 'computation'

urlpatterns = [
    path('compute/<int:shipment_id>/', views.compute_shipment, name='compute'),
    path('hs-search/', views.hs_code_search, name='hs_search'),
    path('advisory/<int:shipment_id>/', views.shipping_advisory, name='advisory'),
]