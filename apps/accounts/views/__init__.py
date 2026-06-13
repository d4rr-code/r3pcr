"""apps.accounts.views package.

Split from the original ~750-line views.py into cohesive submodules
(common / validators / auth / registration / password / profile). This
__init__ re-exports the public surface so urls.py (`from . import views`) and
external importers (`from apps.accounts.views import _validate_phone_number`
in supervisor) keep working unchanged.

The unwired email-verification flow (send_verification_email / confirm_email /
check_email_verified + helpers) was removed in the split — it had no URL routes
and was never called by register_view.
"""
from .common import redirect_by_role, _send_mail_async
from .validators import (
    _normalize_phone_number, _validate_phone_number, _validate_profile_fields,
    _validate_password_strength, _generate_username,
)
from .auth import login_view, verify_otp_view, resend_otp, logout_view
from .registration import register_view
from .password import forgot_password, forgot_username, reset_password
from .profile import account_settings
