from .models import Notification


def unread_notification_count(request):
    ctx = {
        'unread_notification_count': 0,
        'recent_announcements': [],
    }
    if request.user.is_authenticated:
        ctx['unread_notification_count'] = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        # Pull active announcements for all roles (imported here to avoid circular at module level)
        try:
            from apps.supervisor.models import Announcement
            ctx['recent_announcements'] = list(
                Announcement.objects.filter(is_active=True).order_by('-created_at')[:5]
            )
        except Exception:
            pass
    return ctx
