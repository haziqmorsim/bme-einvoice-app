"""PDF -> page images -> OCR text.

Each PDF may be a batch scan containing several invoices, so we work
page-by-page and hand both the rendered image and the OCR text to the
extraction backend downstream (the "hybrid" approach).
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image

from .config import settings

# Honour an explicit tesseract path (mainly for Windows).
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


@dataclass
class Page:
    """A single rendered + OCR'd PDF page."""

    index: int          # 1-based page number
    image: Image.Image  # PIL image (full render)
    text: str           # raw OCR text

    def to_ai_payload(self, max_width: Optional[int] = None) -> tuple[str, str]:
        """Return (base64_png, media_type) downscaled for the AI request.

        Pass a smaller max_width for local models (faster, less memory).
        """
        img = self.image
        max_w = max_width or settings.ai_image_max_width
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def render_pdf(pdf_bytes: bytes) -> list[Image.Image]:
    """Render every page of a PDF to a PIL image."""
    kwargs = {"dpi": settings.render_dpi}
    if settings.poppler_path:
        kwargs["poppler_path"] = settings.poppler_path
    return convert_from_bytes(pdf_bytes, **kwargs)


def ocr_image(image: Image.Image) -> str:
    """Run Tesseract OCR on a single image."""
    return pytesseract.image_to_string(image, config="--psm 6")


def process_pdf(pdf_bytes: bytes) -> list[Page]:
    """Render + OCR an entire PDF, returning one Page per physical page."""
    pages: list[Page] = []
    for i, image in enumerate(render_pdf(pdf_bytes), start=1):
        text = ocr_image(image)
        pages.append(Page(index=i, image=image, text=text))
    return pages