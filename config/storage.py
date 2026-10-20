"""Prevent accidental file access across Supabase environments."""
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


def validate_storage_project(database, storage_url):
    endpoint = urlparse(storage_url)
    hostname = endpoint.hostname or ''
    suffix = '.supabase.co'
    storage_ref = hostname[:-len(suffix)] if hostname.endswith(suffix) else ''
    username = database.get('USER') or ''
    database_ref = username.partition('.')[2]
    host = database.get('HOST') or ''
    if not database_ref and host.startswith('db.') and host.endswith(suffix):
        database_ref = host[3:-len(suffix)]
    if (endpoint.scheme != 'https' or not storage_ref or '.' in storage_ref
            or not database_ref or database_ref != storage_ref):
        raise ImproperlyConfigured(
            'Supabase storage must match the database project. Configure matching dev '
            'credentials or use SHIPMENT_STORAGE_MODE=local for local development.'
        )
