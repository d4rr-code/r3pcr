from django.urls import path
from . import views

app_name = 'consignee'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('submit/', views.submit_shipment, name='submit'),
    path('submissions/', views.my_submissions, name='my_submissions'),
]