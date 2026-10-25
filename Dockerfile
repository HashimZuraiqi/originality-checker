# syntax=docker/dockerfile:1

# ---- Frontend build -------------------------------------------------------
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Application ----------------------------------------------------------
FROM python:3.12-slim AS app
# Semantic similarity model baked into the image:
#   wordvec (default) - spaCy word vectors, ~30 MB, no Hugging Face access needed
#   sbert             - Sentence-Transformers MiniLM + cross-encoder (best quality,
#                       needs Hugging Face access at build time, ~1 GB larger)
#   none              - lexical fallback only (smallest; semantic results degraded)
ARG SEMANTIC=wordvec
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

RUN if [ "$SEMANTIC" = "sbert" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install "sentence-transformers>=3.0" && \
      python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/stsb-distilroberta-base')"; \
    fi

COPY backend/ ./
RUN rm -rf data models && \
    if [ "$SEMANTIC" = "wordvec" ]; then \
      pip install spacy==3.8.16 "en_core_web_md @ https://github.com/explosion/spacy-models/releases/download/en_core_web_md-3.8.0/en_core_web_md-3.8.0-py3-none-any.whl" && \
      python -m app.tools.build_wordvec && \
      pip uninstall -y en_core_web_md spacy; \
    fi
COPY --from=web /web/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 app && mkdir -p /app/backend/data && chown -R app:app /app/backend/data
USER app

ENV EMBEDDING_PROVIDER=auto
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
