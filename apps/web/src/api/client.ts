import type { ApiFetch } from "./types";
import { httpBaseFor } from "../shared/lib/platform";

const DEFAULT_TIMEOUT_MS = 30000;

export function isAbortLikeError(error: unknown) {
  if (error instanceof DOMException && error.name === "AbortError") return true;
  const message = error instanceof Error ? error.message : String(error);
  return message.toLowerCase().includes("signal is aborted") || message.toLowerCase().includes("aborted");
}

export function createApiFetch(port: number, token: string): ApiFetch {
  return (path, opts) => {
    const headers = new Headers(opts?.headers);
    headers.set("Authorization", `Bearer ${token}`);
    const controller = new AbortController();
    const abort = () => controller.abort(new DOMException("Request cancelled", "AbortError"));
    const timeoutMs = Math.max(0, opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const timeoutId = timeoutMs > 0
      ? window.setTimeout(() => controller.abort(new DOMException("Request timed out", "TimeoutError")), timeoutMs)
      : null;
    if (opts?.signal) {
      if (opts.signal.aborted) controller.abort(new DOMException("Request cancelled", "AbortError"));
      else opts.signal.addEventListener("abort", abort, { once: true });
    }
    // Base resolved synchronously: the desktop shell addresses the sidecar at 127.0.0.1, the web
    // shell is served BY the API and stays same-origin. Must not be async — the timeout below is
    // armed already, so anything awaited here would eat into the request's own budget.
    return fetch(`${httpBaseFor(port)}${path}`, { ...opts, headers, signal: controller.signal })
      .catch(error => {
        if (controller.signal.aborted || isAbortLikeError(error)) {
          const reason = controller.signal.reason;
          if (reason instanceof DOMException && reason.name === "TimeoutError") {
            throw new Error(`Local backend timed out after ${Math.round(timeoutMs / 1000)}s on port ${port}.`);
          }
          throw new DOMException("Request cancelled", "AbortError");
        }
        const message = error instanceof Error ? error.message : String(error);
        if (message === "Failed to fetch" || message.includes("NetworkError")) {
          throw new Error(`Local backend is unreachable on port ${port}. The sidecar may have restarted; wait a moment or restart the app if this stays.`);
        }
        throw error;
      })
      .finally(() => {
        if (timeoutId !== null) window.clearTimeout(timeoutId);
        opts?.signal?.removeEventListener("abort", abort);
      });
  };
}
