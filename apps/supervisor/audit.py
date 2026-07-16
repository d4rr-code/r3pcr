import logging

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def log_audit(action, summary, *, user=None, request=None, shipment=None, target=None, details=None):
    """Best-effort audit writer. Audit failures must not break user workflows."""
    try:
        from .models import AuditLog

        actor = user
        if actor is None and request is not None and getattr(request, 'user', None) is not None:
            if request.user.is_authenticated:
                actor = request.user

        target_type = ''
        target_id = ''
        if target is not None:
            target_type = target.__class__.__name__
            target_id = str(getattr(target, 'pk', '') or '')

        AuditLog.objects.create(
            user=actor if getattr(actor, 'is_authenticated', False) else None,
            user_role=getattr(actor, 'role', '') or '',
            action=action,
            shipment=shipment,
            target_type=target_type,
            target_id=target_id,
            summary=str(summary)[:240],
            details=details or {},
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '')[:255] if request is not None else ''),
        )
    except Exception as exc:
        logger.debug('Audit logging failed for %s: %s', action, exc)
