FROM python:3.11-slim

# System binaries required by the app:
#   - tesseract-ocr : OCR engine used by pytesseract
#   - poppler-utils : PDF rendering backend used by pdf2image
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# --- Memory tuning for small (512 MB) hosts ---
# Cap glibc malloc arenas so fork()/spawn of poppler doesn't fail with ENOMEM
# under a tight memory limit; keep Tesseract single-threaded.
ENV MALLOC_ARENA_MAX=2 \
    OMP_THREAD_LIMIT=1

WORKDIR /app

# Install Python dependencies first (better build caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Render provides the port to bind on via $PORT
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands at runtime
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}