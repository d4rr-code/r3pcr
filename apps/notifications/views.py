from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Notification

@login_required
def notifications_list(request):
    notifications = Notification.objects.filter(recipient=request.user)
    
    # Mark all as read when viewed
    notifications.filter(is_read=False).update(is_read=True)
    
    return render(request, 'notifications/list.html', {
        'notifications': notifications
    })

@login_required
def mark_read(request, notification_id):
    notification = get_object_or_404(
        Notification, 
        id=notification_id, 
        recipient=request.user
    )
    notification.is_read = True
    notification.save()
    return redirect('notifications:list')