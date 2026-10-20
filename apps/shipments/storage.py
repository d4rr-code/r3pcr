"""Shipment URLs always pass through application object authorization."""
from django.urls import reverse
from storages.backends.s3 import S3Storage


class PrivateS3Storage(S3Storage):
    default_acl = None
    querystring_auth = True
    custom_domain = None

    def url(self, name, parameters=None, expire=None, http_method=None):
        return reverse('protected_media', kwargs={'path': name})
