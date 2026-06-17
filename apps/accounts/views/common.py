"""Shared helpers for the accounts views package: background email sending and
role-based post-login redirect."""
import logging
from django.shortcuts import redirect

logger = logging.getLogger('r3pcr.accounts')


def _send_mail_async(subject, message, from_email, recipient_list, html_message=None, log_tag=''):
    """Send email in the background with retry + logging (delegates to the
    shared, hardened helper). Signature kept for existing call sites."""
    from apps.notifications.email import send_email_async
    send_email_async(subject, message, recipient_list, html_message=html_message,
                     from_email=from_email, log_tag=log_tag)


def redirect_by_role(user):
    if user.role == 'consignee':
        return redirect('/consignee/dashboard/')
    elif user.role == 'declarant':
        return redirect('/declarant/dashboard/')
    elif user.role == 'supervisor':
        return redirect('/supervisor/dashboard/')
    return redirect('accounts:login')
