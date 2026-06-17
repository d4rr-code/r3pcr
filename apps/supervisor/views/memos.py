import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.accounts.models import User
from apps.notifications.utils import create_notification
from ..models import Announcement

logger = logging.getLogger(__name__)

from .common import *  # noqa: F401,F403

def _announcement_recipients(announcement):
    return User.objects.filter(
        role__in=announcement.target_roles(),
        is_active=True,
        is_pending_approval=False,
    )


def _notify_announcement_recipients(announcement):
    recipients = _announcement_recipients(announcement)
    for recipient in recipients:
        create_notification(
            recipient=recipient,
            shipment=None,
            notification_type='announcement',
            title=announcement.title,
            message=announcement.content,
            announcement=announcement,
        )
    announcement.notified_at = timezone.now()
    announcement.save(update_fields=['notified_at', 'updated_at'])
    return recipients.count()


@login_required
@supervisor_required
def list_memos(request):
    memos = Announcement.objects.all()
    return render(request, 'supervisor/memos.html', {'memos': memos})


@login_required
@supervisor_required
def create_memo(request):
    if request.method == 'POST':
        title     = request.POST.get('title', '').strip()
        content   = request.POST.get('content', '').strip()
        category  = request.POST.get('category', 'general')
        audience  = request.POST.get('target_audience', 'all')
        is_active = request.POST.get('is_active') == '1'
        valid_audiences = {choice[0] for choice in Announcement.AUDIENCE_CHOICES}
        if audience not in valid_audiences:
            audience = 'all'
        if not title or not content:
            messages.error(request, 'Title and content are required.')
        else:
            announcement = Announcement.objects.create(
                title=title, content=content,
                category=category, target_audience=audience,
                is_active=is_active, created_by=request.user,
            )
            if announcement.is_active:
                notified_count = _notify_announcement_recipients(announcement)
                messages.success(
                    request,
                    f'Announcement "{title}" published and sent to {notified_count} user(s).',
                )
            else:
                messages.success(request, f'Announcement "{title}" saved as hidden.')
    return redirect('supervisor:memos')


@login_required
@supervisor_required
def delete_memo(request, memo_id):
    if request.method == 'POST':
        memo = get_object_or_404(Announcement, id=memo_id)
        title = memo.title
        memo.delete()
        messages.success(request, f'Announcement "{title}" deleted.')
    return redirect('supervisor:memos')


@login_required
@supervisor_required
def toggle_memo(request, memo_id):
    if request.method == 'POST':
        memo = get_object_or_404(Announcement, id=memo_id)
        memo.is_active = not memo.is_active
        memo.save()
        state = 'published' if memo.is_active else 'archived'
        if memo.is_active and not memo.notified_at:
            notified_count = _notify_announcement_recipients(memo)
            messages.success(request, f'"{memo.title}" {state} and sent to {notified_count} user(s).')
        else:
            messages.success(request, f'"{memo.title}" {state}.')
    return redirect('supervisor:memos')


#  System Configuration 

