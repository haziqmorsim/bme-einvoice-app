"""Extraction backend: Anthropic Claude (cloud, paid).

Only imported when EXTRACTION_BACKEND=anthropic, so the `anthropic` package
is optional for users who run the free backends.
"""
from __future__ import annotations

from typing import Optional

from .config import settings
from .extract_common import SYSTEM_PROMPT, build_row, build_user_text, parse_json
from .ocr import Page
from .schema import InvoiceRow

_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic  # imported lazily

        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it to .env, or switch "
                "EXTRACTION_BACKEND to 'ollama' or 'rules' for a free option."
            )
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def preflight() -> None:
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured. Add it to .env, or set "
            "EXTRACTION_BACKEND=ollama (or =rules) to run without a paid API."
        )


def extract_page(page: Page, source_file: str) -> Optional[InvoiceRow]:
    b64, media_type = page.to_ai_payload()
    resp = _get_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": build_user_text(page)},
                ],
            }
        ],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    try:
        data = parse_json(raw)
    except ValueError:
        return None
    return build_row(data, page, source_file)
