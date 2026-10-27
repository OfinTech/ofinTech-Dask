// Lists interface strings that have no translation yet, per language.
//   node scripts/i18n.mjs            report missing and unused keys
//   node scripts/i18n.mjs --keys     print every key as JSON (to send to a translator)
// Strings are found as tr("...") and N("...") literals in src/.
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../src");
const keys = new Set();
function walk(dir) {
  for (const f of fs.readdirSync(dir)) {
    const p = path.join(dir, f);
    if (fs.statSync(p).isDirectory()) { if (f !== "i18n") walk(p); continue; }
    if (!/\.(tsx?|mjs)$/.test(f)) continue;
    const src = fs.readFileSync(p, "utf8");
    for (const m of src.matchAll(/\b(?:tr|N)\(\s*("(?:[^"\\]|\\.)*")/g)) keys.add(JSON.parse(m[1]));
  }
}
walk(root);
if (process.argv.includes("--keys")) {
  console.log(JSON.stringify([...keys].sort(), null, 1));
  process.exit(0);
}
let missingTotal = 0;
for (const f of fs.readdirSync(path.join(root, "i18n"))) {
  const src = fs.readFileSync(path.join(root, "i18n", f), "utf8");
  const have = new Set([...src.matchAll(/^\s*("(?:[^"\\]|\\.)*")\s*:/gm)].map((m) => JSON.parse(m[1])));
  const missing = [...keys].filter((k) => !have.has(k));
  const unused = [...have].filter((k) => !keys.has(k));
  missingTotal += missing.length;
  console.log(`${f}: ${have.size - unused.length}/${keys.size} translated, ${missing.length} missing, ${unused.length} unused`);
  for (const k of missing.slice(0, 40)) console.log(`  missing: ${JSON.stringify(k)}`);
}
process.exit(process.argv.includes("--strict") && missingTotal ? 1 : 0);
