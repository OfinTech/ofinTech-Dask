# OfinTech Desk Team Server: the engine in server mode plus the web UI.
# Build from the repository root:  docker build -f server/Dockerfile -t ofintech-desk .

FROM node:26-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN node build.mjs

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# apt is upgraded before anything else is installed, and the base image's other packages with it, so the image
# ships with the operating-system fixes Debian has already published rather than whichever ones its base layer
# happened to be built with. CVE-2026-109518 is why this is not optional: apt's handling of a resumed HTTP
# download could be made to merge two downloads when a redirect gave them the same address, letting an on-path
# attacker substitute a package after it had passed verification and run code as root. Debian fixed it in apt
# 3.0.4 for trixie.
#
# Two steps in one layer, because the apt that fetches the fix is still the vulnerable one:
#   1. retries off, in the layer's own first command, and nothing fetched but apt itself: with no retry there is
#      no resumed download to merge, which is the mitigation the advisory gives for a build whose sources are
#      not yet HTTPS. The build stops here if apt is not at 3.0.4 or later afterwards.
#   2. retries on again (three, stated rather than left to a default) for everything else, now fetched by the
#      fixed apt: the rest of the base image and Tesseract, about a hundred packages.
# Until 22 Nov 2026 the whole layer ran without retries, so one connection reset by the mirror on one package
# out of that hundred failed the build (the arm64 build on main, on a merge that had not touched this file).
# The drop-in goes away with the package lists.
# Tesseract OCR (Apache-2.0) for scanned PDFs and images, with English, Arabic, Spanish and French
RUN printf 'Acquire::Retries "0";\n' > /etc/apt/apt.conf.d/99-ci-no-retries \
    && apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade apt \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' apt)" ge 3.0.4 \
    && rm -f /etc/apt/apt.conf.d/99-ci-no-retries \
    && apt-get -o Acquire::Retries=3 upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
        tesseract-ocr-ara tesseract-ocr-spa tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --home /var/lib/ofintech-desk desk && mkdir -p /var/lib/ofintech-desk \
    && chown desk /var/lib/ofintech-desk
WORKDIR /app
COPY engine/requirements.txt ./
RUN pip install -r requirements.txt
COPY engine/desk_engine ./desk_engine
# The three invented files "Try it with a demo company" loads (desk_engine/demo.py): about 100 KB, written by
# examples/demo-company/generate.py, naming no real company or person. Leave the line out and the button is not offered.
COPY examples/demo-company/HarborLine-demo/ofintech-desk/ ./demo/
COPY --from=ui /ui/dist /app/ui
ENV DESK_MODE=server DESK_HOST=0.0.0.0 DESK_PORT=8700 DESK_DATA_DIR=/var/lib/ofintech-desk DESK_UI_DIR=/app/ui
USER desk
EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/health').status == 200 else 1)"
CMD ["python", "-m", "desk_engine", "serve"]
