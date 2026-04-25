from django.urls import path
from . import views

app_name = 'supervisor'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
]