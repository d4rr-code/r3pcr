"""Field-validation helpers for registration / profile forms, plus the
auto-username generator. Pure functions (no request handling)."""
import re
from ..models import User


def _normalize_phone_number(phone):
    """Reduce any PH mobile entry to the canonical 11-digit 09xxxxxxxxx form.
    Accepts +63/63 prefixes and stray spaces/dashes; returns '' if unusable."""
    if not phone:
        return ''
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('63') and len(digits) == 12:
        digits = '0' + digits[2:]      # 639xxxxxxxxx -> 09xxxxxxxxx
    return digits


def _validate_phone_number(phone):
    if not phone:
        return None
    digits = _normalize_phone_number(phone)
    if not re.fullmatch(r'09\d{9}', digits):
        return 'Enter a valid PH mobile number in the format 09xxxxxxxxx (11 digits).'
    return None


def _validate_profile_fields(first_name, last_name, email, phone='', company=''):
    """Return a list of error strings; empty list means all fields are valid."""
    errors = []

    # First name
    if not first_name:
        errors.append('First name is required.')
    elif len(first_name) < 2:
        errors.append('First name must be at least 2 characters.')
    elif len(first_name) > 30:
        errors.append('First name cannot exceed 30 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", first_name):
        errors.append('First name may only contain letters, spaces, hyphens, and apostrophes.')

    # Last name
    if not last_name:
        errors.append('Last name is required.')
    elif len(last_name) < 2:
        errors.append('Last name must be at least 2 characters.')
    elif len(last_name) > 30:
        errors.append('Last name cannot exceed 30 characters.')
    elif not re.match(r"^[a-zA-ZÀ-ÿ\s\-'.]+$", last_name):
        errors.append('Last name may only contain letters, spaces, hyphens, and apostrophes.')

    # Email — use Django's validator for robust format checking
    if not email:
        errors.append('Email address is required.')
    else:
        from django.core.validators import validate_email as _dj_validate_email
        from django.core.exceptions import ValidationError as _DjVErr
        try:
            _dj_validate_email(email)
        except _DjVErr:
            errors.append('Enter a valid email address (e.g. juandelacruz@gmail.com).')

    # Phone (optional) — Philippine format: 09XX-XXX-XXXX or +639XX-XXX-XXXX
    if phone:
        phone_error = _validate_phone_number(phone)
        if phone_error:
            errors.append(phone_error)

    # Company (optional)
    if company and len(company) > 100:
        errors.append('Company name cannot exceed 100 characters.')

    return errors


def _validate_password_strength(password):
    """Enforce the character-class rules shown on the registration form, plus
    Django's configured validators (common/numeric/similarity) as a backstop.
    Returns a list of error strings."""
    errors = []
    if len(password) < 8:
        errors.append('Password must be at least 8 characters.')
    if not re.search(r'[A-Z]', password):
        errors.append('Password must include at least one uppercase letter.')
    if not re.search(r'[a-z]', password):
        errors.append('Password must include at least one lowercase letter.')
    if not re.search(r'[0-9]', password):
        errors.append('Password must include at least one number.')
    if not re.search(r'[^A-Za-z0-9]', password):
        errors.append('Password must include at least one special character.')

    if not errors:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as _DjVErr
        try:
            validate_password(password)
        except _DjVErr as ve:
            errors.extend(ve.messages)
    return errors


def _generate_username(first_name, last_name):
    """Auto-generate a compact unique username: first initial + last name,
    lowercased and length-capped. e.g. 'Juan Dela Cruz' -> 'jdelacruz'.
    Falls back to fuller name parts when the result would be too short."""
    first = re.sub(r'[^a-z0-9]', '', (first_name or '').lower())
    last  = re.sub(r'[^a-z0-9]', '', (last_name or '').lower())

    base = (first[:1] + last) if (first and last) else (first or last)
    base = base[:15]                       # not too long
    if len(base) < 5:                      # not too short
        base = (first + last)[:15] or base
    if not base:
        base = 'user'

    username = base
    counter  = 2
    while User.objects.filter(username=username).exists():
        username = f'{base}{counter}'
        counter += 1
    return username
