from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required


def landing(request):
    if request.user.is_authenticated:
        role = getattr(request.user, 'role', None)
        if role == 'consignee':
            return redirect('/consignee/dashboard/')
        elif role == 'declarant':
            return redirect('/declarant/dashboard/')
        elif role == 'supervisor':
            return redirect('/supervisor/dashboard/')
    from apps.consignee.models import Feedback
    feedbacks = Feedback.objects.filter(is_approved=True).select_related('consignee').order_by('-created_at')[:6]
    return render(request, 'landing.html', {'feedbacks': feedbacks})


def track_shipment(request):
    """Public shipment tracking — requires HAWB + consignee email for verification."""
    result = None
    error  = None

    if request.method == 'POST':
        hawb  = request.POST.get('hawb_number', '').strip()
        email = request.POST.get('consignee_email', '').strip().lower()

        if not hawb or not email:
            error = 'Please enter both your Shipment ID and registered email.'
        else:
            from apps.shipments.models import Shipment
            try:
                shipment = Shipment.objects.select_related('consignee').get(
                    hawb_number__iexact=hawb,
                    consignee__email__iexact=email,
                )
                # Only expose safe, non-sensitive fields
                result = {
                    'hawb_number':  shipment.hawb_number,
                    'status':       shipment.get_status_display(),
                    'status_code':  shipment.status,
                    'urgency':      shipment.get_urgency_display(),
                    'import_type':  shipment.get_import_type_display(),
                    'submitted_at': shipment.submitted_at,
                    'last_updated': shipment.updated_at,
                    'boc_reference': shipment.boc_reference,
                    'boc_status':   shipment.boc_status,
                }
            except Shipment.DoesNotExist:
                error = 'No shipment found with that ID and email combination. Please check your details.'

    from apps.consignee.models import Feedback
    feedbacks = Feedback.objects.filter(is_approved=True).select_related('consignee').order_by('-created_at')[:6]
    return render(request, 'landing.html', {
        'track_result': result,
        'track_error':  error,
        'track_mode':   True,
        'feedbacks':    feedbacks,
    })


urlpatterns = [
    path('admin/',  admin.site.urls),
    path('',        landing,         name='landing'),
    path('track/',  track_shipment,  name='track_shipment'),
    path('accounts/',      include('apps.accounts.urls',      namespace='accounts')),
    path('supervisor/',    include('apps.supervisor.urls',    namespace='supervisor')),
    path('consignee/',     include('apps.consignee.urls',     namespace='consignee')),
    path('declarant/',     include('apps.declarant.urls',     namespace='declarant')),
    path('computation/',   include('apps.computation.urls',   namespace='computation')),
    path('notifications/', include('apps.notifications.urls', namespace='notifications')),
]

# Serve media files in all environments using serve() directly — static() ignores DEBUG=False
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
