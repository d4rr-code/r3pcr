"""OCR text extraction: image preprocessing, Tesseract, Google Vision, PDF -> text."""
import base64
import io
import logging
import os
import re

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pdf2image import convert_from_path
import requests as http_requests

from .text_utils import assess_quality

logger = logging.getLogger('r3pcr.ocr')

def _vision_api_call(api_key, image_bytes):
    """Send raw image bytes to Google Vision API. Returns extracted text string."""
    url = f'https://vision.googleapis.com/v1/images:annotate?key={api_key}'
    payload = {
        'requests': [{
            'image': {'content': base64.b64encode(image_bytes).decode('utf-8')},
            'features': [{'type': 'DOCUMENT_TEXT_DETECTION'}],
        }]
    }
    try:
        resp = http_requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data['responses'][0].get('fullTextAnnotation', {}).get('text', '')
        else:
            logger.warning("Vision API error %s: %s", resp.status_code, resp.text[:300])
            return ''
    except Exception as e:
        logger.warning("Vision API request error: %s", e)
        return ''


def _preprocess_image_for_ocr(image):
    """
    Normalize scanned document images before Tesseract.
    Upscaling, grayscale, autocontrast, light sharpening and thresholding help
    the common phone-scan cases: faint text, shadows, and low resolution.
    """
    image = image.convert('RGB')
    max_side = max(image.size)
    if max_side < 2200:
        scale = 2200 / max_side
        image = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.Resampling.LANCZOS,
        )
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(1.6)
    gray = gray.filter(ImageFilter.SHARPEN)
    threshold = 180
    return gray.point(lambda px: 255 if px > threshold else 0, mode='1')


