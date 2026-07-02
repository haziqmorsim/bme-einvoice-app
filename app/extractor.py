"""Extraction dispatcher.

Selects an extraction backend based on EXTRACTION_BACKEND and exposes a single
`extract_pages()` used by the rest of the app, plus `preflight()` / `health()`.

Resilience for the LLM backends (ollama/anthropic):
  * warmup() is called once so the model loads before the first real page.
  * If a page fails (timeout / connection dropped / crash) and
    LLM_FALLBACK_TO_RULES is on, that page is parsed with the offline rules
    engine instead of returning nothing.
  * After LLM_FALLBACK_MAX_FAILURES consecutive LLM failures we stop calling the
    LLM for the rest of the document and use the fast rules engine — this avoids
    "waited a very long time and got nothing" when the model has crashed.
"""
from __future__ import annotations

from typing import Callable, Optional

from .config import settings
from .ocr import Page
from .schema import ExtractionResult, InvoiceRow

_BACKENDS = {"ollama", "rules", "anthropic"}


def _load_backend():
    name = (settings.extraction_backend or "").strip().lower()
    if name not in _BACKENDS:
        raise RuntimeError(
            f"Unknown EXTRACTION_BACKEND '{settings.extraction_backend}'. "
            f"Choose one of: {', '.join(sorted(_BACKENDS))}."
        )
    if name == "ollama":
        from . import extractor_ollama as backend
    elif name == "rules":
        from . import extractor_rules as backend
    else:
        from . import extractor_anthropic as backend
    return name, backend


def preflight() -> None:
    """Raise RuntimeError with a helpful message if the backend isn't ready."""
    _, backend = _load_backend()
    backend.preflight()


def health() -> dict:
    name = (settings.extraction_backend or "").strip().lower()
    info = {"backend": name}
    if name == "ollama":
        info["model"] = settings.ollama_model
        info["host"] = settings.ollama_host
    elif name == "anthropic":
        info["model"] = settings.anthropic_model
        info["key_configured"] = bool(settings.anthropic_api_key)
    elif name == "rules":
        info["model"] = "offline regex/heuristics"
    try:
        preflight()
        info["ready"] = True
        info["message"] = "ready"
    except Exception as exc:  # noqa: BLE001
        info["ready"] = False
        info["message"] = str(exc)
    return info


def extract_page(page: Page, source_file: str):
    """Single-page extraction with no fallback (used for tests/scripts)."""
    _, backend = _load_backend()
    return backend.extract_page(page, source_file)


def _rules_backend():
    from . import extractor_rules as rules
    return rules


def extract_pages(
    pages: list[Page],
    source_file: str,
    progress: Optional[Callable[[], None]] = None,
) -> ExtractionResult:
    """Run the configured backend over every page with resilient fallback.

    `progress` (if given) is called once per page so the UI can update.
    """
    name, primary = _load_backend()
    is_llm = name in ("ollama", "anthropic")
    result = ExtractionResult()

    rules = _rules_backend() if (is_llm and settings.llm_fallback_to_rules) else None

    # Warm the model up once (best-effort). If it fails, the per-page loop will
    # handle it and, if enabled, fall back to rules.
    if is_llm:
        warm = getattr(primary, "warmup", None)
        if callable(warm):
            try:
                warm()
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"{source_file}: model warm-up failed ({exc}).")

    consecutive_failures = 0
    llm_disabled = False

    for page in pages:
        row: Optional[InvoiceRow] = None

        if is_llm and not llm_disabled:
            try:
                row = primary.extract_page(page, source_file)
                consecutive_failures = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_failures += 1
                result.warnings.append(f"{source_file} p{page.index}: {exc}")
                # Fall back to rules for this page.
                if rules is not None:
                    try:
                        row = rules.extract_page(page, source_file)
                        if row is not None:
                            row.confidence = "low"
                    except Exception:  # noqa: BLE001
                        row = None
                # Trip the circuit breaker after repeated failures.
                if consecutive_failures >= settings.llm_fallback_max_failures:
                    llm_disabled = True
                    if rules is not None:
                        result.warnings.append(
                            f"{source_file}: the AI model kept failing, so the remaining "
                            f"pages were parsed with the offline rules engine."
                        )
                    else:
                        result.warnings.append(
                            f"{source_file}: stopped calling the AI model after repeated "
                            f"failures (enable LLM_FALLBACK_TO_RULES for offline parsing)."
                        )
        elif is_llm and llm_disabled:
            # Circuit breaker tripped: use rules for the rest (or skip if disabled).
            if rules is not None:
                try:
                    row = rules.extract_page(page, source_file)
                    if row is not None:
                        row.confidence = "low"
                except Exception:  # noqa: BLE001
                    row = None
        else:
            # Rules is the primary backend.
            try:
                row = primary.extract_page(page, source_file)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"{source_file} p{page.index}: {exc}")
                row = None

        if row is not None:
            result.rows.append(row)
        if progress is not None:
            progress()

    return result