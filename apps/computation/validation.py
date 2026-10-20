"""Finite, bounded decimal inputs shared by estimates and drafts."""
from decimal import Decimal, InvalidOperation


def decimal_value(value, label='Amount', minimum=Decimal('0'), maximum=Decimal('999999999999.99')):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'{label} must be a valid number.') from None
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}.')
    return result
