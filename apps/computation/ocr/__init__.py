"""apps.computation.ocr package.

Split from the original ~1400-line ocr.py module into focused submodules
(text_utils / engine / line_items / fields / document). This __init__ re-exports
the public surface so existing ``from apps.computation.ocr import X`` imports
keep working unchanged.
"""
from .text_utils import (
    _w, assess_quality, _clean_text, _clean_number,
    _volume_cbm_from_dimensions, _first_match, _block_after_label,
)
from .engine import (
    _vision_api_call, _preprocess_image_for_ocr, _tesseract_confidence,
    _tesseract_extract, _tesseract_image_to_text, _image_to_text,
    _extract_text_from_pdf_direct, extract_text_from_file,
)
from .line_items import (
    SKIP_WORDS, _normalize_hs_code, _match_item_row,
    _extract_line_items, _extract_hs_anchored_items,
)
from .fields import (
    extract_fields_from_invoice, extract_fields_from_hawb,
    extract_fields_from_packing_list, extract_fields_from_fan,
)
from .document import process_document
