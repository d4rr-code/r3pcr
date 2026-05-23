from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .models import Notification


@login_required
def notifications_list(request):
    filter_type   = request.GET.get('filter', 'all')
    notifications = Notification.objects.filter(recipient=request.user)

    if filter_type == 'unread':
        notifications = notifications.filter(is_read=False)
    elif filter_type == 'read':
        notifications = notifications.filter(is_read=True)

    return render(request, 'notifications/list.html', {
        'notifications': notifications,
        'filter_type':   filter_type,
        'unread_total':  Notification.objects.filter(recipient=request.user, is_read=False).count(),
    })


@login_required
def notification_detail(request, notification_id):
    """Show a single notification and mark it as read."""
    notif = get_object_or_404(Notification, id=notification_id, recipient=request.user)

    # Mark as read on open
    if not notif.is_read:
        notif.is_read = True
        notif.save()

    return render(request, 'notifications/detail.html', {'notif': notif})


@login_required
def mark_read(request, notification_id):
    notification = get_object_or_404(
        Notification, id=notification_id, recipient=request.user
    )
    notification.is_read = True
    notification.save()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    return redirect('notifications:list')


@login_required
def mark_all_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return redirect('notifications:list')
