import json
import logging
import os
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, date as date_type
from decimal import Decimal, InvalidOperation
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Avg, Sum, Min, Max, F, ExpressionWrapper, DurationField, Q
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.core.mail import send_mail
from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpResponse
from apps.accounts.models import User
from apps.accounts.views import _validate_phone_number
from apps.shipments.models import Shipment, HSCode, StatusLog, TariffSchedule, HSCodeRate
from apps.computation.models import DutyComputation, ShippingAdvisory
from apps.consignee.models import Feedback
from apps.notifications.utils import create_notification, notify_shipment_status_change
from apps.supervisor.exchange_rates import ensure_daily_exchange_rates
from ..models import SystemConfig, Announcement, IssueReport

logger = logging.getLogger(__name__)


# ─── Business-day helpers ─────────────────────────────────────────────────────

URGENCY_BUSINESS_DAYS = {
    'rush': 3, 'urgent': 5, 'priority': 10, 'standard': 15, 'normal': 15,
}


def _urgency_business_days():
    values = dict(URGENCY_BUSINESS_DAYS)
    try:
        rows = {
            sc.key: sc.value
            for sc in SystemConfig.objects.filter(
                key__in=[f'urgency_days_{k}' for k in ('standard', 'priority', 'urgent', 'rush')]
            )
        }
    except Exception:
        rows = {}
    for key in ('standard', 'priority', 'urgent', 'rush'):
        try:
            days = int(rows.get(f'urgency_days_{key}', ''))
        except (TypeError, ValueError):
            continue
        if 1 <= days <= 60:
            values[key] = days
    values['normal'] = values['standard']
    return values


def _urgency_days_for(urgency):
    days = _urgency_business_days()
    return days.get(urgency or 'standard', days['standard'])


def _add_business_days(start_dt, n):
    """Return date that is n business days (Mon–Fri) after start_dt."""
    d = start_dt.date() if hasattr(start_dt, 'date') else start_dt
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _business_days_diff(from_date, to_date):
    """Signed count of business days from from_date to to_date.
    Positive = future (days left), negative = past (overdue)."""
    from_date = from_date.date() if hasattr(from_date, 'date') else from_date
    to_date   = to_date.date()   if hasattr(to_date,   'date') else to_date
    if from_date == to_date:
        return 0
    sign = 1 if to_date > from_date else -1
    a, b = (from_date, to_date) if to_date > from_date else (to_date, from_date)
    count, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return sign * count


#  HS Code Section / Chapter Hierarchy
_HS_SECTIONS = [
    (1,  'I',     'Live Animals; Animal Products',                  list(range(1, 6))),
    (2,  'II',    'Vegetable Products',                             list(range(6, 15))),
    (3,  'III',   'Animal or Vegetable Fats and Oils',             [15]),
    (4,  'IV',    'Prepared Foodstuffs; Beverages; Tobacco',        list(range(16, 25))),
    (5,  'V',     'Mineral Products',                               list(range(25, 28))),
    (6,  'VI',    'Chemical or Allied Industry Products',            list(range(28, 39))),
    (7,  'VII',   'Plastics and Rubber',                            [39, 40]),
    (8,  'VIII',  'Raw Hides, Leather, Furskins',                  list(range(41, 44))),
    (9,  'IX',    'Wood, Cork, Straw',                             list(range(44, 47))),
    (10, 'X',     'Pulp of Wood, Paper, Paperboard',               list(range(47, 50))),
    (11, 'XI',    'Textiles and Textile Articles',                 list(range(50, 64))),
    (12, 'XII',   'Footwear, Headgear, Umbrellas',                 list(range(64, 68))),
    (13, 'XIII',  'Articles of Stone, Ceramics, Glass',            list(range(68, 71))),
    (14, 'XIV',   'Precious Stones, Precious Metals',              [71]),
    (15, 'XV',    'Base Metals and Articles',                      list(range(72, 84))),
    (16, 'XVI',   'Machinery and Mechanical Appliances',           [84, 85]),
    (17, 'XVII',  'Vehicles, Aircraft, Vessels',                   list(range(86, 90))),
    (18, 'XVIII', 'Optical, Photographic, Medical Instruments',    list(range(90, 93))),
    (19, 'XIX',   'Arms and Ammunition',                            [93]),
    (20, 'XX',    'Miscellaneous Manufactured Articles',            list(range(94, 97))),
    (21, 'XXI',   'Works of Art, Collectors Pieces, Antiques',     [97]),
]

def _chapter_num(chapter_str):
    """Extract numeric chapter from 'Chapter 84', '84', 'Chapter 01', '01', etc."""
    if not chapter_str:
        return None
    m = re.search(r'\d+', str(chapter_str))
    return int(m.group()) if m else None


def _send_mail_async(subject, message, from_email, recipient_list, html_message=None, log_tag=''):
    """Send email in the background with retry + logging (delegates to the
    shared, hardened helper). Signature kept for existing call sites."""
    from apps.notifications.email import send_email_async
    send_email_async(subject, message, recipient_list, html_message=html_message,
                     from_email=from_email, log_tag=log_tag)

def supervisor_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.role != 'supervisor':
            from apps.accounts.views.common import redirect_by_role
            return redirect_by_role(request.user)
        return view_func(request, *args, **kwargs)
    return wrapper


#  Dashboard 



__all__ = [
    'supervisor_required', '_HS_SECTIONS', '_chapter_num', '_send_mail_async',
    'URGENCY_BUSINESS_DAYS', '_urgency_business_days', '_urgency_days_for',
    '_add_business_days', '_business_days_diff',
]
