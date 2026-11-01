// Builds dist/index.js (ESM, React as a peer) and copies the stylesheet. No network, no other tooling.
import { build } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist", { recursive: true });
cpSync("src/styles.css", "dist/styles.css");

await build({
  entryPoints: { index: "src/index.ts" },
  outdir: "dist",
  bundle: true,
  format: "esm",
  platform: "browser",
  target: ["chrome120", "safari16", "firefox120"],
  jsx: "automatic",
  external: ["react", "react-dom", "react/jsx-runtime"],
  minify: false,
  sourcemap: true,
  legalComments: "linked",
  logLevel: "info",
});
