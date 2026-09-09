/**
 * Dev-mode bundle: fast, unminified, stable filenames.
 *
 * Called by scripts/dev.ts on start and on every source change. Deliberately
 * separate from build.ts so watch rebuilds never pay production costs
 * (minify, fingerprinting, dist wipe).
 */

import path from "node:path";
import { readdir, rm } from "node:fs/promises";

const ROOT = path.resolve(import.meta.dir, "..");
const DIST = path.join(ROOT, "dist");

await Bun.$`mkdir -p ${DIST}`.quiet();

// Previous dev outputs are fully regenerated below; clearing them first keeps
// stale hashed chunks from piling up in dist/.
for (const f of await readdir(DIST)) {
  if (/^(chunk-.+\.js|main\.js|index\.css)$/.test(f)) {
    await rm(path.join(DIST, f), { force: true });
  }
}

const result = await Bun.build({
  entrypoints: [path.join(ROOT, "src/main.tsx")],
  outdir: DIST,
  target: "browser",
  format: "esm",
  splitting: true,
  minify: false,
  // React's CJS builds branch on NODE_ENV; without this define the literal
  // `process.env.NODE_ENV` would reach browsers that have no `process`.
  define: { "process.env.NODE_ENV": JSON.stringify("development") },
  naming: { entry: "[name].[ext]", chunk: "chunk-[name]-[hash].[ext]", asset: "[name]-[hash].[ext]" },
});

if (!result.success) {
  console.error(result.logs);
  process.exit(1);
}

await Bun.$`bunx @tailwindcss/cli -i ${path.join(ROOT, "src/index.css")} -o ${DIST}/index.css`.quiet();
