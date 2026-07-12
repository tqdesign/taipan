FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY main.py ./
COPY taipan ./taipan
COPY static ./static

# Build stamp, shown in the UI as vMMDDYY.HHMM (UTC).
RUN date -u +v%m%d%y.%H%M > version.txt

# Persist the saves/ directory on a volume in production.
VOLUME ["/app/saves"]

ENV HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Single worker by design: live game generators are per-process.
CMD ["uv", "run", "--no-sync", "main.py"]
