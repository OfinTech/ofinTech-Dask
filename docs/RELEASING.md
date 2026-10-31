# Releasing the desktop app

`.github/workflows/build.yml` builds installers on macOS, Windows and Linux:

1. Engine tests (with OCR and LibreOffice), the UI type check, translation check and build, the browser smoke test
   and the desktop type check (Ubuntu).
2. On each OS: the engine is bundled with PyInstaller (`engine/packaging/build_engine.py` → `engine/dist/desk-engine`),
   the UI is built, then `electron-builder` packages both into installers (`desktop/release/`).

Signing (repository secrets):

| Secret | Purpose |
|---|---|
| `CSC_LINK`, `CSC_KEY_PASSWORD` | Code-signing certificate (base64 .p12) for macOS or Windows |
| `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | macOS notarisation |

Without these secrets the installers are built unsigned: macOS Gatekeeper and Windows SmartScreen will warn users.

Smoke test of the desktop shell (Linux, no display):

```bash
cd desktop && npm install && npm run build
DESK_SMOKE_OUT=/tmp/smoke xvfb-run -a npx electron --no-sandbox .
cat /tmp/smoke/smoke.json    # url, token bridge present, no Node in the page, page text
```

Optional build-time OAuth credentials: export `DESK_<PROVIDER>_CLIENT_ID/SECRET` in the environment that starts the
app. The desktop shell passes them to the engine. Read the distribution note in CONNECTORS.md first.

## Cutting a release

1. Move the *Unreleased* notes in `CHANGELOG.md` under the new version and date.
2. Set the version in `engine/desk_engine/config.py` (`VERSION`), `engine/pyproject.toml`, `ui/package.json` and
   `desktop/package.json`.
3. Run the full checks (`docs/TESTING.md`) and update its *What was executed* table.
4. Tag `vX.Y.Z` and push the tag. The `release` job in `build.yml` waits for the tests, the installers on all
   three systems and the Team Server image, then creates the GitHub release with the installers attached and the
   notes taken from this version's section of `CHANGELOG.md`. It fails rather than publishing an empty release if
   the changelog has no section for the tag, or if an installer is missing. Mention any new migrations or settings
   in the notes, and say so in the release if the installers are unsigned.
