# OfinTech Desk Team Server: the engine in server mode plus the web UI.
# Build from the repository root:  docker build -f server/Dockerfile -t ofintech-desk .

FROM node:24-alpine AS ui
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --no-audit --no-fund
COPY ui/ ./
RUN node build.mjs

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# Tesseract OCR (Apache-2.0) for scanned PDFs and images, with English, Arabic, Spanish and French
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara \
        tesseract-ocr-spa tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --home /var/lib/ofintech-desk desk && mkdir -p /var/lib/ofintech-desk \
    && chown desk /var/lib/ofintech-desk
WORKDIR /app
COPY engine/requirements.txt ./
RUN pip install -r requirements.txt
COPY engine/desk_engine ./desk_engine
COPY --from=ui /ui/dist /app/ui
ENV DESK_MODE=server DESK_HOST=0.0.0.0 DESK_PORT=8700 DESK_DATA_DIR=/var/lib/ofintech-desk DESK_UI_DIR=/app/ui
USER desk
EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/health').status == 200 else 1)"
CMD ["python", "-m", "desk_engine", "serve"]
