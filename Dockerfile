# AXTRACT — universal document parser (DQCL §5 reproduction)
FROM python:3.12-slim

# LibreOffice enables legacy Office formats (doc/ppt/xls).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Model weights (LaTeX OCR) are fetched at build time; checksums verified
# by the fetch script.
COPY scripts/fetch_models.py scripts/fetch_models.py
RUN python scripts/fetch_models.py || echo "WARNING: model fetch failed; equations degrade to OCR-only"

COPY backend backend

# Env at runtime: SUPABASE_URL, SUPABASE_SECRET_KEY, DATABASE_URL,
# AXTRACT_MASTER_KEY_BASE64 (see backend/.env.example)
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
