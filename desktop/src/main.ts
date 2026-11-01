// OfinTech Desk — Electron main process.
//
// Local mode: starts the bundled Python engine on 127.0.0.1, passes it a per-launch token and the
// master key (kept encrypted with the operating system's secure storage), and shows the UI the
// engine serves. Team mode: shows the Team Server's UI instead (the user signs in there).
//
// Hardening: context isolation, sandboxed renderer, no Node in the page, navigation pinned to the
// app's own origin, new windows and external links opened in the system browser (http/https only),
// all permission requests denied.

import { app, BrowserWindow, dialog, ipcMain, Menu, powerMonitor, safeStorage, shell, type MenuItemConstructorOptions } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync, createWriteStream } from "node:fs";
import { join } from "node:path";
import { PROTOCOL, handoffCode, isAcceptableServerUrl, isAppUrl as appUrlOf, isOpenableExternally, isSetupPage, routeFromDeepLink, trustedSender as senderOk } from "./guards";

type Config = { mode: "local" | "remote"; serverUrl?: string; serverOnly?: boolean };

/** Build edition, set by electron-builder (extraMetadata.deskEdition) for branded builds:
 *  defaultServer — the Team Server the app opens on first start (e.g. https://desk.ofintech.ai);
 *  serverOnly    — no local engine in this build: the app is a client of the Team Server. */
type Edition = { defaultServer?: string; serverOnly?: boolean };
function edition(): Edition {
  try {
    const pkg = JSON.parse(readFileSync(join(app.getAppPath(), "package.json"), "utf8"));
    const e = pkg.deskEdition || {};
    const env = process.env.DESK_DEFAULT_SERVER;  // development override
    const server = env || e.defaultServer;
    return { defaultServer: typeof server === "string" && /^https:\/\/[^\s/]+$/.test(server.replace(/\/+$/, "")) ? server.replace(/\/+$/, "") : undefined,
             serverOnly: !!(e.serverOnly || process.env.DESK_SERVER_ONLY === "1") };
  } catch {
    return {};
  }
}
const EDITION = edition();

const DEFAULT_PORT = 47821;
let engine: ChildProcess | null = null;
let mainWindow: BrowserWindow | null = null;
let localToken = "";
let appOrigin = "";
let warnedBasic = false;

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

// ---- links from Excel and Word files: ofintech-desk://open/<app path>?ws=<workspace> ---------------------
// The path is only ever used as the hash route of the app's own page; nothing in it is executed or fetched.
let pendingDeepLink: string | null = null;
if (process.defaultApp && process.argv.length >= 2) {
  app.setAsDefaultProtocolClient(PROTOCOL, process.execPath, [process.argv[1]]);
} else {
  app.setAsDefaultProtocolClient(PROTOCOL);
}


// Google sign-in from the app: Google does not allow sign-in inside embedded browsers, so it runs in the system
// browser. The page asks for a challenge (the app keeps the verifier); the browser returns a one-time code through
// ofintech-desk://open/auth?handoff=…; the app redeems it in its own window with the verifier.
let handoffVerifier = "";

function handoff(raw: string): boolean {
  const code = handoffCode(raw);
  if (code === null) {
    try { const u = new URL(raw); return u.protocol === `${PROTOCOL}:` && u.hostname === "open" && u.pathname.replace(/^\/+/, "") === "auth"; } catch { return false; }
  }
  const config = readConfig();
  if (!handoffVerifier || config.mode !== "remote" || !config.serverUrl || !mainWindow) return true;
  const target = new URL("/api/auth/desktop-handoff", config.serverUrl);
  target.searchParams.set("code", code);
  target.searchParams.set("verifier", handoffVerifier);
  handoffVerifier = "";
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
  void mainWindow.loadURL(target.toString());
  return true;
}

function openDeepLink(raw: string): void {
  if (handoff(raw)) return;
  const route = routeFromDeepLink(raw);
  if (!route) return;
  if (!mainWindow || !appOrigin) { pendingDeepLink = raw; return; }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
  void mainWindow.webContents.executeJavaScript(`location.hash = ${JSON.stringify(route)}`);
}

app.on("open-url", (event, url) => { event.preventDefault(); openDeepLink(url); });  // macOS
pendingDeepLink = process.argv.find((a) => a.startsWith(`${PROTOCOL}://`)) || null;  // Windows / Linux first start

