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

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /app

# Copy installed packages từ builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY app/ /app/app/

# Create temp directory với proper permissions
RUN mkdir -p /tmp/plex-subtitles && \
    chown -R appuser:appuser /tmp/plex-subtitles /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()"

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
