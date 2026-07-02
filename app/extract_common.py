"""Shared pieces used by the extraction backends.

- SYSTEM_PROMPT / USER_INSTRUCTION: the contract given to LLM backends.
- parse_json(): tolerant JSON extraction from model output.
- _num(): turn "RM 1,336.43" / "-338,013.35" / "" into a float.
- build_row(): turn a parsed dict into a validated InvoiceRow.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .ocr import Page
from .schema import InvoiceRow

SYSTEM_PROMPT = """You are an expert accounts-payable clerk for a Malaysian \
manufacturer that imports/exports goods through freight forwarders. You read \
scanned freight-forwarder invoices (CNC Freight, FM Global Logistics, Unimaju, \
CLS, Complete, etc.) and turn them into structured accounting rows.

You are given, for ONE scanned page:
  1. The raw OCR text (may contain errors).
  2. The page image (authoritative — trust it over OCR, and use it to read
     handwriting such as the project / PO number written at the top).

TASK
Decide if this page is the FIRST page of an invoice. A page is NOT a new
invoice if it is a blank page, a terms-and-conditions page, a packing list, a
delivery order, or a continuation of the previous invoice. If it is not a new
invoice, return {"is_invoice": false}.

If it IS an invoice, extract these fields:

- project_no: The customer's project / PO number. Often HANDWRITTEN at the top
  of the page or inside the "INVOICE CERTIFICATION / PROJ NO / PO NO" stamp box.
  Examples: "PO25-1191", "ST6754", "MP0742", "PB0902". Keep slashes if several
  are written (e.g. "ST6752/ ST6821"). If none is visible, use "".
- vendor: The forwarder company that ISSUED the invoice (the logo / letterhead
  at the top), normalised short name. Map letterheads to:
  "CNC Freight Services"->"CNC", "FM Global Logistics"->"FM Global",
  "Unimaju"->"Unimaju" (or "Unimaju Global" if the letterhead says Global),
  "Complete"->"Complete", "CLS"->"CLS". Otherwise use the company's short name.
- invoice_no: The invoice number (field labelled INVOICE NO / INVOICE NUMBER).
- pod: Port of Discharge / Final Destination / P.O.D. For imports this is the
  origin->destination as written (e.g. "From Wakayama", "From Kobe"); for
  exports it is the destination port (e.g. "Colon", "Surabaya", "Sibu Sarawak").
  Copy what the invoice shows.
- type: Shipment type. Use "LCL", "FCL", "Barge", "Air", or the container
  configuration if shown (e.g. "1 x 40'HC", "2 x 40'FR", "1 x 40'HC DG").

Then read EVERY charge line item and assign its amount to exactly ONE bucket.
Sum amounts that fall in the same bucket. Use amounts INCLUSIVE of tax (the
right-most "AMOUNT INCL. TAX" column) so the buckets add up to the total.

Buckets:
- freight: ocean/sea freight, air freight, barge freight, freight surcharges,
  BAF/CAF, the main carriage cost.
- local_charges: terminal handling (THC), documentation/doc fee, customs/forwarding
  agency fees, handling, B/L fee, seal, DG surcharge, EDI, port dues, and any
  local destination/origin charges that are NOT storage or inland transport.
- port_storage: storage charges, demurrage, detention, port rent/warehousing
  while goods sit at the port.
- transport_charges: inland haulage, trucking, delivery, cartage, transport to/from
  the port or door.
- reimbursement: items the forwarder paid on your behalf and is recharging —
  duty/tax reimbursement, disbursement, out-of-pocket, "reimbursable".

- total: the invoice grand total INCLUSIVE of tax. Should equal the sum of the
  five buckets; if the printed total differs, trust the printed total and still
  fill the buckets as best you can.
- confidence: "high" if the image is clear and numbers are certain, "medium" if
  some inference was needed, "low" if the scan is poor.

OUTPUT
Return ONLY a JSON object, no prose, no markdown fences:
{"is_invoice": true,
 "project_no": "...", "vendor": "...", "invoice_no": "...",
 "pod": "...", "type": "...",
 "freight": 0, "local_charges": 0, "port_storage": 0,
 "transport_charges": 0, "reimbursement": 0, "total": 0,
 "confidence": "high"}
All amounts are plain numbers (no "RM", no commas). Negative values are allowed
(credit notes). Use 0 for empty buckets."""

USER_INSTRUCTION = (
    "Extract the invoice as specified. Return JSON only."
)


def build_user_text(page: Page) -> str:
    ocr_text = page.text.strip()[:6000]
    return (
        "OCR TEXT (may contain errors — trust the image):\n"
        "```\n" + (ocr_text or "(no text detected)") + "\n```\n\n"
        + USER_INSTRUCTION
    )


def parse_json(text: str) -> dict:
    """Tolerantly parse a JSON object out of model output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def _num(value) -> float:
    """Coerce messy money values to float. '' / '-' / None -> 0.0."""
    if value in (None, "", "-"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def build_row(data: dict, page: Page, source_file: str) -> Optional[InvoiceRow]:
    """Turn a parsed dict into an InvoiceRow, or None if not an invoice."""
    if not data.get("is_invoice"):
        return None
    row = InvoiceRow(
        project_no=str(data.get("project_no", "")).strip(),
        vendor=str(data.get("vendor", "")).strip(),
        invoice_no=str(data.get("invoice_no", "")).strip(),
        pod=str(data.get("pod", "")).strip(),
        type=str(data.get("type", "")).strip(),
        freight=_num(data.get("freight")),
        local_charges=_num(data.get("local_charges")),
        port_storage=_num(data.get("port_storage")),
        transport_charges=_num(data.get("transport_charges")),
        reimbursement=_num(data.get("reimbursement")),
        total=_num(data.get("total")),
        source_file=source_file,
        source_page=page.index,
        confidence=str(data.get("confidence", "medium")).strip() or "medium",
    )
    if not row.total:
        row.total = row.computed_total
    return row
