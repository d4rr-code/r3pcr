from django.urls import path
from . import views

app_name = 'declarant'

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
]