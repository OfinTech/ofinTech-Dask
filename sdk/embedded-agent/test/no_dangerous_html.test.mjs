import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

function files(dir) {
  return readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? files(p) : [p]; });
}

test("the SDK never renders HTML from data", () => {
  for (const f of files("src")) {
    const src = readFileSync(f, "utf8");
    assert.ok(!src.includes("dangerouslySetInnerHTML"), f);
    assert.ok(!src.includes("innerHTML"), f);
    assert.ok(!/\beval\s*\(/.test(src), f);
    assert.ok(!/new Function\s*\(/.test(src), f);
    assert.ok(!src.includes("document.write"), f);
  }
});

test("links come only from the host (onOpenSource) or an https Desk approval URL", () => {
  const comps = readFileSync("src/components/index.tsx", "utf8");
  const hrefs = [...comps.matchAll(/href=\{([^}]+)\}/g)].map((m) => m[1]);
  assert.deepEqual(hrefs, ["props.approval_url"]);
  assert.ok(comps.includes('/^https:\\/\\//.test(props.approval_url)'));
  assert.ok(comps.includes('rel="noopener noreferrer"'));
});
