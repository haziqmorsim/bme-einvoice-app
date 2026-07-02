"""PDF -> page images -> OCR text.

Each PDF may be a batch scan containing several invoices, so we work
page-by-page and hand both the rendered image and the OCR text to the
extraction backend downstream (the "hybrid" approach).

Memory note: on small hosts (e.g. a 512 MB container) rendering every page of
a long PDF at once will exhaust RAM. We therefore render ONE page at a time and
release each image as soon as it has been OCR'd. When the caller does not need
the pixel data (the offline "rules" backend only reads OCR text), we drop the
image entirely and render in grayscale, which cuts peak memory dramatically.
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

import pytesseract
from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

from .config import settings

# Honour an explicit tesseract path (mainly for Windows).
if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


@dataclass
class Page:
    """A single rendered + OCR'd PDF page."""

    index: int                   # 1-based page number
    image: Optional[Image.Image] # PIL image (None once released to save memory)
    text: str                    # raw OCR text

    def to_ai_payload(self, max_width: Optional[int] = None) -> tuple[str, str]:
        """Return (base64_jpeg, media_type) downscaled for the AI request.

        Pass a smaller max_width for local models (faster, less memory).
        Only used by the LLM backends, which keep the image around.
        """
        if self.image is None:
            raise RuntimeError("Page image was released; cannot build AI payload.")
        img = self.image
        max_w = max_width or settings.ai_image_max_width
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def _poppler_kwargs() -> dict:
    """Extra kwargs to point pdf2image at poppler (explicit path if configured)."""
    if settings.poppler_path:
        return {"poppler_path": settings.poppler_path}
    return {}


def _render_page(pdf_path: str, page_no: int, grayscale: bool) -> Image.Image:
    """Render a single page to a PIL image (low memory: one page at a time)."""
    images = convert_from_path(
        pdf_path,
        dpi=settings.render_dpi,
        first_page=page_no,
        last_page=page_no,
        grayscale=grayscale,
        thread_count=1,
        **_poppler_kwargs(),
    )
    return images[0]


def ocr_image(image: Image.Image) -> str:
    """Run Tesseract OCR on a single image."""
    return pytesseract.image_to_string(image, config="--psm 6")


def process_pdf(pdf_bytes: bytes, keep_images: bool = False) -> list[Page]:
    """Render + OCR an entire PDF, returning one Page per physical page.

    Renders page-by-page to keep peak memory to roughly a single page. When
    ``keep_images`` is False (the offline rules backend) each image is rendered
    in grayscale and released immediately after OCR, so long PDFs fit in small
    containers. LLM backends pass ``keep_images=True`` to retain colour images
    for the vision model.
    """
    pages: list[Page] = []

    # Write the PDF to a temp file once, then render individual pages from it
    # (avoids re-buffering the whole PDF on every page).
    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pdf_bytes)

        info = pdfinfo_from_path(pdf_path, **_poppler_kwargs())
        total = int(info.get("Pages", 0))

        for i in range(1, total + 1):
            image = _render_page(pdf_path, i, grayscale=not keep_images)
            text = ocr_image(image)
            if keep_images:
                pages.append(Page(index=i, image=image, text=text))
            else:
                pages.append(Page(index=i, image=None, text=text))
                image.close()  # free the pixel buffer right away
    finally:
        try:
            os.remove(pdf_path)
        except OSError:
            pass

    return pages