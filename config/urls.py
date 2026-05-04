from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
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
    return render(request, 'landing.html')


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', landing, name='landing'),
    path('accounts/', include('apps.accounts.urls', namespace='accounts')),
    path('supervisor/', include('apps.supervisor.urls', namespace='supervisor')),
    path('consignee/', include('apps.consignee.urls', namespace='consignee')),
    path('declarant/', include('apps.declarant.urls', namespace='declarant')),
    path('computation/', include('apps.computation.urls', namespace='computation')),
    path('notifications/', include('apps.notifications.urls', namespace='notifications')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
