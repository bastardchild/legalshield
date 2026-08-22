FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pip build backend and project deps from pyproject.toml.
# Set INSTALL_DEV=true to additionally install the test/lint extras (used by the
# `test` service in docker-compose.yml). Production images keep it false.
ARG INSTALL_DEV=false
COPY pyproject.toml .
RUN pip install --no-cache-dir hatchling && \
    if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir ".[dev]"; \
    else \
        pip install --no-cache-dir .; \
    fi

COPY . .

EXPOSE 8000