// ---- configuration ----------------------------------------------------------------------------

function configPath(): string {
  return join(app.getPath("userData"), "config.json");
}

function readConfig(): Config {
  try {
    const c = JSON.parse(readFileSync(configPath(), "utf8"));
    if (c.mode === "remote" && typeof c.serverUrl === "string" && /^https:\/\//.test(c.serverUrl)) {
      return { ...c, serverOnly: !!EDITION.serverOnly };
    }
  } catch { /* first start */ }
  if (EDITION.defaultServer) return { mode: "remote", serverUrl: EDITION.defaultServer, serverOnly: !!EDITION.serverOnly };
  return { mode: "local" };
}

function writeConfig(c: Config): void {
  writeFileSync(configPath(), JSON.stringify(c, null, 2), { mode: 0o600 });
}

/** 32 random bytes, stored encrypted by the OS (Keychain, DPAPI, libsecret/kwallet). */
function masterKey(): string {
  const file = join(app.getPath("userData"), "master.key");
  if (!safeStorage.isEncryptionAvailable() && process.platform === "linux") {
    // No keyring (e.g. a minimal desktop): fall back to Electron's built-in key and warn the user.
    safeStorage.setUsePlainTextEncryption(true);
    if (!warnedBasic && !process.env.DESK_SMOKE_OUT) {
      warnedBasic = true;
      void dialog.showMessageBox({ type: "warning", title: "OfinTech Desk",
        message: "No system keyring was found, so stored credentials are protected only by file permissions. Install and "
          + "unlock GNOME Keyring or KWallet for stronger protection." });
    }
  }
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("The operating system's secure storage is not available, so OfinTech Desk cannot protect your "
      + "credentials.");
  }
  if (existsSync(file)) {
    return safeStorage.decryptString(readFileSync(file));
  }
  const key = randomBytes(32).toString("base64url");
  writeFileSync(file, safeStorage.encryptString(key), { mode: 0o600 });
  return key;
}

// ---- engine -----------------------------------------------------------------------------------

function engineCommand(): { cmd: string; args: string[]; cwd: string; uiDir: string } {
  if (app.isPackaged) {
    const dir = join(process.resourcesPath, "engine");
    const exe = process.platform === "win32" ? "desk-engine.exe" : "desk-engine";
    return { cmd: join(dir, exe), args: ["serve"], cwd: dir, uiDir: join(process.resourcesPath, "ui") };
  }
  const root = join(__dirname, "..", "..");
  return {
    cmd: process.env.DESK_ENGINE_PYTHON || (process.platform === "win32" ? "python" : "python3"),
    args: ["-m", "desk_engine", "serve"],
    cwd: join(root, "engine"),
    uiDir: join(root, "ui", "dist"),
  };
}

function startEngine(): Promise<number> {
  const { cmd, args, cwd, uiDir } = engineCommand();
  const dataDir = join(app.getPath("userData"), "data");
  mkdirSync(dataDir, { recursive: true });
  const logFile = join(app.getPath("logs"), "engine.log");
  mkdirSync(app.getPath("logs"), { recursive: true });
  const log = createWriteStream(logFile, { flags: "a" });
  localToken = randomBytes(32).toString("base64url");

  const env: NodeJS.ProcessEnv = {
    PATH: process.env.PATH, HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE, SYSTEMROOT: process.env.SYSTEMROOT,
    TEMP: process.env.TEMP, TMP: process.env.TMP, TMPDIR: process.env.TMPDIR, LANG: process.env.LANG,
    APPDATA: process.env.APPDATA, LOCALAPPDATA: process.env.LOCALAPPDATA,
    DESK_MODE: "desktop",
    DESK_DATA_DIR: dataDir,
    DESK_UI_DIR: uiDir,
    DESK_PORT: String(DEFAULT_PORT),
    DESK_LOCAL_TOKEN: localToken,
    DESK_MASTER_KEY: masterKey(),
    // OAuth client IDs shipped with a build (optional) are passed through
    ...Object.fromEntries(Object.entries(process.env).filter(([k]) => /^DESK_(QUICKBOOKS|ZOHO_BOOKS|XERO|GOOGLE)_CLIENT_/.test(k))),
  };

  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    engine = child;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error(`The engine did not start within 60 seconds. Log: ${logFile}`)); }
    }, 60_000);
    child.stdout?.on("data", (buf: Buffer) => {
      const text = buf.toString();
      log.write(text);
      const m = /DESK_READY (\d+)/.exec(text);
      if (m && !settled) { settled = true; clearTimeout(timer); resolve(Number(m[1])); }
    });
    child.stderr?.on("data", (buf: Buffer) => log.write(buf));
    child.on("exit", (code) => {
      log.write(`\n[engine exited with code ${code}]\n`);
      if (!settled) { settled = true; clearTimeout(timer); reject(new Error(`The engine stopped (code ${code}). Log: ${logFile}`)); }
      else if (!quitting) {
        dialog.showErrorBox("OfinTech Desk", `The engine stopped unexpectedly (code ${code}). Restart the app. Log: ${logFile}`);
      }
    });
    child.on("error", (err) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(err); }
    });
  });
}

