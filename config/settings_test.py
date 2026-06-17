"""Test settings — isolated, offline, fast.

Uses an in-memory SQLite database so the test suite NEVER touches Supabase
(the session pooler can't ``CREATE DATABASE``) and runs fully offline. The
app's migrations are ORM-only (``RunPython`` data migrations, no raw SQL or
``contrib.postgres`` fields), so the schema builds cleanly on SQLite.

Run:
    python manage.py test --settings=config.settings_test
"""
from .settings import *  # noqa: F401,F403

# ── Isolated, in-memory database ──────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# ── Fast + offline test environment ───────────────────────────────────────────
# MD5 hashing keeps user-creation cheap; locmem email captures mail without SMTP.
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Quiet the console during test runs.
LOGGING = {'version': 1, 'disable_existing_loggers': True}

# The production security flags (settings.py gates these on ``not DEBUG``) would
# make the test client follow an SSL redirect / drop cookies — force them off
# here regardless of the local .env DEBUG value.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
