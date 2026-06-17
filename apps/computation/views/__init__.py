"""apps.computation.views package.

Split from the original ~2450-line views.py into cohesive submodules
(ecdt / ocr_views / drafts / compute / downloads / hs_codes / advisory). This
__init__ re-exports the public surface so urls.py (`from . import views`) and
external importers keep working unchanged — including compute_ecdt (tests),
compute_wmcda (consignee/supervisor/seed) and the HS-code helpers (declarant).
"""
from .ecdt import compute_ecdt
from .ocr_views import ocr_extract, ocr_extract_all
from .drafts import draft_item, delete_draft_item, draft_globals
from .compute import compute_shipment
from .downloads import download_computation
from .hs_codes import (
    hs_suggestions, confirm_hs_code, hs_code_suggest,
    update_line_item_hs, hs_code_search,
    find_hs_by_document_code, extract_document_hs_codes, suggest_hs_codes,
)
from .advisory import shipping_advisory, save_declarant_advisory, compute_wmcda