let quitting = false;
function stopEngine(): void {
  quitting = true;
  if (engine && engine.exitCode === null) {
    engine.kill("SIGTERM");
    setTimeout(() => { if (engine && engine.exitCode === null) engine.kill("SIGKILL"); }, 4000);
  }
}

// ---- windows ---------------------------------------------------------------------------------------

function isAppUrl(url: string): boolean {
  return appUrlOf(url, appOrigin);
}

function openSafe(url: string): void {
  if (isOpenableExternally(url)) void shell.openExternal(new URL(url).toString());
}

function createWindow(url: string): BrowserWindow {
  const win = new BrowserWindow({
    width: 1360, height: 880, minWidth: 1024, minHeight: 680, show: false, title: "OfinTech Desk",
    backgroundColor: "#f6f7f5",
    webPreferences: {
      preload: join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: true,
    },
  });
  win.once("ready-to-show", () => win.show());
  // CI smoke test: DESK_SMOKE_OUT=<dir> saves a screenshot and what the page sees, then quits.
  const smoke = process.env.DESK_SMOKE_OUT;
  if (smoke) {
    win.webContents.once("did-finish-load", () => setTimeout(async () => {
      const seen = await win.webContents.executeJavaScript(
        "({ url: location.href, token: !!(window.desk && window.desk.token), node: typeof require, text: document.body.innerText.slice(0, 300) })");
      writeFileSync(join(smoke, "smoke.json"), JSON.stringify(seen, null, 2));
      writeFileSync(join(smoke, "smoke.png"), (await win.webContents.capturePage()).toPNG());
      app.quit();
    }, 4000));
  }
  win.webContents.setWindowOpenHandler(({ url: target }) => { openSafe(target); return { action: "deny" }; });
  win.webContents.on("will-navigate", (event, target) => {
    if (!isAppUrl(target)) { event.preventDefault(); openSafe(target); }
  });
  win.webContents.session.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  void win.loadURL(url);
  return win;
}

