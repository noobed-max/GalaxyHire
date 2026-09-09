/**
 * Platform capabilities — the one place the app asks "am I in a browser or the desktop shell?"
 *
 * GalaxyHire is browser-first (ARCHITECTURE.md D3) because the §6 apply flow — open the portal,
 * sync the extension, fill page by page — is native to a browser and awkward to drive from a Tauri
 * window. But the desktop experience is *replicated, not dropped*: every capability below has both
 * a web and a Tauri implementation, and `src-tauri/` stays buildable.
 *
 * Two rules make that work:
 *
 *  1. **Nothing outside this directory imports `@tauri-apps/*`.** Components ask for a capability;
 *     they never ask which shell they're in. Before this existed, twelve files imported Tauri
 *     directly, which meant the browser build pulled in desktop-only modules and every new feature
 *     had to rediscover the same conditionals.
 *  2. **Tauri modules are loaded dynamically.** A static import would put them in the web bundle
 *     and, worse, execute Tauri's IPC bootstrap in a plain browser where `__TAURI_INTERNALS__`
 *     doesn't exist.
 *
 * Adding a capability: extend `Platform`, implement it in both `web.ts` and `tauri.ts`. If a
 * capability genuinely cannot exist on one shell, it returns a documented no-op rather than
 * throwing — a missing auto-updater must not break the page it's rendered on.
 */

export type PlatformKind = 'web' | 'tauri';

/** How the UI finds the API it should talk to. */
export interface BackendEndpoint {
  /** Port the API is listening on. */
  port: number;
  /** Bearer token for HTTP and the WebSocket subprotocol. */
  token: string;
}

export interface UpdateInfo {
  available: boolean;
  version?: string;
  /** Runs the download+install. Absent when no update mechanism exists (web). */
  install?: (onProgress?: (downloaded: number, total: number | null) => void) => Promise<void>;
}

export interface Platform {
  readonly kind: PlatformKind;

  /**
   * Open a URL in the user's browser.
   *
   * Scheme filtering lives in the caller (`openExternalUrl`), not here: lead URLs come from job
   * postings, so a crafted `file:` or `javascript:` URL must be rejected before it reaches either
   * the OS opener or `window.open`.
   */
  openExternal(url: string): Promise<void>;

  /** App version for display. */
  getVersion(): Promise<string | null>;

  /** Best-effort desktop notification. Never throws; notifications are a nicety. */
  notify(title: string, body: string): Promise<void>;

  /** Restart the app. On the web this is a reload. */
  relaunch(): Promise<void>;

  /** Check for an app update. `{available: false}` where no updater exists. */
  checkUpdate(): Promise<UpdateInfo>;

  /**
   * Resolve the API endpoint, and keep resolving it if it changes.
   *
   * The two shells differ most here. Tauri spawns the API as a sidecar on a dynamic port and
   * announces it over IPC events, so discovery is asynchronous and can change mid-session (the
   * sidecar can restart). On the web the API *serves this page*, so the endpoint is same-origin
   * and known immediately.
   *
   * `onChange` is called with each endpoint as it becomes known. Returns an unsubscribe function.
   */
  watchBackend(onChange: (endpoint: BackendEndpoint | null) => void): () => void;

  /** Last sidecar error, where the shell tracks one. */
  getBackendError(): Promise<string | null>;

  /**
   * Ask the user for a directory to save generated resumes into (§6).
   *
   * Tauri opens a native folder picker and returns a real path the backend can write to. Browsers
   * cannot hand out filesystem paths, so the web implementation returns null and the caller falls
   * back to a server-side configured directory plus ordinary downloads.
   */
  pickDirectory(): Promise<string | null>;
}

/**
 * True when running inside the Tauri shell.
 *
 * Tauri v2 injects `__TAURI_INTERNALS__`; v1 used `__TAURI__`. Both are checked so the detection
 * doesn't silently break on an upgrade, and the check is guarded for SSR/test environments where
 * `window` is absent.
 */
export function isTauri(): boolean {
  if (typeof window === 'undefined') return false;
  const w = window as unknown as Record<string, unknown>;
  return '__TAURI_INTERNALS__' in w || '__TAURI__' in w;
}

/**
 * Base URL for HTTP calls to the API.
 *
 * Deliberately **synchronous and outside the `Platform` interface**, because this sits on every
 * single API request. Routing it through `await platform()` put a dynamic import in front of every
 * fetch — which added latency to the hot path and, worse, broke the request timeout and abort
 * handling, since those depend on the fetch starting when the timer does. Choosing a base needs
 * nothing async: it is a string built from a synchronous shell check.
 *
 * Getting it wrong is a subtle cross-origin bug rather than an obvious failure. The desktop shell
 * loads the UI from `tauri://localhost` and must address the sidecar explicitly at
 * `http://127.0.0.1:<port>`. The web shell is *served by* the API, so it must use the page's own
 * origin — hardcoding loopback there makes every call cross-origin whenever the user typed
 * `localhost`, leaving it dependent on CORS to work at all.
 */
export function httpBaseFor(port: number): string {
  if (isTauri()) return `http://127.0.0.1:${port}`;
  if (typeof window === 'undefined' || !window.location?.host) return `http://127.0.0.1:${port}`;
  return `${window.location.protocol}//${window.location.host}`;
}

/** WebSocket base URL. Same reasoning as {@link httpBaseFor}. */
export function wsBaseFor(port: number): string {
  if (isTauri()) return `ws://127.0.0.1:${port}`;
  if (typeof window === 'undefined' || !window.location?.host) return `ws://127.0.0.1:${port}`;
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${window.location.host}`;
}

let cached: Platform | null = null;

/** The active platform. Cached because detection can't change within a page's lifetime. */
export async function platform(): Promise<Platform> {
  if (cached) return cached;
  cached = isTauri() ? (await import('./tauri')).tauriPlatform : (await import('./web')).webPlatform;
  return cached;
}

/** Test seam: force a platform. */
export function __setPlatform(p: Platform | null): void {
  cached = p;
}
