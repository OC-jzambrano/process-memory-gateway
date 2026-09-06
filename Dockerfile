FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /bin/bash appuser

# Install Python dependencies first for build layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create storage mount directory and assign permissions
RUN mkdir -p /mnt/process-memory-data /app/data && \
    chown -R appuser:appgroup /mnt/process-memory-data /app

# Copy application sources explicitly
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY server.py .
COPY Caddyfile .

# Ensure ownership by appuser
RUN chown -R appuser:appgroup /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "src.api.http_app:app", "--host", "0.0.0.0", "--port", "8000"]

