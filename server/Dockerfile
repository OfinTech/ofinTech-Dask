# OfinTech Desk Team Server: the engine in server mode plus the web UI.
# Build from the repository root:  docker build -f server/Dockerfile -t ofintech-desk .

FROM node:26-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN node build.mjs

FROM python:3.15-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# apt is upgraded before anything else is installed, and the base image's other packages with it, so the image
# ships with the operating-system fixes Debian has already published rather than whichever ones its base layer
# happened to be built with. CVE-2026-109518 is why this is not optional: apt's handling of a resumed HTTP
# download could be made to merge two downloads when a redirect gave them the same address, letting an on-path
# attacker substitute a package after it had passed verification and run code as root. Debian fixed it in apt
# 3.0.4 for trixie. Retries are switched off first, in the layer's own first command, because the apt that
# fetches the fix is still the vulnerable one: with no retry there is no resumed download to merge, which is the
# mitigation the advisory gives for a build whose sources are not yet HTTPS. The drop-in goes away with the
# package lists, since once apt is past 3.0.4 the stock setting is the right one again.
# Tesseract OCR (Apache-2.0) for scanned PDFs and images, with English, Arabic, Spanish and French
RUN printf 'Acquire::Retries "0";\n' > /etc/apt/apt.conf.d/99-ci-no-retries \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara \
        tesseract-ocr-spa tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99-ci-no-retries
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
