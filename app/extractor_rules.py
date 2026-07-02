"""Extraction backend: pure offline rules (no API, no model, no extra install).

Parses the Tesseract OCR text with regex + keyword heuristics. This is the
zero-dependency fallback: it works completely offline but is less accurate than
the LLM backends, especially for handwritten project numbers and unusual
layouts. Rows are flagged with low/medium confidence so you know what to check.
"""
from __future__ import annotations

import re
from typing import Optional

from .extract_common import build_row
from .ocr import Page
from .schema import InvoiceRow

# A money token like 1,336.43  or  121.99  or  -338,013.35
_MONEY = r"-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|-?\d+\.\d{1,2}"
_MONEY_RE = re.compile(_MONEY)

# Known project / PO number shapes seen on these invoices.
_PROJECT_RE = re.compile(
    r"\b((?:ST|MP|PB|PO|WO|PI|PB)\s?\d{3,6}[A-Z]?(?:\s*[/\-]\s*(?:ST|MP|PB|PO|WO)?\d{2,6}[A-Z]?)?)",
    re.IGNORECASE,
)

# Vendor letterhead fingerprints (checked in order).
_VENDORS = [
    (re.compile(r"CNC\s+FREIGHT", re.I), "CNC"),
    (re.compile(r"FM\s+GLOBAL", re.I), "FM Global"),
    (re.compile(r"UNIMAJU.*GLOBAL|UNIMAJU\s+GLOBAL", re.I), "Unimaju Global"),
    (re.compile(r"UNIMAJU", re.I), "Unimaju"),
    (re.compile(r"\bCLS\b", re.I), "CLS"),
    (re.compile(r"COMPLETE", re.I), "Complete"),
]

# Charge bucket keywords. Order matters: more specific buckets first so a line
# like "STORAGE CHARGES" doesn't get caught by the generic local-charges net.
_BUCKET_KEYWORDS = [
    ("port_storage", [
        "storage", "demurrage", "detention", "warehous", "port rent", "ground rent",
    ]),
    ("transport_charges", [
        "transport", "haulage", "trucking", "truck", "lorry", "cartage",
        "delivery", "inland", "drayage", "door",
    ]),
    ("reimbursement", [
        "reimburs", "disburs", "out of pocket", "out-of-pocket", "duty", "on behalf",
    ]),
    ("freight", [
        "freight", "ocean", "sea fr", "seafreight", "air fr", "barge", "baf", "caf",
        "carriage", "shipping line",
    ]),
    ("local_charges", [
        "thc", "terminal handling", "terminal", "document", "doc fee", "d/o", "do fee",
        "delivery order", "handling", "customs", "clearance", "forwarding", "agency",
        "b/l", "bl fee", "bill of lading", "seal", "edi", "dangerous", "dg ",
        "port due", "wharf", "lift", "survey", "permit", "declaration", "local charge",
    ]),
]

_TOTAL_RE = re.compile(
    r"(?:GRAND\s+TOTAL|TOTAL\s*\(?\s*INCLUSIVE|TOTAL\s+INCL|TOTAL\s+AMOUNT|"
    r"NETT?\s+TOTAL|TOTAL\s+PAYABLE)\b[^0-9\-]*(" + _MONEY + r")",
    re.I,
)
_ANY_TOTAL_RE = re.compile(r"\bTOTAL\b[^0-9\-]*(" + _MONEY + r")", re.I)
_INVOICE_RE = re.compile(r"INVOICE\s*N(?:O|UMBER)\b[^A-Za-z0-9\n]{0,5}([A-Z0-9][A-Z0-9/\-]{3,})", re.I)
_TYPE_CONTAINER_RE = re.compile(r"\b(\d\s*[xX]\s*\d{2}\s?'?\s?(?:HC|HQ|FR|GP|RF|OT|DG)\w*)", re.I)


def _to_float(token: str) -> float:
    try:
        return float(token.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def _detect_vendor(text: str) -> str:
    for rx, name in _VENDORS:
        if rx.search(text):
            return name
    # Fallback: first non-empty line that looks like a company.
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 4 and re.search(r"(SDN\s+BHD|LOGISTICS|FREIGHT|SHIPPING|FORWARD)", line, re.I):
            return re.split(r"\bSDN\b", line, flags=re.I)[0].strip().title()
    return ""


def _detect_project(text: str) -> str:
    head = "\n".join(text.splitlines()[:12])  # project no is usually near the top
    m = _PROJECT_RE.search(head) or _PROJECT_RE.search(text)
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(1)).upper().replace("/", "/ ")


