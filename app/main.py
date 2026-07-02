"""FastAPI application: upload PDFs -> OCR + extraction -> downloadable Excel."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .excel_builder import build_workbook
from . import extractor
from .ocr import process_pdf
from .schema import InvoiceRow

app = FastAPI(title="BME e-Invoice App", version="1.2.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR.parent / "generated"
OUTPUT_DIR.mkdir(exist_ok=True)


@dataclass
class Job:
    id: str
    file_names: list[str] = field(default_factory=list)  # uploaded PDF names
    status: str = "queued"          # queued | processing | done | error
    message: str = ""
    total_pages: int = 0
    done_pages: int = 0
    rows: list[InvoiceRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    excel_path: str | None = None


JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _output_filename(job: Job) -> str:
    """Name the downloaded Excel after the uploaded PDF(s)."""
    stems = [Path(n).stem for n in job.file_names if n]
    if not stems:
        return "invoices.xlsx"
    if len(stems) == 1:
        base = stems[0]
    else:
        base = f"{stems[0]}_and_{len(stems) - 1}_more"
    return f"{base}.xlsx"


def _row_dict(r: InvoiceRow) -> dict:
    return {
        "project_no": r.project_no, "vendor": r.vendor, "invoice_no": r.invoice_no,
        "pod": r.pod, "type": r.type, "freight": r.freight,
        "local_charges": r.local_charges, "port_storage": r.port_storage,
        "transport_charges": r.transport_charges, "reimbursement": r.reimbursement,
        "total": r.total, "source_file": r.source_file,
        "source_page": r.source_page, "confidence": r.confidence,
    }


def _process(job_id: str, files: list[tuple[str, bytes]]) -> None:
    """Background worker: OCR + extraction + Excel build."""
    job = JOBS[job_id]
    try:
        job.status = "processing"

        # Phase 1: render + OCR every page (so we know the total for the bar).
        # Only the vision (LLM) backends need the rendered image kept in memory;
        # the offline "rules" backend reads OCR text only, so we drop images to
        # keep peak memory low on small hosts.
        keep_images = (settings.extraction_backend or "").strip().lower() in (
            "ollama",
            "anthropic",
        )
        all_pages: list[tuple[str, list]] = []
        for name, data in files:
            pages = process_pdf(data, keep_images=keep_images)
            all_pages.append((name, pages))
            job.total_pages += len(pages)

        # Phase 2: extract, once per file so warm-up / circuit-breaker / fallback
        # span the whole document. progress() bumps the page counter for the UI.
        def bump() -> None:
            job.done_pages += 1

        for name, pages in all_pages:
            result = extractor.extract_pages(pages, name, progress=bump)
            job.rows.extend(result.rows)
            job.warnings.extend(result.warnings)

        # Phase 3: build the Excel workbook.
        xlsx = build_workbook(job.rows)
        out_path = OUTPUT_DIR / f"BME_invoices_{job_id}.xlsx"
        out_path.write_bytes(xlsx)
        job.excel_path = str(out_path)

        job.status = "done"
        job.message = f"Extracted {len(job.rows)} invoice(s) from {len(files)} file(s)."
    except Exception as exc:  # noqa: BLE001
        import traceback
        # Log the full traceback so the real cause is visible in server logs
        # (the message shown to the user is often misleading, e.g. pdf2image
        # reports "Is poppler installed?" for any OS-level spawn failure,
        # including out-of-memory fork() errors).
        traceback.print_exc()
        job.status = "error"
        job.message = str(exc)


@app.post("/api/upload")
async def upload(files: list[UploadFile]) -> JSONResponse:
    if not files:
        raise HTTPException(400, "No files uploaded.")
    # Validate the selected extraction backend is configured & reachable.
    try:
        extractor.preflight()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, str(exc))

    payloads: list[tuple[str, bytes]] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"'{f.filename}' is not a PDF.")
        payloads.append((f.filename, await f.read()))

    job = Job(id=uuid.uuid4().hex[:12], file_names=[n for n, _ in payloads])
    with _LOCK:
        JOBS[job.id] = job
    threading.Thread(target=_process, args=(job.id, payloads), daemon=True).start()
    return JSONResponse({"job_id": job.id})


@app.get("/api/status/{job_id}")
async def status(job_id: str) -> JSONResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")
    return JSONResponse({
        "status": job.status,
        "message": job.message,
        "total_pages": job.total_pages,
        "done_pages": job.done_pages,
        "warnings": job.warnings,
        "rows": [_row_dict(r) for r in job.rows],
        "has_excel": job.excel_path is not None,
        "download_name": _output_filename(job),
    })


@app.get("/api/download/{job_id}")
async def download(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or not job.excel_path:
        raise HTTPException(404, "Result not ready.")
    return FileResponse(
        job.excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=_output_filename(job),
    )


@app.get("/api/health")
async def health() -> dict:
    info = extractor.health()
    info["ok"] = True
    return info


# Serve the frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")