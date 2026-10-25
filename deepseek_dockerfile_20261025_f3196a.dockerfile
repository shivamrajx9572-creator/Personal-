# ============================================================================
# MAYA ULTRA-POWER SERVER - PRODUCTION DOCKERFILE
# ============================================================================
FROM python:3.11.9-slim AS base

# --- Environment ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=10000 \
    MAYA_PUBLIC_DIR=/app/generated_public \
    MAYA_DB_PATH=/data/maya_ultra_core.db

# --- System Dependencies (for reportlab, pillow, etc.) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Working Directory ---
WORKDIR /app

# --- Install Python Dependencies (cached layer) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy Application Files ---
COPY . .

# --- Create non-root user (security best practice) ---
RUN useradd -m -u 1000 mayauser \
    && mkdir -p /app/generated_public /data \
    && chown -R mayauser:mayauser /app /data

USER mayauser

# --- Expose Port ---
EXPOSE 10000

# --- Health Check (Docker native) ---
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# --- Start Command ---
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --workers 1"]