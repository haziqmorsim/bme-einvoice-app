"""Extraction backend: local vision LLM via Ollama (free, offline).

Setup (one time):
    1. Install Ollama:        https://ollama.com/download
    2. Pull a vision model:   ollama pull llama3.2-vision
       (smaller/faster option: ollama pull llava)
    3. Ollama runs a local server at http://localhost:11434 automatically.

Speed & stability notes:
    * We send a downscaled JPEG (OLLAMA_IMAGE_MAX_WIDTH) — big images make local
      vision models very slow and can exhaust memory (causing Ollama to crash).
    * keep_alive keeps the model resident between pages so it isn't reloaded
      every request. warmup() loads it once up front.
    * If the model is still too slow/unstable on your machine, the dispatcher
      automatically falls back to the offline 'rules' engine (see extractor.py),
      or set EXTRACTION_BACKEND=rules to skip the model entirely.

Uses only the Python standard library (urllib) so it adds no dependencies.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from .config import settings
from .extract_common import SYSTEM_PROMPT, build_row, build_user_text, parse_json
from .ocr import Page
from .schema import InvoiceRow


def _post(path: str, payload: dict, timeout: int) -> dict:
    url = settings.ollama_host.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path: str, timeout: int = 5) -> dict:
    url = settings.ollama_host.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def preflight() -> None:
    """Fail early with a helpful message if Ollama isn't ready."""
    try:
        tags = _get("/api/tags")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {settings.ollama_host}. Install it from "
            f"https://ollama.com and make sure it is running (try `ollama list`). "
            f"({exc})"
        )
    installed = {m.get("name", "").split(":")[0] for m in tags.get("models", [])}
    wanted = settings.ollama_model.split(":")[0]
    if not installed:
        raise RuntimeError(
            "No Ollama models are installed yet. Download one first, e.g. "
            f"`ollama pull {settings.ollama_model}` (or a lighter one: `ollama pull llava`)."
        )
    if wanted not in installed:
        raise RuntimeError(
            f"Ollama model '{settings.ollama_model}' is not installed. Run "
            f"`ollama pull {settings.ollama_model}`. Installed: {', '.join(sorted(installed))}"
        )


def warmup() -> None:
    """Load the model into memory once (best-effort) so the first page isn't
    penalised by cold-start time and stays resident via keep_alive."""
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"num_predict": 1, "temperature": 0},
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    _post("/api/chat", payload, timeout=settings.ollama_load_timeout)


def extract_page(page: Page, source_file: str) -> Optional[InvoiceRow]:
    b64, _ = page.to_ai_payload(max_width=settings.ollama_image_max_width)
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",  # force valid JSON output
        "keep_alive": settings.ollama_keep_alive,
        "options": {"temperature": 0, "num_predict": settings.ollama_num_predict},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_text(page),
                "images": [b64],  # Ollama expects bare base64 (no data: prefix)
            },
        ],
    }
    resp = _post("/api/chat", payload, timeout=settings.ollama_timeout)
    raw = (resp.get("message") or {}).get("content", "")
    if not raw:
        return None
    try:
        data = parse_json(raw)
    except ValueError:
        return None
    return build_row(data, page, source_file)