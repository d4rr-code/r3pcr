from django.urls import path
from . import views

app_name = 'supervisor'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('users/', views.user_management, name='users'),
    path('users/add/', views.add_user, name='add_user'),
    path('users/toggle/<int:user_id>/', views.toggle_user, name='toggle_user'),
    path('analytics/', views.analytics, name='analytics'),
    path('config/', views.system_config, name='config'),
    path('reset/<int:shipment_id>/',  views.reset_shipment,  name='reset_shipment'),
    path('delete/<int:shipment_id>/', views.delete_shipment, name='delete_shipment'),
]
