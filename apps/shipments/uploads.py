"""Shared server-side validation for every shipment upload entry point."""
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from PIL import Image, UnidentifiedImageError


def validate_document(file):
    maximum = getattr(settings, 'SHIPMENT_UPLOAD_MAX_BYTES', 10 * 1024 * 1024)
    if not file.size or file.size > maximum:
        raise ValidationError('Documents must be nonempty and no larger than 10 MB.')
    suffix = Path(file.name).suffix.lower()
    try:
        file.seek(0)
        if suffix == '.pdf':
            if not file.read(5) == b'%PDF-':
                raise ValidationError('Upload a valid PDF, JPEG, or PNG document.')
        elif suffix in {'.jpg', '.jpeg', '.png'}:
            with Image.open(file) as image:
                expected = 'PNG' if suffix == '.png' else 'JPEG'
                if image.format != expected:
                    raise ValidationError('The image content does not match its file extension.')
                image.verify()
        else:
            raise ValidationError('Only PDF, JPEG, and PNG shipment documents are supported.')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError('The uploaded image could not be validated.')
    finally:
        file.seek(0)


def validate_document_uploads(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method == 'POST':
            try:
                for _, files in request.FILES.lists():
                    for file in files:
                        validate_document(file)
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
                return redirect(request.path)
        return view(request, *args, **kwargs)
    return wrapped
