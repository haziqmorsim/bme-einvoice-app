# BME e-Invoice App

Upload scanned freight-forwarder invoice PDFs → the app reads them with **OCR +
an extraction engine**, sorts the charges into your accounting buckets, and gives
you a downloadable Excel workbook in the exact layout of
`forwarder_detailed_dashboard.xlsx` (a **Detailed** sheet + a **Summary** sheet).

It is built for the real invoices in this folder (CNC Freight, FM Global Logistics,
Unimaju, etc.) — image-based scans, sometimes with **handwritten project numbers**.

---

## Choosing an extraction backend

The extraction step is **pluggable**. Pick one with `EXTRACTION_BACKEND` in `.env`:

| Backend | Cost | Setup | Accuracy | Notes |
|---|---|---|---|---|
| **`ollama`** *(default)* | **Free** | Install Ollama + pull a vision model | High | Local vision LLM, fully offline. Recommended free option. |
| **`rules`** | **Free** | **Nothing extra** | Basic | Pure offline regex/heuristics. Works instantly, but weaker on handwriting & odd layouts. |
| **`anthropic`** | Paid | API key | Highest | Claude cloud API. Best on poor scans/handwriting. |

All three feed the same downstream code, so you can switch any time by editing
one line in `.env` and restarting — no code changes.

### Option A — `ollama` (free, local AI)

1. Install Ollama: https://ollama.com/download
2. Pull a vision model (one time):
   ```
   ollama pull llama3.2-vision
   ```
   Alternatives you can set as `OLLAMA_MODEL`: `llava`, `minicpm-v`, `qwen2.5vl`.
3. Ollama runs a local server at `http://localhost:11434` automatically.
4. In `.env`: `EXTRACTION_BACKEND=ollama`

A vision model needs a reasonably capable machine (≈8 GB RAM/VRAM for the 11B
`llama3.2-vision`). It runs on CPU too, just slower. Nothing leaves your computer.

### Option B — `rules` (free, zero install)

Just set `EXTRACTION_BACKEND=rules`. No model, no API, no network — it parses the
Tesseract OCR text directly. Great for a quick start or air-gapped machines.
Expect to eyeball the results: handwritten project numbers and unusual charge
lines may be missed, and such rows are flagged **⚠ low** confidence.

### Option C — `anthropic` (paid, highest accuracy)

1. Uncomment `anthropic==0.42.0` in `requirements.txt` and `pip install -r requirements.txt`.
2. Get a key at https://console.anthropic.com/ and put it in `.env` as `ANTHROPIC_API_KEY`.
3. In `.env`: `EXTRACTION_BACKEND=anthropic`

---

## How it works (the pipeline)

```
PDF ─► render pages to images ─► Tesseract OCR (text)
                                      │
                          ┌───────────┴────────────┐
        page image  ──►   │   extraction backend   │ ──►  structured JSON per invoice
        + OCR text  ──►   │ ollama / rules / claude │      (project, vendor, invoice no,
                          └────────────────────────┘       POD, type, 5 charge buckets)
                                      │
                                      ▼
                       openpyxl ─► Detailed + Summary .xlsx
```

OCR (Tesseract) is always the first, cheap step. The chosen backend then turns
that text — plus, for the LLM backends, the page image — into structured rows.
Sending the image lets the LLM backends fix OCR mistakes and read handwritten
project numbers that plain OCR garbles.

Each charge line is classified into one of five buckets:

| Bucket | What goes in it |
|---|---|
| **Freight** | ocean/air/barge freight, BAF/CAF, main carriage |
| **Local Charges** | THC, documentation, customs/agency, handling, B/L, DG, port dues |
| **Port Storage** | storage, demurrage, detention, warehousing at port |
| **Transport Charges** | inland haulage, trucking, delivery, cartage |
| **Reimbursement** | duty/tax reimbursement, disbursements, out-of-pocket |

---

## Project layout

