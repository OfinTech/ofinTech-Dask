import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function files(dir) {
  return readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? files(p) : [p]; });
}

test("the only network calls go to the configured endpoint", () => {
  for (const f of files("src")) {
    const src = readFileSync(f, "utf8");
    const code = src.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(!/https?:\/\/[a-z0-9.-]+/i.test(code), `${f} contains a literal URL`);
    assert.ok(!src.includes("XMLHttpRequest"), f);
    assert.ok(!src.includes("sendBeacon"), f);
    assert.ok(!src.includes("<script"), f);
    assert.ok(!src.includes("WebSocket"), f);
    if (!f.endsWith("client.ts")) assert.ok(!/\bfetch\s*\(/.test(src), `${f} calls fetch; only client.ts may`);
  }
  const client = readFileSync("src/client.ts", "utf8");
  const fetches = [...client.matchAll(/this\.fetchFn\(\s*joinUrl\(this\.options\.endpoint/g)];
  const allFetches = [...client.matchAll(/this\.fetchFn\(/g)];
  assert.equal(fetches.length, allFetches.length, "every fetch is rooted at the configured endpoint");
});

test("the package has no runtime dependencies besides React", () => {
  const pkg = JSON.parse(readFileSync("package.json", "utf8"));
  assert.equal(pkg.dependencies, undefined);
  assert.deepEqual(Object.keys(pkg.peerDependencies).sort(), ["react", "react-dom"]);
});