def _detect_invoice_no(text: str) -> str:
    m = _INVOICE_RE.search(text)
    return m.group(1).strip() if m else ""


def _detect_type(text: str) -> str:
    m = _TYPE_CONTAINER_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).replace(" '", "'").upper().replace("X", "x")
    upper = text.upper()
    if "BARGE" in upper:
        return "Barge"
    if re.search(r"\bFCL\b", upper):
        return "FCL"
    if re.search(r"\bLCL\b", upper):
        return "LCL"
    if re.search(r"\bAIR\s*FREIGHT|\bAWB\b", upper):
        return "Air"
    return ""


def _detect_pod(text: str) -> str:
    for label in (
        r"FINAL\s+DESTINATION", r"P\.?\s*O\.?\s*D", r"PORT\s+OF\s+DISCHARGE",
        r"DESTINATION", r"DISCHARGE\s+PORT",
    ):
        m = re.search(label + r"\s*[:\-]?\s*([A-Za-z][A-Za-z ,./'-]{2,40})", text, re.I)
        if m:
            val = m.group(1).strip(" .:-")
            val = re.split(r"\s{2,}|JOB|OBL|HBL|VESSEL", val)[0].strip()
            if len(val) > 2:
                return val
    return ""


def _categorize_charges(text: str) -> dict:
    """Walk each line, find its amount, and add it to the matching bucket."""
    buckets = {
        "freight": 0.0, "local_charges": 0.0, "port_storage": 0.0,
        "transport_charges": 0.0, "reimbursement": 0.0,
    }
    matched_any = False
    for line in text.splitlines():
        low = line.lower()
        # Skip total / subtotal / tax summary lines — handled separately.
        if re.search(r"\btotal\b|\bsub\s*total\b|\btax\s+summary\b|\bsst\b", low):
            continue
        amounts = _MONEY_RE.findall(line)
        if not amounts:
            continue
        amount = _to_float(amounts[-1])  # right-most number = amount incl. tax
        if amount == 0.0:
            continue
        for bucket, keywords in _BUCKET_KEYWORDS:
            if any(k in low for k in keywords):
                buckets[bucket] += amount
                matched_any = True
                break
    return buckets if matched_any else buckets  # always return; may be all zero


def _detect_total(text: str) -> float:
    m = _TOTAL_RE.search(text)
    if m:
        return _to_float(m.group(1))
    # Fall back to the largest amount on any line mentioning TOTAL.
    candidates = [_to_float(x) for x in _ANY_TOTAL_RE.findall(text)]
    return max(candidates) if candidates else 0.0


def _looks_like_invoice(text: str) -> bool:
    has_invoice_word = re.search(r"INVOICE", text, re.I) is not None
    has_amount = _MONEY_RE.search(text) is not None
    return has_invoice_word and has_amount


def preflight() -> None:
    # Nothing external is required for the offline rules backend.
    return None


def extract_page(page: Page, source_file: str) -> Optional[InvoiceRow]:
    text = page.text or ""
    if not _looks_like_invoice(text):
        return None

    buckets = _categorize_charges(text)
    printed_total = _detect_total(text)
    sum_buckets = round(sum(buckets.values()), 2)

    # Confidence: high-ish only if printed total agrees with our bucket sum.
    if printed_total and abs(printed_total - sum_buckets) < 0.05 and sum_buckets:
        confidence = "medium"
    else:
        confidence = "low"

    data = {
        "is_invoice": True,
        "project_no": _detect_project(text),
        "vendor": _detect_vendor(text),
        "invoice_no": _detect_invoice_no(text),
        "pod": _detect_pod(text),
        "type": _detect_type(text),
        "freight": buckets["freight"],
        "local_charges": buckets["local_charges"],
        "port_storage": buckets["port_storage"],
        "transport_charges": buckets["transport_charges"],
        "reimbursement": buckets["reimbursement"],
        "total": printed_total or sum_buckets,
        "confidence": confidence,
    }
    return build_row(data, page, source_file)
