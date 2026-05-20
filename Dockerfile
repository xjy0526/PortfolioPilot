# PortfolioPilot - Production Container
FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -r appuser

WORKDIR /app

# Install Python deps (without Playwright)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y playwright 2>/dev/null || true

# Copy application
COPY . .

# Create cache directory
RUN mkdir -p /app/cache && chown -R appuser:appuser /app

USER appuser

# Cloud Run sets $PORT automatically
ENV PORT=8080
ENV ENVIRONMENT=production

EXPOSE ${PORT}

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
