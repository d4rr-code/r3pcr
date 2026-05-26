from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('',                                views.notifications_list,  name='list'),
    path('<int:notification_id>/',          views.notification_detail, name='detail'),
    path('<int:notification_id>/json/',     views.notification_json,   name='json'),
    path('read/<int:notification_id>/',     views.mark_read,           name='mark_read'),
    path('mark-all-read/',                  views.mark_all_read,       name='mark_all_read'),
]
