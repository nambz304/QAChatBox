# syntax=docker/dockerfile:1

# ── Stage 1: dependency builder ───────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Use a virtual environment — avoids --prefix install which corrupts C extension .so files
# (hnswlib, PyYAML, etc. become 0-byte when copied via --prefix)
RUN python -m venv /venv

COPY requirements.txt .

# --mount=type=cache keeps downloaded .whl files on the host between builds.
# Changing requirements.txt only re-downloads NEW/changed packages —
# torch (530 MB), sentence-transformers, etc. are served from cache.
RUN --mount=type=cache,target=/root/.cache/pip \
    /venv/bin/pip install -r requirements.txt

# Pre-download the sentence-transformers model so it's baked into the image
# (avoids a ~270 MB download on every cold start)
# Must match EMBEDDING_MODEL in .env — currently: paraphrase-multilingual-MiniLM-L12-v2
RUN /venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; \
     SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"


# ── Stage 2: runtime ──────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the entire virtual environment from builder (all files intact, no corruption)
COPY --from=builder /venv /venv
# Copy cached HuggingFace model files
COPY --from=builder /root/.cache /root/.cache

# Make the venv the default Python/pip/uvicorn/streamlit for this container
ENV PATH="/venv/bin:$PATH"

# Copy source code
COPY . .

# Create persistent data directory
RUN mkdir -p data

EXPOSE 8000 8501

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["bash", "/entrypoint.sh"]