```
BME e-Invoice App/
├── app/
│   ├── main.py                FastAPI: /api/upload, /api/status, /api/download, /api/health
│   ├── config.py              settings from .env (incl. EXTRACTION_BACKEND)
│   ├── ocr.py                 PDF → images → Tesseract OCR
│   ├── extractor.py           dispatcher: picks the backend, runs the page loop
│   ├── extract_common.py      shared prompt + JSON/number helpers + row builder
│   ├── extractor_ollama.py    backend: local vision LLM (free, offline)
│   ├── extractor_rules.py     backend: pure offline regex/heuristics (free)
│   ├── extractor_anthropic.py backend: Claude cloud API (paid, optional)
│   ├── excel_builder.py       rows → Detailed + Summary workbook
│   ├── schema.py              data models
│   └── static/                web UI (index.html, style.css, app.js)
├── requirements.txt
├── .env.example               copy to .env and pick your backend
├── run.bat / run.sh           one-command launchers
└── README.md
```

---

## Prerequisites

Always required (one time):

1. **Python 3.10+** — https://www.python.org/downloads/
2. **Tesseract OCR**
   - Windows: install from https://github.com/UB-Mannheim/tesseract/wiki, then set
     `TESSERACT_CMD` in `.env` (e.g. `C:\Program Files\Tesseract-OCR\tesseract.exe`).
   - macOS: `brew install tesseract` · Ubuntu/Debian: `sudo apt install tesseract-ocr`
3. **Poppler** (renders PDF pages to images)
   - Windows: download from https://github.com/oschwartz10612/poppler-windows/releases,
     unzip, set `POPPLER_PATH` in `.env` to its `...\Library\bin` folder.
   - macOS: `brew install poppler` · Ubuntu/Debian: `sudo apt install poppler-utils`

Then, depending on backend: **Ollama** (option A) **or** nothing (option B) **or**
an **Anthropic API key** (option C).

---

## Setup & run

### Quick start

**Windows** — double-click `run.bat`. **macOS / Linux** — `chmod +x run.sh && ./run.sh`

The script makes a virtual environment, installs dependencies, copies
`.env.example` → `.env` on first run, and starts the server. The default backend
is `ollama` — edit `.env` if you want `rules` or `anthropic`.

### Manual start

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# mac/linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then choose EXTRACTION_BACKEND
python -m uvicorn app.main:app --reload --port 8000
```

Open **http://127.0.0.1:8000**. The pill in the top-right shows the active backend
and whether it's ready (green) or needs attention (red — hover for the reason).

> **Tip (Windows):** if `uvicorn` isn't found, your virtual environment isn't
> active. Activate it (`.venv\Scripts\activate`) or call it directly:
> `.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000`.

---

## Using it

1. **Drag in one or more PDF invoices** (scanned is fine).
2. Click **Extract & build Excel**. A progress bar shows pages being read.
3. Review the table — rows flagged **⚠ low** had a poor scan / uncertain parse
   and are worth a quick manual check.
4. Click **Download Excel** to get `forwarder_detailed_dashboard.xlsx`.

A single PDF can hold many invoices across many pages; the app detects each
invoice page and produces one row per invoice. Terms pages, packing lists and
blank pages are skipped.

---

## Notes & limitations

- **Accuracy varies by backend** (see the table up top). With `rules`, always
  sanity-check totals and project numbers. With `ollama`/`anthropic`, accuracy is
  high but still worth a glance on faint scans. The **Source** column (file + page)
  and the **confidence** flag help you trace any row back.
- **Summary sheet:** charges are aggregated by **Project No + Vendor**, summing the
  five buckets. Your original dashboard used some manual groupings, so this
  auto-generated version may group slightly differently — adjust in Excel if needed.
- **Totals:** the printed grand total is used when present; otherwise the app falls
  back to the sum of the five buckets. The Detailed sheet also has a live `=SUM()`
  TOTAL row.
- **Privacy:** `ollama` and `rules` are 100% local — nothing leaves your machine.
  `anthropic` sends page images to the Anthropic API.

---

## API (for scripting)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/upload` | multipart `files[]` of PDFs → `{job_id}` |
| `GET`  | `/api/status/{job_id}` | progress + extracted rows (JSON) |
| `GET`  | `/api/download/{job_id}` | the generated `.xlsx` |
| `GET`  | `/api/health` | active backend + readiness |

Interactive docs at **http://127.0.0.1:8000/docs**.
