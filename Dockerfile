# ─────────────────────────────────────────────────────────────────────────────
# Anupam Bearings — Production Dockerfile
# Multi-stage build for minimal image size
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependencies stage ───────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .

# Install Python deps with no cache to reduce image size
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so workers start instantly
# (removes cold-start delay on first RAG query)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ── Application stage ────────────────────────────────────────────────────────
FROM deps AS app

COPY . .

# Create required directories
RUN mkdir -p \
    data/faiss_index \
    data/knowledge_base \
    staticfiles \
    media/products \
    media/categories

# Collect static files
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["gunicorn", "anupam_bearings.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "120"]
