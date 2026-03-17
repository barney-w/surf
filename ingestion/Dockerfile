# syntax=docker/dockerfile:1

# ---------- builder ----------
FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7@sha256:629240833dd25d03949509fc01ceff56ae74f5e5f0fd264da634dd2f70e9cc70 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------- runtime ----------
FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0 AS runtime

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "from src.connectors.pdf import create_document_from_pdf; from src.pipeline.chunking import chunk_document" || exit 1

ENTRYPOINT ["python", "-m", "src.main"]
