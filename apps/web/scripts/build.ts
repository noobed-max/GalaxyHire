/**
 * Production frontend build — pure Bun, no Vite.
 *
 * Pipeline:
 *   1. Tailwind CLI compiles src/index.css (it owns the `@import "tailwindcss"`
 *      and @theme layer) → dist/assets/index.css
 *   2. Bun.build bundles src/main.tsx with code splitting (lazy routes become
 *      their own chunks) → dist/assets/*.js
 *   3. index.html is copied with its script/css hrefs rewritten to the built,
 *      fingerprinted filenames.
 *
 * Replaces scripts/build-frontend.mjs (typecheck + `vite build`). Typechecking
 * stays a separate gate (`npm run typecheck`) so bundling can be parallelized
 * and does not silently depend on tsc's speed.
 */

import { $ } from "bun";
import { rm, mkdir, readFile, writeFile, copyFile, readdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dir, "..");
const DIST = path.join(ROOT, "dist");
const ASSETS = path.join(DIST, "assets");

// Clean only what THIS script owns (assets/ + the prod index.html). A dev
// server may be running against dist/main.js + dist/chunk-*.js; wiping all of
// dist/ would 404 its bundle mid-session (blank-screen footgun).
await rm(ASSETS, { recursive: true, force: true });
await rm(path.join(DIST, "index.html"), { force: true });
await mkdir(ASSETS, { recursive: true });

// 1 ── CSS via the Tailwind CLI -------------------------------------------------
await $`bunx @tailwindcss/cli -i ${path.join(ROOT, "src/index.css")} -o ${path.join(ASSETS, "index.css")} --minify`.quiet();

// 2 ── JS via Bun.build ---------------------------------------------------------
const result = await Bun.build({
  entrypoints: [path.join(ROOT, "src/main.tsx")],
  outdir: ASSETS,
  target: "browser",
  format: "esm",
  // Splitting gives every lazy route its own chunk, mirroring what the old
  // manualChunks config did for react/@tauri-apps vendors.
  splitting: true,
  minify: true,
  naming: { entry: "[name]-[hash].[ext]", chunk: "[name]-[hash].[ext]", asset: "[name]-[hash].[ext]" },
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});

if (!result.success) {
  console.error(result.logs);
  process.exit(1);
}

// 3 ── index.html: rewrite module hrefs to the fingerprinted outputs -----------
const htmlPath = path.join(ROOT, "index.html");
let html = await readFile(htmlPath, "utf8");

const mainEntry = result.outputs.find(o => o.kind === "entry-point");
if (!mainEntry) throw new Error("no entry point emitted");

html = html.replace(
  /<script type="module" src="\/src\/main\.tsx"><\/script>/,
  `<script type="module" src="./assets/${mainEntry.path.split("/").pop()}"></script>`,
);

const cssName = (await readdir(ASSETS)).find(f => f.startsWith("index.") && f.endsWith(".css"));
if (!cssName) throw new Error("tailwind produced no css");
html = html.replace(/(<link rel="stylesheet" crossorigin href=")?$/m, "");
// inject the built stylesheet after the fonts link with cache-busting query
const buildHash = Date.now().toString(36);
html = html.replace(
  /(rel="stylesheet"\s*\/>\s*)/,
  `$1<link rel="stylesheet" href="./assets/${cssName}?v=${buildHash}" />\n    `,
);

// public assets: favicon etc.
for (const pub of ["galaxyhire-logo.png", "galaxyhire-logo-transparent.png"]) {
  const src = path.join(ROOT, "public", pub);
  if (existsSync(src)) await copyFile(src, path.join(DIST, pub));
}

await writeFile(path.join(DIST, "index.html"), html);

const kb = (n: number) => `${(n / 1024).toFixed(1)} kB`;
console.log(`✓ built dist/ — entry ${mainEntry.path.split("/").pop()} (${kb(mainEntry.size)}), css ${cssName}, ${result.outputs.length} outputs`);
