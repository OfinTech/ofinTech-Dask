# Desktop app for desk.ofintech.ai (macOS and Windows)

Two desktop editions are built from the same code:

| Edition | What it contains | Where data lives | Build |
|---|---|---|---|
| **Cloud client** (`electron-builder.cloud.yml`) | The app window only. It opens `https://desk.ofintech.ai` (or another Team Server chosen at build time). | On the Team Server | `.github/workflows/desktop-cloud.yml`, about 5 minutes |
| Standalone (`electron-builder.yml`) | The app and the Python engine (PyInstaller) | On the computer | `.github/workflows/build.yml` |

This page covers the cloud client.

## What the cloud client does

- Opens the Team Server at start. People sign in with their e-mail and password, or with Google (below).
- Shows a *cannot reach the server* page with **Try again** when the computer is offline.
- Opens `ofintech-desk://` links from Excel and Word files in the app.
- Keeps the security settings of the standalone app: sandboxed page, no Node, navigation pinned to the server,
  external links in the system browser, permission requests denied.
- *File → Connect to a Team Server…* can point it at another server; there is no local mode in this edition.

**Google sign-in.** Google does not allow sign-in inside embedded browsers, so the app opens Google in the system
browser. After sign-in the browser shows *You are signed in* and opens the app through `ofintech-desk://`; the app
then redeems a one-time code in its own window. The code is valid for two minutes, works once, and only for the app
that started the sign-in (it holds a secret verifier whose hash was sent at the start), so a link sent by someone
else cannot sign a person into another account.

## Build the installers

1. The Team Server runs at `https://desk.ofintech.ai` (see DEPLOYMENT.md, with `DESK_DOMAIN=desk.ofintech.ai`).
   For Google sign-in, the Google OAuth app has the redirect URI `https://desk.ofintech.ai/oauth/callback`.
2. Push the repository to GitHub.
3. **Actions → desktop-cloud → Run workflow** (the server address can be changed there), or push a tag such as
   `desk-v1.0.0`, which also creates a GitHub release with the files.
4. Download from the run's artifacts:
   - macOS: `OfinTech-Desk-1.0.0-mac-arm64.dmg` (Apple silicon) and `…-mac-x64.dmg` (Intel), plus `.zip` copies;
   - Windows: `OfinTech-Desk-1.0.0-win-x64.exe` and `…-win-arm64.exe` (or a combined installer, depending on the
     electron-builder version).

Locally, on a Mac or a Windows PC: `cd desktop && npm install && npm run build && npx electron-builder --config
electron-builder.cloud.yml --mac` (or `--win`).

## Signing (strongly recommended before giving the app to people)

Without signing the app works, but macOS blocks it on first open (*Control-click → Open*, or *System Settings →
Privacy & Security → Open Anyway*) and Windows SmartScreen warns (*More info → Run anyway*).

| Platform | You need | Repository secrets |
|---|---|---|
| macOS | Apple Developer Program membership; a *Developer ID Application* certificate exported as .p12; an app-specific password | `CSC_LINK` (base64 of the .p12), `CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` (signing and notarisation) |
| Windows | A code-signing certificate (OV or EV) from a certificate authority, as .pfx, or a cloud signing service | `WIN_CSC_LINK` (base64 of the .pfx), `WIN_CSC_KEY_PASSWORD` |

EV certificates and cloud signing services usually keep the key in hardware; signing then needs the vendor's
tooling in the workflow instead of a .pfx. Base64 of a file: `base64 -i cert.p12 | pbcopy` (macOS) or
`[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))` (PowerShell).

## Distributing

Put the installers on a download page, for example `https://ofintech.ai/download`, or link to the GitHub release.
Updates: install the new version over the old one; the app keeps its settings. Automatic updates are not built in
yet.

## Development

`DESK_DEFAULT_SERVER=https://desk.ofintech.ai DESK_SERVER_ONLY=1 npm start` runs the cloud client from source.
