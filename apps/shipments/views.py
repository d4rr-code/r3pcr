
"""Authorized streaming of private shipment and issue-report files."""
import mimetypes
from pathlib import PurePosixPath

from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, Http404
from django.views.decorators.http import require_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import Shipment


@login_required
@require_safe
@xframe_options_sameorigin
def protected_media(request, path):
    parts = PurePosixPath(path).parts
    if not parts or '..' in parts or path.startswith(('/', '\\')) or '\\' in path:
        raise Http404
    shipments = Shipment.objects.filter(Q(documents__file=path) | Q(payment_receipt=path))
    if request.user.role == 'consignee':
        shipments = shipments.filter(consignee=request.user)
    elif request.user.role == 'declarant':
        shipments = shipments.filter(declarant=request.user)
    elif request.user.role != 'supervisor':
        raise Http404
    from apps.supervisor.models import IssueReport
    issues = IssueReport.objects.filter(attachment=path)
    if request.user.role != 'supervisor':
        issues = issues.filter(reporter=request.user)
    issue_attachment = issues.exists()
    if not shipments.exists() and not issue_attachment:
        raise Http404
    try:
        source = default_storage.open(path, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404
    content_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    inline = not issue_attachment and content_type in {'application/pdf', 'image/jpeg', 'image/png'}
    response = FileResponse(source, as_attachment=not inline, filename=parts[-1], content_type=content_type)
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return response
