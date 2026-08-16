FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    CHATGPT_HOME=/app/.data/prod \
    CHATGPT_TZ=Asia/Kolkata \
    DISPLAY=:99 \
    HERMES_HEADLESS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install Xvfb, fonts, and required OS utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
      bash \
      xvfb \
      xauth \
      x11-utils \
      procps \
      fonts-liberation \
      fonts-noto-color-emoji \
      ca-certificates \
      curl \
      gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency specifications and install Python virtual environment
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Install Playwright Chromium and its Linux system libraries
RUN uv run --no-sync playwright install --with-deps chromium

# Copy application source code and configurations
COPY . .

# Finalize project installation and entrypoint permissions
RUN chmod +x /app/entrypoint.sh && uv sync --frozen --no-dev

# Create base directories with appropriate permissions
RUN mkdir -p "${CHATGPT_HOME}/profile" /app/cookies && chmod 700 "${CHATGPT_HOME}"

EXPOSE 18111
VOLUME ["/app/.data", "/app/cookies"]

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["start"]
