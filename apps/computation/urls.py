from django.urls import path
from . import views

app_name = 'computation'

urlpatterns = [
    path('compute/<int:shipment_id>/',                  views.compute_shipment,    name='compute'),
    path('ocr-extract/<int:shipment_id>/<int:doc_id>/', views.ocr_extract,         name='ocr_extract'),
    path('ocr-extract-all/<int:shipment_id>/',         views.ocr_extract_all,    name='ocr_extract_all'),
    path('download/<int:shipment_id>/',                 views.download_computation, name='download'),
    path('hs-search/',                                  views.hs_code_search,      name='hs_search'),
    path('hs-suggest/',                                 views.hs_code_suggest,     name='hs_suggest'),
    path('advisory/<int:shipment_id>/',                 views.shipping_advisory,       name='advisory'),
    path('save-advisory/<int:shipment_id>/',            views.save_declarant_advisory, name='save_advisory'),
]
