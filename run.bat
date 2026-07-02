@echo off
REM ---- BME e-Invoice App launcher (Windows) ----
cd /d "%~dp0"

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing dependencies...
pip install -q -r requirements.txt

if not exist ".env" (
  echo.
  echo  No .env found - copying .env.example to .env
  echo  Open .env and paste your ANTHROPIC_API_KEY before uploading invoices.
  echo.
  copy /Y ".env.example" ".env" >nul
)

echo Starting server at http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
