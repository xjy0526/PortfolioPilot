# PortfolioPilot - Production Container
FROM python:3.12-slim AS dependencies

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -r appuser

WORKDIR /app

# Install Python deps (without Playwright)
COPY requirements.txt .
ARG TORCH_VERSION=2.13.0
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}+cpu" \
    && pip install --no-cache-dir -r requirements.txt

# The Python base image and PyTorch CPU index can carry older packaging tools.
# Keep every derived runtime free of known pip/setuptools advisories as well.
RUN python -m pip install --no-cache-dir --upgrade \
    "pip>=26.1.2,<27" \
    "setuptools>=83,<85"

# Lightweight deterministic target for Compose E2E. It contains the complete
# application but deliberately does not download a model checkpoint.
FROM dependencies AS e2e

COPY . .

RUN mkdir -p /app/cache /app/data/object_storage \
    && chown -R appuser:appuser /app

USER appuser

ENV PORT=8080
ENV ENVIRONMENT=test

EXPOSE ${PORT}

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1"]

FROM dependencies AS production

# Bake the verified 384-dimensional multilingual model into the immutable
# image so production startup does not depend on a model-registry download.
ARG RAG_EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${RAG_EMBEDDING_MODEL}')"
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

# Copy application
COPY . .

# Create cache directory
RUN mkdir -p /app/cache && chown -R appuser:appuser /app

USER appuser

# Cloud Run sets $PORT automatically
ENV PORT=8080
ENV ENVIRONMENT=production

EXPOSE ${PORT}

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1"]
