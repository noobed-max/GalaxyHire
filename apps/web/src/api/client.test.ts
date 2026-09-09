import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { createApiFetch, isAbortLikeError } from "./client";

/* Bun's test runner has no `vi.useFakeTimers`, so the timeout test drives a
 * hand-rolled clock: `window.setTimeout` is stubbed with a controllable
 * implementation and the test fires it explicitly. */
function makeClock() {
  const pending = new Map<number, () => void>();
  let nextId = 1;
  const setTimeout = (fn: () => void) => {
    const id = nextId++;
    pending.set(id, fn);
    return id;
  };
  const clearTimeout = (id: number) => { pending.delete(id); };
  const fireAll = () => {
    const fns = [...pending.values()];
    pending.clear();
    for (const fn of fns) fn();
  };
  return { setTimeout, clearTimeout, fireAll };
}

const originalFetch = globalThis.fetch;
let restoreGlobals: (() => void) | null = null;

describe("createApiFetch", () => {
  beforeEach(() => {
    // client.ts resolves timers off `window`; Bun has none, so provide one
    // backed by real timers (the timeout test swaps in a manual clock).
    (globalThis as any).window = { setTimeout, clearTimeout };
  });

  afterEach(() => {
    restoreGlobals?.();
    restoreGlobals = null;
    delete (globalThis as any).window;
    globalThis.fetch = originalFetch;
  });

  it("adds the bearer token to every request", async () => {
    const calls: Array<[string, RequestInit]> = [];
    globalThis.fetch = (async (url: any, init?: RequestInit) => {
      calls.push([String(url), init ?? {}]);
      return new Response("ok");
    }) as typeof fetch;
    await createApiFetch(4567, "secret")("/api/v1/leads");
    const headers = new Headers(calls[0][1].headers);
    expect(calls[0][0]).toBe("http://127.0.0.1:4567/api/v1/leads");
    expect(headers.get("Authorization")).toBe("Bearer secret");
  });

  it("preserves caller headers", async () => {
    const calls: Array<[string, RequestInit]> = [];
    globalThis.fetch = (async (url: any, init?: RequestInit) => {
      calls.push([String(url), init ?? {}]);
      return new Response("ok");
    }) as typeof fetch;
    await createApiFetch(4567, "secret")("/x", { headers: { "x-request-id": "abc" } });
    const headers = new Headers(calls[0][1].headers);
    expect(headers.get("x-request-id")).toBe("abc");
  });

  it("formats backend unreachable errors", async () => {
    globalThis.fetch = (async () => { throw new Error("Failed to fetch"); }) as unknown as typeof fetch;
    await expect(createApiFetch(4567, "secret")("/x")).rejects.toThrow("Local backend is unreachable");
  });

  it("formats timeout errors", async () => {
    const clock = makeClock();
    // The client resolves `window.setTimeout` at call time; Bun has no window,
    // so install the stub before the request is issued.
    (globalThis as any).window = { setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout };
    restoreGlobals = () => { delete (globalThis as any).window; };
    globalThis.fetch = (async (_url: string, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject((init.signal as AbortSignal).reason));
      })) as unknown as typeof fetch;
    const request = createApiFetch(4567, "secret")("/x", { timeoutMs: 50 });
    clock.fireAll();
    await expect(request).rejects.toThrow("timed out");
  });

  it("converts caller aborts to AbortError", async () => {
    globalThis.fetch = (async (_url: string, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject((init.signal as AbortSignal).reason));
      })) as unknown as typeof fetch;
    const controller = new AbortController();
    const request = createApiFetch(4567, "secret")("/x", { signal: controller.signal });
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("isAbortLikeError", () => {
  it("recognizes DOM aborts", () => {
    expect(isAbortLikeError(new DOMException("Request cancelled", "AbortError"))).toBe(true);
  });

  it("recognizes textual abort errors", () => {
    expect(isAbortLikeError(new Error("signal is aborted"))).toBe(true);
  });
});
