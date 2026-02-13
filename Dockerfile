# Multi-stage Dockerfile để optimize image size

# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /build

# Install Poetry
RUN pip install --no-cache-dir poetry==1.7.1

# Copy dependency files
COPY pyproject.toml ./

# Configure Poetry: không tạo virtual env trong Docker
RUN poetry config virtualenvs.create false

# Install dependencies vào system Python
RUN poetry install --no-dev --no-root --no-interaction --no-ansi

# Stage 2: Runtime
FROM python:3.11-slim

# Install gosu for entrypoint privilege drop
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /app

# Copy installed packages từ builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app/ /app/app/

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create directories
RUN mkdir -p /app/data /tmp/plex-subtitles && \
    chown -R appuser:appuser /app/data /tmp/plex-subtitles /app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD gosu appuser python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

# Entrypoint fixes permissions then drops to appuser
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
