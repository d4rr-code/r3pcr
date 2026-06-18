"""apps.declarant.views package.

Split from the original ~1900-line views.py into cohesive submodules
(common / pages / workflow / uploads). This __init__ re-exports the public
surface so urls.py (`from . import views`) and external importers
(`from apps.declarant.views import declarant_required`,
`from apps.declarant.views import _CHAPTER_TITLES`) keep working unchanged.
"""
from .common import declarant_required, _CHAPTER_TITLES
from .pages import (
    dashboard, system_reference, report_issue,
    system_parameters, system_fees, system_wmcda,
    tariff_book, tariff_book_section, tariff_book_chapter,
)
from .workflow import (
    shipment_preview, queue_manager, claim_shipment,
    run_ocr_sync, ocr_status, process_shipment,
    update_shipping_mode, update_tracking_fields, proceed_to_lodgement, update_status,
    payment_confirmation,
)
from .uploads import (
    upload_sad, save_fan_assessment, upload_supporting_document,
    upload_receipt, flag_deficiency, save_ocr_items,
)
