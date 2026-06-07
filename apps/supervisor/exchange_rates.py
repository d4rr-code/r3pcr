import json
import urllib.request as urequest

from django.utils import timezone

from .models import SystemConfig


SUPPORTED_CURRENCIES = {
    'USD': 'rate_USD',
    'EUR': 'rate_EUR',
    'JPY': 'rate_JPY',
    'HKD': 'rate_HKD',
    'CNY': 'rate_CNY',
    'GBP': 'rate_GBP',
    'SGD': 'rate_SGD',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; R3-PCR/1.0)',
    'Accept': 'application/json',
}

LAST_SUCCESS_KEY = 'exchange_rates_last_success'
LAST_ATTEMPT_KEY = 'exchange_rates_last_attempt'
LAST_ERROR_KEY = 'exchange_rates_last_error'
SOURCE_KEY = 'exchange_rates_source'


def _open_er():
    req = urequest.Request('https://open.er-api.com/v6/latest/PHP', headers=HEADERS)
    with urequest.urlopen(req, timeout=12) as response:
        data = json.loads(response.read().decode())
    if data.get('result') != 'success':
        raise ValueError('open.er-api returned a non-success response')
    return 'open.er-api.com', data.get('rates', {})


def _frankfurter():
    url = 'https://api.frankfurter.app/latest?from=PHP&to=USD,EUR,JPY,HKD,CNY,GBP,SGD'
    req = urequest.Request(url, headers=HEADERS)
    with urequest.urlopen(req, timeout=12) as response:
        return 'frankfurter.app', json.loads(response.read().decode()).get('rates', {})


def fetch_live_exchange_rates():
    """Return PHP-per-currency rates from a live source."""
    last_error = None
    for fetcher in (_open_er, _frankfurter):
        try:
            source, raw_rates = fetcher()
            rates = {}
            for code in SUPPORTED_CURRENCIES:
                raw = raw_rates.get(code)
                if raw:
                    rates[code] = round(1.0 / float(raw), 4)
            if rates:
                return source, rates
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f'All exchange-rate sources failed. Last error: {last_error}')


def save_exchange_rates(rates, source='', user=None):
    saved = {}
    for code, value in rates.items():
        key = SUPPORTED_CURRENCIES.get(code)
        if not key:
            continue
        SystemConfig.objects.update_or_create(
            key=key,
            defaults={'value': str(value), 'updated_by': user},
        )
        saved[code] = value

    if 'USD' in saved:
        SystemConfig.objects.update_or_create(
            key='exchange_rate',
            defaults={'value': str(saved['USD']), 'updated_by': user},
        )

    now_iso = timezone.now().isoformat()
    SystemConfig.objects.update_or_create(
        key=LAST_SUCCESS_KEY,
        defaults={'value': now_iso, 'updated_by': user},
    )
    if source:
        SystemConfig.objects.update_or_create(
            key=SOURCE_KEY,
            defaults={'value': source, 'updated_by': user},
        )
    SystemConfig.objects.filter(key=LAST_ERROR_KEY).delete()
    return saved


def _stored_date(key):
    raw = SystemConfig.get(key, '')
    if not raw:
        return None
    try:
        return timezone.datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def ensure_daily_exchange_rates(user=None, force=False):
    """
    Update live exchange rates once per calendar day.

    Returns a small status dict and never raises on source/network failure, so
    page loads can keep using the most recent saved rates.
    """
    today = timezone.localdate()
    if not force and _stored_date(LAST_SUCCESS_KEY) == today:
        return {'updated': False, 'skipped': True, 'reason': 'already_current'}
    if not force and _stored_date(LAST_ATTEMPT_KEY) == today:
        return {'updated': False, 'skipped': True, 'reason': 'already_attempted'}

    SystemConfig.objects.update_or_create(
        key=LAST_ATTEMPT_KEY,
        defaults={'value': timezone.now().isoformat(), 'updated_by': user},
    )
    try:
        source, rates = fetch_live_exchange_rates()
        saved = save_exchange_rates(rates, source=source, user=user)
        return {'updated': True, 'skipped': False, 'source': source, 'rates': saved}
    except Exception as exc:
        SystemConfig.objects.update_or_create(
            key=LAST_ERROR_KEY,
            defaults={'value': str(exc)[:2000], 'updated_by': user},
        )
        return {'updated': False, 'skipped': False, 'error': str(exc)}
