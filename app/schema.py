"""Data models for extracted invoice rows."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class InvoiceRow(BaseModel):
    """One row in the 'Detailed' sheet = one freight-forwarder invoice."""

    project_no: str = Field("", description="Project / PO number (often handwritten).")
    vendor: str = Field("", description="Forwarder company that issued the invoice.")
    invoice_no: str = Field("", description="Invoice number.")
    pod: str = Field("", description="Port of discharge / final destination.")
    type: str = Field("", description="Shipment type e.g. LCL, FCL, Barge, 1 x 40'HC.")

    freight: float = Field(0.0, description="Freight charges (RM).")
    local_charges: float = Field(0.0, description="Local / destination charges (RM).")
    port_storage: float = Field(0.0, description="Port storage / demurrage (RM).")
    transport_charges: float = Field(0.0, description="Inland transport / haulage (RM).")
    reimbursement: float = Field(0.0, description="Reimbursements / disbursements (RM).")
    total: float = Field(0.0, description="Grand total inclusive of tax (RM).")

    # Bookkeeping (not written to the Detailed sheet's main columns).
    source_file: str = Field("", description="Originating PDF filename.")
    source_page: Optional[int] = Field(None, description="1-based page number.")
    confidence: str = Field("medium", description="low | medium | high")

    @property
    def computed_total(self) -> float:
        return round(
            self.freight
            + self.local_charges
            + self.port_storage
            + self.transport_charges
            + self.reimbursement,
            2,
        )


class ExtractionResult(BaseModel):
    rows: list[InvoiceRow] = []
    warnings: list[str] = []
