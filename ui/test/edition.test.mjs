// The hooks an edition uses to change the core's pages without patching them (no browser, no engine):
//   node --experimental-strip-types --test "test/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { editionName, extendEdition, filesTop } from "../src/lib/edition.ts";

const A = { id: "w_a", name: "Invented A", role: "editor" };
const B = { id: "w_b", name: "Invented B", role: "viewer" };

test("with no edition, every name is the core's and nothing is drawn above the folders", () => {
  assert.equal(editionName("files", A), null);
  assert.equal(filesTop(), null);
});

test("an edition names an entry per workspace, and gives the component drawn above the folders", () => {
  function Drawn() { return null; }
  extendEdition({
    navLabel: (key, ws) => (key === "files" && ws.id === "w_a" ? "Renamed here" : null),
    filesTop: Drawn,
  });
  assert.equal(editionName("files", A), "Renamed here");
  assert.equal(editionName("files", B), null);               // another workspace keeps the core's name
  assert.equal(editionName("documents", A), null);           // and so does every other entry
  assert.equal(filesTop(), Drawn);
});

test("a hook given again replaces the earlier one", () => {
  extendEdition({ navLabel: () => "Second" });
  assert.equal(editionName("files", B), "Second");
});
