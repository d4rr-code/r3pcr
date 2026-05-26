from django.urls import path
from . import views

app_name = 'consignee'

urlpatterns = [
    path('dashboard/',                        views.dashboard,       name='dashboard'),
    path('submit/',                           views.submit_shipment, name='submit'),
    path('submissions/',                      views.my_submissions,  name='my_submissions'),
    path('shipment/<int:shipment_id>/',       views.shipment_detail, name='shipment_detail'),
    path('cancel/<int:shipment_id>/',         views.cancel_submission,  name='cancel'),
    path('shipment/<int:shipment_id>/feedback/',       views.submit_feedback,     name='feedback'),
    path('shipment/<int:shipment_id>/approve/',   views.approve_computation,  name='approve_computation'),
    path('shipment/<int:shipment_id>/revise/',    views.revise_computation,   name='revise_computation'),
    path('shipment/<int:shipment_id>/reject/',    views.reject_computation,   name='reject_computation'),
    path('shipment/<int:shipment_id>/download/',  views.download_computation, name='download_computation'),
    path('shipment/<int:shipment_id>/resubmit/',  views.resubmit_documents,   name='resubmit'),
    path('chart-data/',                           views.chart_data,           name='chart_data'),
]
