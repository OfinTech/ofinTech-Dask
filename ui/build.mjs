// Builds the UI into ui/dist with esbuild (no other tooling needed).
//   node build.mjs            production build
//   node build.mjs --watch    rebuild on change
import { build, context } from "esbuild";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";

const watch = process.argv.includes("--watch");
rmSync("dist", { recursive: true, force: true });
mkdirSync("dist/assets", { recursive: true });
cpSync("index.html", "dist/index.html");
cpSync("favicon.svg", "dist/favicon.svg");
cpSync("src/fonts/OFL.txt", "dist/assets/OFL-IBM-Plex-Sans-Arabic.txt");
cpSync("src/fonts/OFL-Inter.txt", "dist/assets/OFL-Inter.txt");

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
  loader: { ".woff": "file", ".woff2": "file" },
  assetNames: "[name]-[hash]",
  // The bundle name has to change when the bundle does. The engine sends a year-long immutable
  // cache-control for hashed asset names, and a browser holding an immutable response does not
  // revalidate it -- so a fixed app.css would pin stale code in every returning browser and no
  // redeploy could reach it. Watch mode keeps plain names for a stable dev loop; the engine
  // recognises those as unhashed and serves them no-cache.
  entryNames: watch ? "[name]" : "[name]-[hash]",
  metafile: !watch,
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
  logLevel: "info",
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
} else {
  const result = await build(options);
  const built = Object.keys(result.metafile.outputs).map((p) => p.replace(/^dist\//, "./"));
  let html = readFileSync("index.html", "utf8");
  for (const ext of ["css", "js"]) {
    const hashed = built.find((p) => new RegExp(`^\\./assets/app-[^/]+\\.${ext}$`).test(p));
    if (!hashed) throw new Error(`the build produced no .${ext} bundle`);
    const plain = `./assets/app.${ext}`;
    if (!html.includes(plain)) throw new Error(`index.html does not reference ${plain}, so it cannot be rewritten`);
    html = html.replaceAll(plain, hashed);
  }
  writeFileSync("dist/index.html", html);
  console.log(`index.html points at ${built.filter((p) => /app-/.test(p)).join(" and ")}`);
}
