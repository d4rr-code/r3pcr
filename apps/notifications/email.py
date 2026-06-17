"""Robust background email sending — shared by every app.

Replaces the old fire-and-forget `print()`-on-error threads. Failures are now
**logged** (visible in Railway logs) and **retried** with a short backoff, so
OTP / verification / status emails fail loudly instead of silently vanishing.
"""
import logging
import threading
import time

from django.conf import settings
from django.core.mail import send_mail
from django.db import close_old_connections

logger = logging.getLogger('r3pcr.email')


def send_email_async(subject, message, recipient_list, html_message=None,
                     from_email=None, log_tag='', retries=2):
    """Send an email in a daemon thread with retry + logging.

    - Empty/None recipients are filtered out (and a no-op is logged).
    - Each attempt is logged; a permanent failure is logged at ERROR level.
    - The thread closes its DB connection on exit so it isn't leaked.
    """
    from_email = from_email or settings.DEFAULT_FROM_EMAIL
    recipients = [r for r in (recipient_list or []) if r]
    tag = log_tag or subject

    if not recipients:
        logger.warning('Email skipped — no valid recipients (%s)', tag)
        return

    def _worker():
        last_error = None
        try:
            for attempt in range(1, retries + 2):
                try:
                    send_mail(
                        subject=subject,
                        message=message,
                        from_email=from_email,
                        recipient_list=recipients,
                        html_message=html_message,
                        fail_silently=False,
                    )
                    logger.info('Email sent (%s) -> %s', tag, recipients)
                    return
                except Exception as e:
                    last_error = e
                    logger.warning('Email attempt %d/%d failed (%s): %s',
                                   attempt, retries + 1, tag, e)
                    if attempt <= retries:
                        time.sleep(2 * attempt)   # 2s, 4s backoff
            logger.error('Email PERMANENTLY failed (%s) -> %s: %s',
                         tag, recipients, last_error)
        finally:
            close_old_connections()

    threading.Thread(target=_worker, daemon=True).start()
