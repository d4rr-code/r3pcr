from pathlib import Path
from dotenv import load_dotenv
import os
try:
    import pytesseract
    _PYTESSERACT_AVAILABLE = True
except ImportError:
    _PYTESSERACT_AVAILABLE = False

load_dotenv(override=True)  # always prefer .env values over system env vars

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY')

DEBUG = os.getenv('DEBUG') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

CSRF_TRUSTED_ORIGINS = os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:8000').split(',')


# ── Production security hardening ──────────────────────────────────────────────
# Only active when DEBUG is off (i.e. on Railway/production); local dev is
# unaffected so HTTP localhost still works.
if not DEBUG:
    # Railway terminates TLS at a proxy and forwards the scheme in this header —
    # required so Django knows the request is HTTPS (otherwise SSL redirect loops).
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000          # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True


INSTALLED_APPS = [
    'anymail',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # R3PCR Apps
    'apps.accounts',
    'apps.shipments',
    'apps.computation',
    'apps.analytics',
    'apps.notifications',

    # Role Apps
    'apps.supervisor',
    'apps.consignee',
    'apps.declarant',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.notifications.context_processors.unread_notification_count',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
        'OPTIONS': {
            'sslmode': os.getenv('DB_SSLMODE', 'require'),
        },
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Logging — surface app events (esp. email send failures) to the console,
# which Railway captures in its deployment logs.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[{levelname}] {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'r3pcr': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True


# Static files
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── Storage backends (Django 6 STORAGES dict) ─────────────────────────────────
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

# ── Supabase Storage (django-storages S3 backend) ─────────────────────────────
_SUPABASE_URL   = os.getenv('SUPABASE_URL', '')          # e.g. https://xxxx.supabase.co
_SUPABASE_KEY   = os.getenv('SUPABASE_SECRET_KEY', '')   # sb_secret_...
_SUPABASE_BUCKET = os.getenv('SUPABASE_BUCKET', 'r3pcr-media')

if _SUPABASE_URL and _SUPABASE_KEY:
    STORAGES['default']['BACKEND'] = 'storages.backends.s3boto3.S3Boto3Storage'

    _project_id = _SUPABASE_URL.replace('https://', '').split('.')[0]

    AWS_ACCESS_KEY_ID       = os.getenv('SUPABASE_S3_ACCESS_KEY_ID', '')
    AWS_SECRET_ACCESS_KEY   = os.getenv('SUPABASE_S3_SECRET_ACCESS_KEY', '')
    AWS_STORAGE_BUCKET_NAME = _SUPABASE_BUCKET
    AWS_S3_ENDPOINT_URL     = f'{_SUPABASE_URL}/storage/v1/s3'
    AWS_S3_REGION_NAME      = 'ap-southeast-1'
    AWS_DEFAULT_ACL         = 'public-read'
    AWS_S3_FILE_OVERWRITE   = False
    AWS_QUERYSTRING_AUTH    = False
    # Generate public URLs via Supabase's public endpoint (not the S3 endpoint)
    AWS_S3_CUSTOM_DOMAIN    = f'{_project_id}.supabase.co/storage/v1/object/public/{_SUPABASE_BUCKET}'
    MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'

# Auth
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

# ── Email ─────────────────────────────────────────────────────────────────────
#
# HOW TO CHOOSE A BACKEND:
#
#   Option A — Gmail SMTP (works right away, good for alpha/testing):
#     Set in Railway env:
#       EMAIL_BACKEND   = django.core.mail.backends.smtp.EmailBackend
#       EMAIL_HOST_USER     = your-gmail@gmail.com
#       EMAIL_HOST_PASSWORD = your-gmail-app-password   ← App Password, not real password
#       DEFAULT_FROM_EMAIL  = your-gmail@gmail.com
#
#   Option B — Resend (production, sends to ANY email):
#     ⚠️  Resend's free plan only delivers to the account owner's email
#         until you verify a custom domain.
#         Steps:
#           1. Go to resend.com → Domains → Add Domain (e.g. rtriplejcustoms.com)
#           2. Add the DNS records Resend gives you
#           3. Set in Railway env:
#                EMAIL_BACKEND      = anymail.backends.resend.EmailBackend
#                RESEND_API_KEY     = re_xxxxxxxxxxxx
#                DEFAULT_FROM_EMAIL = noreply@rtriplejcustoms.com
#
#   Default (fallback if nothing set): Gmail SMTP
#
EMAIL_BACKEND      = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@r3pcr.com')

# Gmail SMTP settings (used when EMAIL_BACKEND is smtp)
EMAIL_HOST          = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT          = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS       = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER     = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
REGISTRATION_EMAIL_DEV_LINKS = os.getenv('REGISTRATION_EMAIL_DEV_LINKS', 'False') == 'True'
LOGIN_OTP_SCREEN_HINT = os.getenv('LOGIN_OTP_SCREEN_HINT', 'False') == 'True'

# Resend API key (used when EMAIL_BACKEND is anymail.backends.resend.EmailBackend)
ANYMAIL = {
    'RESEND_API_KEY': os.getenv('RESEND_API_KEY', ''),
}

# Tesseract OCR
# Windows dev: set TESSERACT_PATH in .env to the full exe path
# Railway / Linux: tesseract is on PATH after apt-get install tesseract-ocr
if _PYTESSERACT_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = os.getenv(
        'TESSERACT_PATH',
        'tesseract'   # works on Linux/Railway; override with TESSERACT_PATH in .env on Windows
    )