function connectWindow(): void {
  const win = new BrowserWindow({
    width: 520, height: 420, resizable: false, title: "Connect to a Team Server", parent: mainWindow ?? undefined, modal: !!mainWindow,
    webPreferences: { preload: join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  win.setMenu(null);
  void win.loadFile(join(__dirname, "..", "static", "connect.html"));
}

// ---- IPC (the page can only ask for these few things) ------------------------------------------------
// Every channel checks which frame is asking: the app's own origin, or the two bundled local pages. A frame
// from anywhere else (a navigation that slipped through, an iframe) gets nothing.


function trustedSender(event: { senderFrame?: { url: string } | null }): boolean {
  return senderOk(event.senderFrame?.url, appOrigin);
}

ipcMain.on("desk:bridge", (event) => {
  const url = event.senderFrame?.url || "";
  const local = appOrigin.startsWith("http://localhost") && isAppUrl(url);
  event.returnValue = { token: local ? localToken : undefined, version: app.getVersion(), mode: readConfig().mode };
});

ipcMain.on("desk:open", (event, url: unknown) => {
  if (trustedSender(event) && typeof url === "string") openSafe(url);
});

ipcMain.handle("desk:set-server", async (event, url: unknown) => {
  if (!isSetupPage(event.senderFrame?.url || "")) return { ok: false, error: "Not allowed from this page." };
  if (url === null && EDITION.serverOnly) return { ok: false, error: "This edition works with a Team Server only." };
  if (url === null) {
    writeConfig({ mode: "local" });
  } else {
    if (!isAcceptableServerUrl(url)) return { ok: false, error: "Enter an https:// address." };
    try {
      const resp = await fetch(new URL("/health", url).toString());
      const body = await resp.json();
      if (body.mode !== "server") return { ok: false, error: "That address is not an OfinTech Desk Team Server." };
    } catch {
      return { ok: false, error: "The server could not be reached." };
    }
    writeConfig({ mode: "remote", serverUrl: url.replace(/\/+$/, "") });
  }
  app.relaunch();
  app.exit(0);
  return { ok: true };
});

ipcMain.handle("desk:get-config", (event) => (isSetupPage(event.senderFrame?.url || "") ? readConfig() : null));

ipcMain.on("desk:google-challenge", (event) => {
  const config = readConfig();
  if (config.mode !== "remote" || !isAppUrl(event.senderFrame?.url || "")) { event.returnValue = null; return; }
  handoffVerifier = randomBytes(48).toString("base64url");
  event.returnValue = createHash("sha256").update(handoffVerifier).digest("base64url");
});

ipcMain.on("desk:connect", (event) => { if (trustedSender(event)) connectWindow(); });

ipcMain.on("desk:retry", (event) => {
  if (!isSetupPage(event.senderFrame?.url || "")) return;
  const config = readConfig();
  if (mainWindow && config.serverUrl) void mainWindow.loadURL(config.serverUrl);
});

// ---- menu ------------------------------------------------------------------------------------------

function buildMenu(): void {
  const config = readConfig();
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" as const }] : []),
    {
      label: "File",
      submenu: [
        { label: config.mode === "remote" ? `Team Server: ${config.serverUrl}` : "Working on this computer", enabled: false },
        { label: "Connect to a Team Server…", click: connectWindow },
        ...(config.mode === "remote" && !config.serverOnly ? [{ label: "Work on this computer instead", click: () => {
          writeConfig({ mode: "local" }); app.relaunch(); app.exit(0);
        } }] : []),
        { type: "separator" },
        { label: "Open data folder", click: () => void shell.openPath(app.getPath("userData")) },
        { label: "Open logs", click: () => void shell.openPath(app.getPath("logs")) },
        { type: "separator" },
        process.platform === "darwin" ? { role: "close" } : { role: "quit" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---- lifecycle ----------------------------------------------------------------------------------------

app.on("second-instance", (_event, argv) => {
  const link = argv.find((a) => a.startsWith(`${PROTOCOL}://`));
  if (link) { openDeepLink(link); return; }
  if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }
});

app.on("web-contents-created", (_e, contents) => {
  contents.on("will-attach-webview", (event) => event.preventDefault());
});

app.whenReady().then(async () => {
  // The computer is locked, put to sleep or left: the workspace is hidden at once (the page locks itself after
  // its inactivity window too; this makes the two moments the same). On resume the page asks the engine or the
  // server how much of the session is really left instead of trusting a clock that stood still.
  for (const event of ["lock-screen", "suspend", "shutdown"] as const) {
    powerMonitor.on(event, () => { for (const w of BrowserWindow.getAllWindows()) w.webContents.send("desk:lock", event); });
  }
  powerMonitor.on("resume", () => { for (const w of BrowserWindow.getAllWindows()) w.webContents.send("desk:resume"); });
  buildMenu();
  const config = readConfig();
  try {
    if (config.mode === "remote" && config.serverUrl) {
      appOrigin = new URL(config.serverUrl).origin;
      mainWindow = createWindow(config.serverUrl);
      const win = mainWindow;
      // no connection: show a page with "Try again" instead of a blank window
      win.webContents.on("did-fail-load", (_e, code, _desc, url, isMainFrame) => {
        if (isMainFrame && code !== -3 && isAppUrl(url)) {
          void win.loadFile(join(__dirname, "..", "static", "offline.html"), { query: { server: config.serverUrl || "" } });
        }
      });
    } else {
      const port = await startEngine();
      appOrigin = `http://localhost:${port}`;
      mainWindow = createWindow(`${appOrigin}/`);
    }
    if (pendingDeepLink) {
      const link = pendingDeepLink;
      pendingDeepLink = null;
      mainWindow.webContents.once("did-finish-load", () => openDeepLink(link));
    }
  } catch (err) {
    dialog.showErrorBox("OfinTech Desk could not start", err instanceof Error ? err.message : String(err));
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopEngine();
  app.quit();
});

app.on("before-quit", stopEngine);
