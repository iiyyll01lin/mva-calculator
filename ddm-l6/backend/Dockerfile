# =============================================================================
# DDM-L6  FastAPI Backend
# =============================================================================
# Base: python:3.11-slim
#   • Smaller than full python:3.11 while remaining compatible with the
#     cryptographic dependencies (python-jose, PyJWT) that Alpine can struggle
#     with due to libc / musl differences.
#
# Security posture
#   • Application runs as a non-root system user (appuser, uid 1001).
#   • The data/ directory is bind-mounted at runtime — the host filesystem
#     governs its permissions.
#
# Layer-cache strategy
#   • requirements.txt is copied and installed BEFORE the application code.
#     Changing source files does not invalidate the dependency cache layer.
# =============================================================================

FROM python:3.11-slim

# ── Non-root runtime user ─────────────────────────────────────────────────────
RUN groupadd --system --gid 1001 appgroup \
 && useradd  --system --uid 1001 --gid appgroup --no-create-home appuser

# ── Working directory mirrors the on-disk layout expected by main.py ─────────
# main.py computes:  DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# With WORKDIR=/app/backend → DATA_DIR resolves to /app/data (bind-mounted below).
WORKDIR /app/backend

# ── Proxy build args (passed from docker-compose / CI for air-gapped builds) ──
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ARG http_proxy
ARG https_proxy
ARG no_proxy
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY} \
    http_proxy=${http_proxy} \
    https_proxy=${https_proxy} \
    no_proxy=${no_proxy}

# ── Dependency layer (cached independently from application code) ─────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# Ownership is set after the COPY so the runtime user can read all files.
# /app/data is bind-mounted at runtime with host-controlled permissions.
RUN chown -R appuser:appgroup /app/backend

# ── Drop to non-root ──────────────────────────────────────────────────────────
USER appuser

EXPOSE 8000

# Single worker is intentional: the file-backed JSON database (db_persistent.json)
# is not safe for concurrent multi-worker writes without a proper lock layer.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
