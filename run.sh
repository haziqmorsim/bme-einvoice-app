#!/usr/bin/env bash
# ---- BME e-Invoice App launcher (macOS / Linux) ----
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  echo
  echo "  No .env found - copying .env.example to .env"
  echo "  Open .env and paste your ANTHROPIC_API_KEY before uploading invoices."
  echo
  cp .env.example .env
fi

echo "Starting server at http://127.0.0.1:8000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
