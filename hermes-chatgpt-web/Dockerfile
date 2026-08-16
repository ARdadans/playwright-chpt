# ==============================================================================
# Hermes Novel Translation System - Production Dockerfile
# Optimized for True Concurrent Multi-Account Playwright Chromium Automation
# ==============================================================================

FROM python:3.11-slim-bookworm

# Core environment configuration
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHATGPT_HOME=/app/.data/prod \
    CHATGPT_TZ=Asia/Kolkata \
    DISPLAY=:99 \
    HERMES_HEADLESS=0 \
    ADAPTER_PORT=18111 \
    WORKER_POLL_INTERVAL=2.0 \
    JOB_COOLDOWN_SECONDS=60 \
    CONTEXT_REFRESH_JOBS=10 \
    TRANSLATION_JOB_TIMEOUT=480 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# Install Xvfb, X11 utilities, certificates, and multi-language fonts (KO/JA/ZH/Emoji)
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      xvfb \
      xauth \
      x11-utils \
      procps \
      curl \
      ca-certificates \
      gnupg \
      fonts-liberation \
      fonts-noto-color-emoji \
      fonts-nanum \
      fonts-ipafont-gothic \
      fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# Install uv high-performance package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Layer 1: Copy dependency specifications and install Python virtual environment
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Layer 2: Install Playwright Chromium and required Linux system dependencies
RUN uv run --no-sync playwright install --with-deps chromium

# Layer 3: Copy application source code and configurations
COPY . .

# Finalize project installation and entrypoint permissions
RUN chmod +x /app/entrypoint.sh && uv sync --frozen --no-dev

# Ensure base runtime directories exist with appropriate permissions
RUN mkdir -p "${CHATGPT_HOME}/profile" /app/cookies && chmod 700 "${CHATGPT_HOME}"

# Network port and volume persistence points
EXPOSE 18111
VOLUME ["/app/.data", "/app/cookies"]

# Container health monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD curl -f http://127.0.0.1:18111/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["start"]
