from django.urls import path
from . import views

app_name = 'supervisor'

urlpatterns = [
    path('dashboard/',   views.dashboard,       name='dashboard'),
    path('users/',       views.user_management, name='users'),
    path('users/add/',   views.add_user,        name='add_user'),
    path('users/toggle/<int:user_id>/',         views.toggle_user,          name='toggle_user'),
    path('users/approve/<int:user_id>/',        views.approve_registration,  name='approve_registration'),
    path('users/reject/<int:user_id>/',         views.reject_registration,   name='reject_registration'),
    path('analytics/',   views.analytics,       name='analytics'),
    path('config/',      views.system_config,   name='config'),
    path('reset/<int:shipment_id>/',            views.reset_shipment,        name='reset_shipment'),
    path('status/<int:shipment_id>/',           views.update_shipment_status, name='update_shipment_status'),
    path('delete/<int:shipment_id>/',           views.delete_shipment,       name='delete_shipment'),
    # Memos & Announcements
    path('memos/',                              views.list_memos,            name='memos'),
    path('memos/create/',                       views.create_memo,           name='create_memo'),
    path('memos/delete/<int:memo_id>/',         views.delete_memo,           name='delete_memo'),
    path('memos/toggle/<int:memo_id>/',              views.toggle_memo,       name='toggle_memo'),
    # Feedbacks
    path('feedbacks/',                               views.manage_feedbacks,  name='feedbacks'),
    path('feedbacks/approve/<int:feedback_id>/',     views.approve_feedback,  name='approve_feedback'),
    path('feedbacks/reject/<int:feedback_id>/',      views.reject_feedback,   name='reject_feedback'),
]
