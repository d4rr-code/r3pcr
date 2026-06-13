import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from apps.accounts.models import User
from apps.shipments.models import Shipment, ShipmentDocument, StatusLog
from apps.shipments.status_progress import build_status_progress
from apps.shipments.fan import fan_assessment_has_values, fan_assessment_rows
from apps.notifications.utils import create_notification, notify_incoming_shipment, notify_shipment_status_change
from apps.supervisor.models import IssueReport
from ..models import Feedback

logger = logging.getLogger('r3pcr.consignee')

def consignee_required(view_func):
    """Restrict view to authenticated users with role='consignee'."""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != 'consignee':
            messages.error(request, 'Access denied — consignees only.')
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


URGENCY_BUSINESS_DAYS = {
    'rush': 3,
    'urgent': 5,
    'priority': 10,
    'standard': 15,
}




def generate_hawb():
    year   = timezone.now().year
    prefix = f'R3PCR-{year}-'
    last   = (
        Shipment.objects
        .filter(hawb_number__startswith=prefix)
        .order_by('hawb_number')
        .last()
    )
    if last:
        try:
            seq      = int(last.hawb_number[len(prefix):])
            next_seq = seq + 1
        except (ValueError, IndexError):
            next_seq = 1
    else:
        next_seq = 1

    hawb = f'{prefix}{str(next_seq).zfill(6)}'
    while Shipment.objects.filter(hawb_number=hawb).exists():
        next_seq += 1
        hawb = f'{prefix}{str(next_seq).zfill(6)}'
    return hawb


# ─── Dashboard ────────────────────────────────────────────────────────────────

