// Security rules of the Electron main process, run under plain Node (no Electron needed): npm test in desktop/.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const g = require("../dist/guards.js");
const LOCAL = "http://localhost:47821";
const TEAM = "https://desk.example.com";

test("the engine token goes only to the app's own origin", () => {
  assert.equal(g.isAppUrl("http://localhost:47821/#/chat", LOCAL), true);
  for (const bad of ["http://localhost:47822/", "http://127.0.0.1:47821/", "https://localhost:47821/", "http://localhost.attacker.test:47821/",
                     "http://attacker.test/#http://localhost:47821", "file:///tmp/x.html", "javascript:alert(1)", "", "not a url"]) {
    assert.equal(g.isAppUrl(bad, LOCAL), false, bad);
  }
  assert.equal(g.isAppUrl("http://localhost:47821/", ""), false, "no origin yet: nothing is the app");
  assert.equal(g.isAppUrl("https://desk.example.com/#/", TEAM), true);
  assert.equal(g.isAppUrl("https://desk.example.com.attacker.test/", TEAM), false);
  assert.equal(g.isAppUrl("https://desk.example.com:8443/", TEAM), false);
});

test("only the bundled setup pages may use the setup bridge", () => {
  assert.equal(g.isSetupPage("file:///Applications/OfinTech%20Desk.app/Contents/Resources/app.asar/static/connect.html"), true);
  assert.equal(g.isSetupPage("file:///C:/Users/x/AppData/Local/Programs/desk/resources/app.asar/static/offline.html?server=https%3A%2F%2Fdesk"), true);
  for (const bad of ["file:///tmp/static/connect.html.attacker", "file:///tmp/evil/connect.html", "https://desk.example.com/static/connect.html",
                     "file:///static/connect.htm", "file:///static/other.html", ""]) {
    assert.equal(g.isSetupPage(bad), false, bad);
  }
});

test("IPC senders: the app origin or a setup page, nothing else", () => {
  assert.equal(g.trustedSender("https://desk.example.com/#/settings", TEAM), true);
  assert.equal(g.trustedSender("file:///x/static/offline.html", TEAM), true);
  assert.equal(g.trustedSender("https://attacker.test/", TEAM), false);
  assert.equal(g.trustedSender(undefined, TEAM), false);
  assert.equal(g.trustedSender(null, TEAM), false);
  assert.equal(g.trustedSender("about:blank", TEAM), false);
});

test("external links: http(s) only", () => {
  assert.equal(g.isOpenableExternally("https://example.com/x"), true);
  assert.equal(g.isOpenableExternally("http://example.com/x"), true);
  for (const bad of ["file:///etc/passwd", "javascript:alert(1)", "smb://share/x", "ofintech-desk://open/x", "mailto:a@b", "data:text/html,<script>", ""]) {
    assert.equal(g.isOpenableExternally(bad), false, bad);
  }
});

test("a Team Server address must be https", () => {
  assert.equal(g.isAcceptableServerUrl("https://desk.example.com"), true);
  for (const bad of ["http://desk.example.com", "https://", "desk.example.com", 42, null, "https:// desk.example.com", "ftp://x"]) {
    assert.equal(g.isAcceptableServerUrl(bad), false, String(bad));
  }
});

test("deep links set a hash route and nothing else", () => {
  assert.equal(g.routeFromDeepLink("ofintech-desk://open/files/f_1?ws=ws_1&cell=Pack!B4"), "#/files/f_1?ws=ws_1&cell=Pack%21B4");
  assert.equal(g.routeFromDeepLink("ofintech-desk://open/chat?ws=ws_1&evil=1"), "#/chat?ws=ws_1");
  for (const bad of ["ofintech-desk://evil/x", "https://open/x", "ofintech-desk://open/../../etc", "ofintech-desk://open/x%00y",
                     "ofintech-desk://open/<script>", "ofintech-desk://open/" + "a".repeat(300), "ofintech-desk://open/",
                     "ofintech-desk://open/x?ws=<img>", "javascript:alert(1)"]) {
    const r = g.routeFromDeepLink(bad);
    assert.ok(r === null || !/[<>"'`%\s]/.test(r.replace(/%2[01]|%21/g, "")), bad + " -> " + r);
    if (bad.startsWith("https") || bad.includes("evil/") || bad.includes("open/<")) assert.equal(r, null, bad);
  }
  // a bad parameter is dropped, the route itself survives
  assert.equal(g.routeFromDeepLink("ofintech-desk://open/x?ws=<img>"), "#/x");
  // ".." is normalised by the URL parser into an ordinary route name; the hash never reaches a file system
  assert.equal(g.routeFromDeepLink("ofintech-desk://open/../../etc"), "#/etc");
});

test("the sign-in handoff accepts only a well-formed one-time code", () => {
  assert.equal(g.handoffCode("ofintech-desk://open/auth?handoff=abcDEF0123456789_-abcdef"), "abcDEF0123456789_-abcdef");
  for (const bad of ["ofintech-desk://open/auth?handoff=short", "ofintech-desk://open/auth?handoff=" + "a".repeat(101),
                     "ofintech-desk://open/auth?handoff=../x", "ofintech-desk://open/auth", "ofintech-desk://open/files?handoff=abcdefghijklmnopqrstuvwxyz",
                     "https://open/auth?handoff=abcdefghijklmnopqrstuvwxyz"]) {
    assert.equal(g.handoffCode(bad), null, bad);
  }
});
