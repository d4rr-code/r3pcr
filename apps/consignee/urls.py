from django.urls import path
from . import views

app_name = 'consignee'

urlpatterns = [
    path('dashboard/',                        views.dashboard,       name='dashboard'),
    path('submit/',                           views.submit_shipment, name='submit'),
    path('submissions/',                      views.my_submissions,  name='my_submissions'),
    path('system-reference/',                 views.system_reference, name='system_reference'),
    path('system-reference/parameters/',      views.system_parameters, name='system_parameters'),
    path('system-reference/fees/',            views.system_fees, name='system_fees'),
    path('system-reference/wmcda/',           views.system_wmcda, name='system_wmcda'),
    path('report-issue/',                     views.report_issue, name='report_issue'),
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
