// Builds the UI into ui/dist with esbuild (no other tooling needed).
//   node build.mjs            production build
//   node build.mjs --watch    rebuild on change
import { build, context } from "esbuild";
import { cpSync, mkdirSync, rmSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });
cpSync("index.html", "dist/index.html");
cpSync("favicon.svg", "dist/favicon.svg");

const options = {
  entryPoints: { app: "src/main.tsx" },
  outdir: "dist/assets",
  bundle: true,
  format: "esm",
  target: ["chrome120", "safari16", "firefox120"],
  jsx: "automatic",
  minify: !watch,
  sourcemap: watch ? "inline" : false,
  legalComments: "linked",
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  logLevel: "info",
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
} else {
  await build(options);
}