def _tesseract_confidence(image, config):
    try:
        data = pytesseract.image_to_data(
            image,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
        confs = []
        for value in data.get('conf', []):
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            if score >= 0:
                confs.append(score)
        if confs:
            return sum(confs) / len(confs)
    except Exception as e:
        logger.warning("Tesseract confidence check failed: %s", e)
    return 0


def _tesseract_extract(image, config):
    """
    Single Tesseract call that returns (text, confidence) together using
    image_to_data — avoids the previous pattern of two separate calls
    (image_to_string + image_to_data) per variant.
    """
    try:
        data = pytesseract.image_to_data(
            image, config=config, output_type=pytesseract.Output.DICT
        )
        words  = data.get('text', [])
        confs  = data.get('conf', [])
        block_nums = data.get('block_num', [0] * len(words))
        par_nums   = data.get('par_num', [0] * len(words))
        line_nums  = data.get('line_num', [0] * len(words))
        line_order, line_parts, conf_scores = [], {}, []
        for i, w in enumerate(words):
            clean_word = (w or '').strip()
            if not clean_word:
                continue
            try:
                score = float(confs[i])
            except (TypeError, ValueError, IndexError):
                score = -1
            if score >= 0:
                conf_scores.append(score)
            line_key = (block_nums[i], par_nums[i], line_nums[i])
            if line_key not in line_parts:
                line_parts[line_key] = []
                line_order.append(line_key)
            line_parts[line_key].append(clean_word)
        text = '\n'.join(
            ' '.join(line_parts[key]).strip()
            for key in line_order
            if line_parts.get(key)
        )
        confidence = sum(conf_scores) / len(conf_scores) if conf_scores else 0
        return text, confidence
    except Exception as e:
        logger.warning("Tesseract extract failed (%s): %s", config, e)
        return '', 0


def _tesseract_image_to_text(image):
    """
    Optimised Tesseract path:
      - Try PSM 6 (uniform block — best for structured invoices) first.
      - If quality is not poor, accept and return immediately.
      - Only fall back to PSM 3 (auto) when PSM 6 yields poor output.
    This replaces the previous 4-variant trial that ran 8 Tesseract calls
    per page; now we run at most 2 calls per page in the worst case.
    """
    original  = image.convert('RGB')
    processed = _preprocess_image_for_ocr(original)

    # Primary attempt — PSM 6 on preprocessed image
    text, confidence = _tesseract_extract(processed, '--psm 6')
    useful_chars = len(re.findall(r'[A-Za-z0-9]', text or ''))
    logger.debug("Tesseract psm6: %s chars, conf %.1f, useful %s", len(text), confidence, useful_chars)

    quality = assess_quality(text)
    if quality != 'poor':
        # Good enough — skip fallback variants
        try:
            processed.close()
            original.close()
        except Exception as e:
            logger.debug('Image cleanup failed: %s', e)
        return text

    # Fallback — PSM 3 (fully automatic layout) on original (unprocessed)
    logger.debug("psm6 quality poor — falling back to psm3 on original image")
    fb_text, fb_conf = _tesseract_extract(original, '--psm 3')
    fb_useful = len(re.findall(r'[A-Za-z0-9]', fb_text or ''))
    logger.debug("Tesseract psm3 fallback: %s chars, conf %.1f, useful %s", len(fb_text), fb_conf, fb_useful)

    # Pick whichever produced more readable content
    result = fb_text if fb_useful > useful_chars else text
    try:
        processed.close()
        original.close()
    except Exception as e:
        logger.debug('Image cleanup failed: %s', e)
    return result


def _image_to_text(image, api_key=''):
    if api_key:
        try:
            buf = io.BytesIO()
            image.convert('RGB').save(buf, format='JPEG', quality=92)
            vision_text = _vision_api_call(api_key, buf.getvalue())
            buf.close()
            if assess_quality(vision_text) != 'poor':
                logger.debug("Vision accepted: %s chars", len(vision_text))
                return vision_text
            logger.debug("Vision output poor/empty; falling back to Tesseract")
        except Exception as e:
            logger.warning("Vision path failed; falling back to Tesseract: %s", e)
    return _tesseract_image_to_text(image)


def _extract_text_from_pdf_direct(file_path):
    """
    Fast path: extract embedded text directly from a text-based PDF using pypdf.
    Returns the extracted text, or '' if pypdf is unavailable or the PDF is image-only.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(file_path)
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ''
            pages_text.append(text)
        return '\n'.join(pages_text).strip()
    except Exception as e:
        logger.warning("pypdf direct extraction failed: %s", e)
        return ''


def extract_text_from_file(file_path):
    """
    Extract text from a document file.

    Strategy (in order):
    1. For PDFs: try pypdf direct text extraction first (fast, lossless for digital PDFs).
       If >= 80 chars are extracted, use that result — no image conversion needed.
    2. Fall back to image-based OCR:
       - Google Vision API if GOOGLE_VISION_API_KEY is set
       - Tesseract otherwise (page-by-page at 150 DPI, --psm 3 for complex layouts)
    3. For image files: Vision API or Tesseract directly.
    """
    import gc
    from pdf2image import pdfinfo_from_path

    ext = os.path.splitext(file_path)[1].lower()
    api_key = os.getenv('GOOGLE_VISION_API_KEY', '')

    try:
        if ext == '.pdf':
            # ── Fast path: direct text extraction from digital PDFs ──────────
            direct_text = _extract_text_from_pdf_direct(file_path)
            if assess_quality(direct_text) != 'poor':
                logger.debug("pypdf direct: %s chars extracted", len(direct_text))
                return direct_text

            # ── Image-based OCR path (for scanned PDFs) ──────────────────────
            logger.debug("pypdf got %s chars — falling back to image OCR", len(direct_text.strip()))
            poppler_path = os.getenv('POPPLER_PATH') or None

            # Determine page count without loading all pages into memory
            try:
                info = pdfinfo_from_path(file_path, poppler_path=poppler_path)
                num_pages = int(info.get('Pages', 1))
            except Exception:
                num_pages = 10  # safe fallback

            full_text = ''
            for page_num in range(1, num_pages + 1):
                try:
                    images = convert_from_path(
                        file_path,
                        dpi=200,
                        first_page=page_num,
                        last_page=page_num,
                        poppler_path=poppler_path,
                    )
                    if not images:
                        break
                    image = images[0]

                    # _image_to_text: Vision API → if poor/failed → Tesseract
                    page_text = _image_to_text(image, api_key)
                    logger.debug("page %s: %s chars", page_num, len(page_text))
                    full_text += page_text + '\n'

                    # Explicitly release page memory before next iteration
                    image.close()
                    del image, images
                    gc.collect()

                except Exception as page_err:
                    logger.warning("page %s error: %s", page_num, page_err)
                    break

            return full_text

        elif ext in ['.jpg', '.jpeg', '.png']:
            image = Image.open(file_path)
            text = _image_to_text(image, api_key)
            image.close()
            return text
        else:
            return ''

    except Exception as e:
        logger.warning("extraction error: %s", e)
        return ''


