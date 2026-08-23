# Multi-stage: build wheels with a toolchain, then ship a runtime image without gcc.
# Keeps the final layer free of compilers and header packages.
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Set INSTALL_DEV=true to additionally install the test/lint extras (used by the
# `test` service in docker-compose.yml). Production images keep it false.
ARG INSTALL_DEV=false
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip hatchling && \
    pip wheel --no-cache-dir --wheel-dir /wheels hatchling && \
    if [ "$INSTALL_DEV" = "true" ]; then \
        pip wheel --no-cache-dir --wheel-dir /wheels ".[dev]"; \
    else \
        pip wheel --no-cache-dir --wheel-dir /wheels .; \
    fi


FROM python:3.11-slim

WORKDIR /app

# libpq5 is the runtime half of libpq-dev, needed by psycopg2-binary's fallback path.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_DEV=false
COPY --from=builder /wheels /wheels
COPY pyproject.toml .
RUN pip install --no-cache-dir --no-index --find-links=/wheels hatchling && \
    if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir --no-index --find-links=/wheels ".[dev]"; \
    else \
        pip install --no-cache-dir --no-index --find-links=/wheels .; \
    fi && \
    rm -rf /wheels

COPY . .

# The compose file bind-mounts ./backend over /app for --reload, which shadows this copy.
# A production deployment should drop that mount and rely on the image contents.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
