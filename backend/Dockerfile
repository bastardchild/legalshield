FROM python:3.11-slim

WORKDIR /app

# Install system deps for asyncpg and PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pip build backend and project deps from pyproject.toml
COPY pyproject.toml .
RUN pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir .

COPY . .

EXPOSE 8000

