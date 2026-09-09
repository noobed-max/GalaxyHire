/**
 * Dev server — pure Bun, no Vite.
 *
 * Replaces the roles vite.config.ts played:
 *   • serves the built frontend (scripts/build-dev.ts output) from dist/
 *   • rebuilds + live-reloads on src/** changes (full reload over SSE)
 *   • proxies /api, /bootstrap, /health to the backend on :8000
 *   • tunnels /ws as a REAL websocket (client ⇄ backend frame passthrough)
 *   • binds Tauri's fixed port 1420; TAURI_DEV_HOST opens it to the network
 */

import path from "node:path";
import fs from "node:fs";

const ROOT = path.resolve(import.meta.dir, "..");
const PORT = Number(process.env.PORT ?? 1420);
const HOST = process.env.TAURI_DEV_HOST ?? "127.0.0.1";

// Resolve the backend once at startup. Explicit API_ORIGIN wins; otherwise
// probe the two ports the stack actually uses (:8080 canonical, :8000 legacy)
// because another local service squatting the wrong port would turn every
// /api call into "failed to load" from the UI.
async function resolveApiOrigin(): Promise<string> {
  if (process.env.API_ORIGIN) return process.env.API_ORIGIN;
  for (const candidate of ["http://127.0.0.1:8080", "http://127.0.0.1:8000"]) {
    try {
      const res = await fetch(`${candidate}/health`, { signal: AbortSignal.timeout(1500) });
      if (res.ok) return candidate;
    } catch { /* try next */ }
  }
  return "http://127.0.0.1:8080";
}

const API = await resolveApiOrigin();
const WS_API = API.replace(/^http/, "ws");

const PROXIED_PREFIXES = ["/api", "/bootstrap", "/health"];
const DIST = path.join(ROOT, "dist");

const MIME: Record<string, string> = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".svg": "image/svg+xml",
  ".html": "text/html",
  ".map": "application/json",
};

function bundle(): void {
  const proc = Bun.spawnSync({
    cmd: ["bun", path.join(ROOT, "scripts/build-dev.ts")],
    cwd: ROOT,
    stdout: "inherit",
    stderr: "inherit",
  });
  if (proc.exitCode !== 0) throw new Error(`dev build failed (${proc.exitCode})`);
}

// Initial bundle must exist before the first request; later rebuilds run out
// of band so a recompile never stalls request serving.
bundle();

let rebuilding = false;
let queued = false;
function rebuildAsync(): void {
  if (rebuilding) { queued = true; return; }
  rebuilding = true;
  const proc = Bun.spawn({
    cmd: ["bun", path.join(ROOT, "scripts/build-dev.ts")],
    cwd: ROOT,
    stdout: "inherit",
    stderr: "inherit",
  });
  proc.exited.then(code => {
    rebuilding = false;
    if (code !== 0) console.error(`dev build failed (${code})`);
    if (queued) { queued = false; rebuildAsync(); }
    else {
      for (const c of [...reloadClients]) {
        try { c.enqueue(encoder.encode("data: reload\n\n")); } catch { reloadClients.delete(c); }
      }
      console.log("↻ rebuilt");
    }
  });
}

// ── live reload (SSE): full page reload after each rebuild ──────────────────
const reloadClients = new Set<ReadableStreamDefaultController<Uint8Array>>();
const encoder = new TextEncoder();

function sseHandler(): Response {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream({
    start(c) {
      controller = c;
      reloadClients.add(c);
      c.enqueue(encoder.encode("data: hello\n\n"));
    },
    cancel() {
      reloadClients.delete(controller);
    },
  });
  return new Response(stream, {
    headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
  });
}

let debounce: ReturnType<typeof setTimeout> | null = null;
fs.watch(path.join(ROOT, "src"), { recursive: true }, () => {
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(() => {
    try {
      rebuildAsync();
    } catch (err) {
      // build errors surface in the console; the browser keeps the last good bundle
      console.error(String(err));
    }
  }, 120);
});

// ── dev index.html (rewritten entry + reload hook) ──────────────────────────
const DEV_HTML = (() => {
  let raw = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
  raw = raw.replace(
    "</head>",
    `<script>new EventSource("/__reload").addEventListener("message", e => { if (e.data === "reload") location.reload(); });</script></head>`,
  );
  raw = raw.replace(
    /<script type="module" src="\/src\/main\.tsx"><\/script>/,
    `<script type="module" src="./dist/main.js"></script>`,
  );
  raw = raw.replace("</head>", `  <link rel="stylesheet" href="./dist/index.css" />\n  </head>`);
  return raw;
})();

async function proxy(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const target = new URL(url.pathname + url.search, API);
  const upstream = await fetch(target, {
    method: req.method,
    headers: req.headers,
    body: ["GET", "HEAD"].includes(req.method) ? undefined : req.body,
    // @ts-expect-error bun-specific: stream request bodies through
    duplex: "half",
  });
  const headers = new Headers(upstream.headers);
  headers.delete("content-encoding");
  headers.delete("content-length");
  headers.delete("transfer-encoding");
  return new Response(upstream.body, { status: upstream.status, headers });
}

Bun.serve({
  port: PORT,
  hostname: HOST,

  async fetch(req, server) {
    const url = new URL(req.url);

    if (url.pathname === "/__reload") return sseHandler();

    // pipeline live updates ride a websocket; relay frames both ways.
    if (url.pathname === "/ws" && req.headers.get("upgrade")?.toLowerCase() === "websocket") {
      const upstream = new WebSocket(`${WS_API}/ws`);
      const ok = server.upgrade(req, { data: { upstream } });
      if (!ok) upstream.close();
      return undefined;
    }

    if (PROXIED_PREFIXES.some(p => url.pathname === p || url.pathname.startsWith(`${p}/`))) {
      return proxy(req);
    }

    // dev shell always wins over any stale production index.html in dist/
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(DEV_HTML, {
        headers: { "content-type": "text/html", "cache-control": "no-cache" },
      });
    }

    const candidate = path.join(ROOT, path.normalize(url.pathname).replace(/^\.\.(\/|\\)/, ""));
    if (!fs.existsSync(candidate)) {
      return new Response("not found", { status: 404 });
    }
    return new Response(fs.readFileSync(candidate), {
      headers: {
        "content-type": MIME[path.extname(candidate)] ?? "application/octet-stream",
        // hashed chunk names change every save; never let the browser pin a
        // bundle that imports chunk filenames that no longer exist
        "cache-control": "no-cache",
      },
    });
  },

  websocket: {
    open(ws) {
      const upstream = (ws.data as { upstream: WebSocket }).upstream;
      upstream.onmessage = event => {
        // strings and ArrayBuffers both pass through untouched
        ws.send(event.data as string | ArrayBufferLike);
      };
      upstream.onclose = () => { try { ws.close(); } catch {} };
      upstream.onerror = () => { try { ws.close(); } catch {} };
    },
    message(ws, msg) {
      const upstream = (ws.data as { upstream: WebSocket }).upstream;
      if (upstream.readyState === WebSocket.OPEN) upstream.send(msg as string | ArrayBufferLike);
    },
    close(ws) {
      const upstream = (ws.data as { upstream: WebSocket }).upstream;
      try { upstream.close(); } catch {}
    },
  },
});

console.log(`▲ dev server on http://${HOST}:${PORT}  (api → ${API})`);
