from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .models import Notification

_MONTH_ABBR = ['Jan','Feb','Mar','Apr','May','Jun',
                'Jul','Aug','Sep','Oct','Nov','Dec']

def _fmt_short(dt):
    """M/D/YYYY at H:MM AM/PM  — cross-platform, no leading zeros."""
    if not dt:
        return ''
    h    = dt.hour % 12 or 12
    ampm = 'AM' if dt.hour < 12 else 'PM'
    return f'{dt.month}/{dt.day}/{dt.year} at {h}:{dt.strftime("%M")} {ampm}'

def _fmt_long(dt):
    """Mon D, YYYY at H:MM AM/PM  — cross-platform, no leading zeros."""
    if not dt:
        return ''
    h    = dt.hour % 12 or 12
    ampm = 'AM' if dt.hour < 12 else 'PM'
    return f'{_MONTH_ABBR[dt.month - 1]} {dt.day}, {dt.year} at {h}:{dt.strftime("%M")} {ampm}'

# ── Next-step descriptions per shipment status ────────────────────────────────
_NEXT_STEP = {
    'incoming':    'Waiting for a declarant to claim your shipment.',
    'arrived':     'Declarant will compute duties and taxes.',
    'computed':    'Please review and approve the computation.',
    'approved':    'Shipment will be lodged to customs.',
    'lodgement':   'Awaiting final customs assessment.',
    'ongoing':     'Customs assessment is in progress.',
    'assessed':    'Please arrange payment of duties and taxes.',
    'paid':        'Awaiting CDH discharge and delivery schedule.',
    'released':    'Shipment has been cleared from customs.',
    'billed':      'Please arrange payment of brokerage fees.',
    'for_revision':'Declarant is reviewing your revision request.',
    'rejected':    'Please contact your declarant for further assistance.',
}

# ── Status sub-labels (what "arrived" means to a consignee) ──────────────────
_STATUS_SUBLABEL = {
    'incoming':    'In Queue',
    'arrived':     'Claimed by Declarant',
    'computed':    'Awaiting Your Approval',
    'approved':    'Approved — Proceeding to Lodgement',
    'lodgement':   'Lodged to Customs',
    'ongoing':     'Under Customs Assessment',
    'assessed':    'Assessed — Payment Required',
    'paid':        'Payment Confirmed',
    'released':    'Cargo Released',
    'billed':      'Brokerage Fee Due',
    'for_revision':'Revision Requested',
    'rejected':    'Rejected',
}


_ROLE_PREFIXES = ('consignee', 'supervisor', 'declarant')


def _role_template(role, role_filename, fallback):
    """Route to a role-specific light-theme template, with a neutral fallback."""
    if role in _ROLE_PREFIXES:
        return f'{role}/{role_filename}'
    return fallback


@login_required
def notifications_list(request):
    filter_type   = request.GET.get('filter', 'all')
    q             = request.GET.get('q', '').strip()
    notifications = Notification.objects.filter(recipient=request.user)

    if filter_type == 'unread':
        notifications = notifications.filter(is_read=False)
    elif filter_type == 'read':
        notifications = notifications.filter(is_read=True)

    if q:
        from django.db.models import Q
        notifications = notifications.filter(
            Q(title__icontains=q) | Q(message__icontains=q) |
            Q(shipment__hawb_number__icontains=q)
        )

    unread_total = Notification.objects.filter(recipient=request.user, is_read=False).count()

    # Route each role to its own light-theme template
    template = _role_template(request.user.role, 'notifications.html', 'notifications/list.html')
    return render(request, template, {
        'notifications': notifications,
        'filter_type':   filter_type,
        'unread_total':  unread_total,
        'q':             q,
    })


@login_required
def notification_detail(request, notification_id):
    """Show a single notification and mark it as read."""
    notif = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save()
    template = _role_template(request.user.role, 'notifications_detail.html',
                              'notifications/detail.html')
    return render(request, template, {'notif': notif})


def _is_announcement(notif, announcement):
    return bool(announcement) or (
        notif.notification_type == 'announcement'
        or (not notif.shipment and notif.title.lower().startswith('announcement:'))
    )


def _announcement_fields(notif, announcement, is_announcement):
    """(title, category, content) for an announcement notification, else blanks."""
    if not is_announcement:
        return '', '', ''
    if announcement:
        return announcement.title, announcement.get_category_display(), announcement.content
    return (notif.title.replace('Announcement:', '', 1).strip(), 'General', notif.message)


def _shipment_fields(shipment):
    """Shipment-derived display fields for the modal; blanks when no shipment."""
    if not shipment:
        return {
            'hawb_number': '', 'status_code': '', 'status_display': '',
            'status_sublabel': '', 'next_step': '', 'submitted_at': '',
            'updated_at': '', 'shipment_id': None, 'declarant': '',
        }
    status_code = shipment.status
    status_lbl  = shipment.get_status_display()
    declarant   = ''
    if shipment.declarant:
        declarant = shipment.declarant.get_full_name() or shipment.declarant.username
    return {
        'hawb_number':     shipment.hawb_number,
        'status_code':     status_code,
        'status_display':  status_lbl,
        'status_sublabel': _STATUS_SUBLABEL.get(status_code, status_lbl),
        'next_step':       _NEXT_STEP.get(status_code, ''),
        'submitted_at':    _fmt_short(shipment.submitted_at),
        'updated_at':      _fmt_long(shipment.updated_at),
        'shipment_id':     shipment.id,
        'declarant':       declarant,
    }


@login_required
def notification_json(request, notification_id):
    """Return notification detail as JSON (used by the consignee modal)."""
    notif = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    if not notif.is_read:
        notif.is_read = True
        notif.save()

    announcement = getattr(notif, 'announcement', None)
    is_announcement = _is_announcement(notif, announcement)
    ann_title, ann_category, ann_content = _announcement_fields(
        notif, announcement, is_announcement)
    ship = _shipment_fields(notif.shipment)

    return JsonResponse({
        'id':               notif.id,
        'title':            notif.title,
        'message':          notif.message,
        'is_announcement':  is_announcement,
        'announcement_title': ann_title,
        'announcement_category': ann_category,
        'announcement_content': ann_content,
        'hawb_number':      ship['hawb_number'],
        'created_at':       _fmt_long(notif.created_at),
        'notification_type': notif.notification_type,
        'status_code':      ship['status_code'],
        'status_display':   ship['status_display'],
        'status_sublabel':  ship['status_sublabel'],
        'declarant':        ship['declarant'],
        'next_step':        ship['next_step'],
        'submitted_at':     ship['submitted_at'],
        'updated_at':       ship['updated_at'],
        'shipment_id':      ship['shipment_id'],
    })


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
