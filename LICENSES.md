# Licences and third-party components

## This project

OfinTech Desk is licensed under the **Apache License 2.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)). Every runtime
dependency below uses a permissive licence (MIT, BSD-3-Clause or Apache-2.0) that is compatible with it. The optional
MinIO server is a separate AGPL service that the project does not link to or ship by default (see the last table).

All code in this repository was written for OfinTech Desk. **No code was copied from OpenHuman** (GPL-3.0); only its
publicly described concepts were used (an agent harness with MCP, skills, workflows, messaging channels and model
choice). Copyright protects code rather than ideas, so the GPL does not apply; organisations with strict policies
may still want counsel to confirm this before they redistribute.

The OCR engine, Tesseract, is Apache-2.0 and is installed as a separate program (in the Team Server image and,
optionally, on the desktop); its language data files are Apache-2.0 as well.

## Runtime dependencies

| Component | Version | Licence | Used for |
|---|---|---|---|
| Starlette | 1.7.0 | BSD-3-Clause | HTTP framework |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| pydantic | 2.13.5 | MIT | Validation |
| httpx2 | 2.13.1 | BSD-3-Clause | HTTP client (also used by the MCP SDK) |
| mcp (Python SDK) | 2.3.0 | MIT | MCP client and server |
| openpyxl | 3.1.5 | MIT | Excel import and Excel workbooks |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | AES-GCM |
| python-multipart | 0.0.32 | Apache-2.0 | File uploads |
| PyYAML | 6.0.3 | MIT | Skill front matter |
| pypdf | 6.21.2 | BSD-3-Clause | PDF text extraction |
| python-docx | 1.2.0 | MIT | Word extraction and Word reports |
| NumPy | 2.5.3 | BSD-3-Clause (and bundled permissive licences) | Semantic search |
| React, React DOM | 19.2.8 | MIT | UI |
| IBM Plex Sans Arabic (Regular, Medium, SemiBold, Bold; © 2017 IBM Corp., from the Google Fonts repository) | SIL Open Font License 1.1, Reserved Font Name "Plex" | Arabic interface text. Bundled in the UI as WOFF, converted from the TTF files without changes to the glyphs or names; the licence travels with it in `ui/src/fonts/OFL.txt` and the built `assets/OFL-IBM-Plex-Sans-Arabic.txt` |
| Electron | 44.x | MIT (Chromium: BSD-3-Clause and others; see `LICENSES.chromium.html` in the app) | Desktop shell |

Build tools (not shipped): esbuild (MIT), TypeScript (Apache-2.0), electron-builder (MIT) and PyInstaller (GPL-2.0
with a bootloader exception that allows bundling proprietary applications).

The UI bundle's licence notices are written to `ui/dist/assets/app.js.LEGAL.txt`. Generate a full SBOM for each
release (for example with `syft`) and include the notices with the installers.

## Optional server components

| Component | Licence | Notes |
|---|---|---|
| MinIO server, run from **PGSTY Silo** `RELEASE.2026-10-18T00-00-00Z` (`server/docker-compose.minio.yml`) | GNU AGPLv3 | Separate network service, used unmodified over the S3 API; OfinTech Desk does not link to it. MinIO, Inc. no longer publishes community images. If you distribute images that contain it, pass on the AGPL and offer the corresponding source. "MinIO" is a trademark of MinIO, Inc. |
| MinIO AIStor (`server/docker-compose.aistor.yml`) | Proprietary (MinIO, Inc.) | Not used by default. Needs a licence file; do not ship the image or licence inside your own distribution. |
| Caddy | Apache-2.0 | HTTPS reverse proxy |

## Third-party services

PipesHub (Apache-2.0, run separately by you or PipesHub Cloud, used through its HTTP API), Odoo (its external API, under the customer's Odoo licence), language model providers, Intuit, Zoho, Xero, Google, Telegram and Meta are used under their own terms. Customers
connect their own accounts. Review each provider's developer terms and data-processing terms before launch, including
where model providers process prompts. Names and trademarks belong to their owners. Their use here describes
compatibility only and implies no endorsement.

## Open items before commercial release

1. Choose the project licence.
2. Provider programme requirements: Intuit app assessment, Google restricted-scope verification and security
   assessment, Xero tier or certification, Meta business verification (see docs/CONNECTORS.md).
3. Privacy notice and data-processing terms for the Team Server and any hosted service.
4. Code-signing certificates (Apple Developer ID, Windows code signing).
