// The partner's backend: holds the integration key (server-side only), mints a short-lived session token per signed-in
// end user, serves the static page. Nothing else talks to OfinTech Desk from here.
import { createServer } from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const DESK_URL = process.env.DESK_URL ?? "https://desk.example.com";          // your OfinTech Desk Team Server
const INTEGRATION_KEY = process.env.OFINTECH_INTEGRATION_KEY ?? "";            // oei_… from Desk → Settings → Embedded
const PORT = Number(process.env.PORT ?? 5173);
if (!INTEGRATION_KEY) { console.error("Set OFINTECH_INTEGRATION_KEY (never ship it to the browser)."); process.exit(1); }

// Replace with your own session handling: the subject must be the end user's id in *your* system.
function currentUser(req) {
  const cookie = req.headers.cookie ?? "";
  const m = /demo_user=([a-zA-Z0-9_-]{1,64})/.exec(cookie);
  return m ? { id: m[1], locale: "ar" } : null;
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (req.method === "POST" && url.pathname === "/api/ofintech/session") {
    const user = currentUser(req);
    if (!user) { res.writeHead(401).end("sign in first"); return; }
    const r = await fetch(`${DESK_URL}/api/embedded/sessions`, {
      method: "POST",
      headers: { authorization: `Bearer ${INTEGRATION_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({ subject: user.id, locale: user.locale, ttl_seconds: 900 }),
    });
    const body = await r.text();
    res.writeHead(r.status, { "content-type": "application/json" }).end(body);   // {token, expires_at, agents, ...}
    return;
  }
  const file = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  const path = join("public", file);
  if (existsSync(path)) {
    const type = file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : "text/html";
    res.writeHead(200, { "content-type": type, "content-security-policy": `default-src 'self'; connect-src 'self' ${DESK_URL}; style-src 'self' 'unsafe-inline'`, "x-frame-options": "DENY" });
    res.end(readFileSync(path));
    return;
  }
  res.writeHead(404).end();
}).listen(PORT, () => console.log(`example on http://localhost:${PORT} → ${DESK_URL}`));
