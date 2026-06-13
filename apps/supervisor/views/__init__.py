"""apps.supervisor.views package.

Split from the original ~2900-line views.py into cohesive submodules
(common / analytics / analytics_sections / users / memos / config / shipments).
This __init__ re-exports the public surface so urls.py (`from . import views`)
and external importers (`from apps.supervisor.views import _HS_SECTIONS,
_chapter_num` in declarant) keep working unchanged.
"""
from .common import supervisor_required, _HS_SECTIONS, _chapter_num
from .analytics import dashboard, analytics, analytics_export, analytics_status_counts
from .users import (
    user_management, approve_registration, reject_registration,
    add_user, edit_user, toggle_user,
)
from .shipments import (
    shipment_detail, reset_shipment, update_shipment_status, delete_shipment,
    manage_feedbacks, approve_feedback, reject_feedback,
    issue_reports, update_issue_report, shipment_records,
    consignee_list, consignee_detail, declarant_list, declarant_detail,
)
from .memos import list_memos, create_memo, delete_memo, toggle_memo
from .config import (
    config_home, config_global, config_fees, fetch_exchange_rates, config_wmcda,
    config_hscodes_sections, upload_tariff_schedule,
    config_hscodes_section, config_hscodes_chapter,
)
