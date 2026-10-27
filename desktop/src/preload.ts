// Runs in the sandboxed renderer before the page. Exposes a minimal, read-only bridge.
import { contextBridge, ipcRenderer } from "electron";

const bridge = ipcRenderer.sendSync("desk:bridge") as { token?: string; version: string; mode: "local" | "remote" };

contextBridge.exposeInMainWorld("desk", {
  token: bridge.token,
  version: bridge.version,
  mode: bridge.mode,
  openExternal: (url: string) => ipcRenderer.send("desk:open", String(url)),
  // Google sign-in in the system browser, handed back to this app (Team Server only)
  googleChallenge: bridge.mode === "remote" ? () => ipcRenderer.sendSync("desk:google-challenge") as string | null : undefined,
});

// Only used by the "Connect to a Team Server" window (a local file).
if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("deskSetup", {
    getConfig: () => ipcRenderer.invoke("desk:get-config"),
    setServer: (url: string | null) => ipcRenderer.invoke("desk:set-server", url),
    retry: () => ipcRenderer.send("desk:retry"),
    connect: () => ipcRenderer.send("desk:connect"),
  });
}
