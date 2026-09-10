import { describe, expect, it } from "bun:test";
import { discoveryApi, isSelectablePortal, portalEnabled, selectablePortals, type PortalSource } from "./discovery";
import type { ApiFetch } from "./types";

const source = (id: string, overrides: Partial<PortalSource> = {}): PortalSource => ({
  id,
  label: id,
  kind: "board",
  provider: id,
  region: "",
  recency_policy: "strict",
  default_enabled: true,
  note: "",
  ...overrides,
});

describe("portal selection contract", () => {
  it("excludes retired and non-direct catalog rows from selectable portals", () => {
    const rows = [
      source("greenhouse"),
      source("retired", { recency_policy: "off" }),
      source("missing-provider", { provider: "" }),
    ];
    expect(isSelectablePortal(rows[0])).toBe(true);
    expect(isSelectablePortal(rows[1])).toBe(false);
    expect(isSelectablePortal(rows[2])).toBe(false);
    expect(selectablePortals(rows).map(row => row.id)).toEqual(["greenhouse"]);
  });

  it("enables every selectable portal before a saved map exists", () => {
    const fresh = source("fresh", { default_enabled: false });
    const retired = source("retired", { recency_policy: "off" });
    expect(portalEnabled(fresh, null)).toBe(true);
    expect(portalEnabled(retired, null)).toBe(false);
  });

  it("keeps an explicit saved map authoritative", () => {
    const saved = source("saved", { default_enabled: true });
    const defaultOff = source("default-off", { default_enabled: false });
    expect(portalEnabled(saved, { saved: false })).toBe(false);
    expect(portalEnabled(defaultOff, { "default-off": true })).toBe(true);
    expect(portalEnabled(defaultOff, {})).toBe(true);
  });

  it("preserves the complete selection map without imposing a portal-count cap", async () => {
    let body: Record<string, unknown> | null = null;
    let contentType = "";
    const api = (async (_path: string, init?: RequestInit) => {
      body = JSON.parse(String(init?.body || "{}"));
      contentType = new Headers(init?.headers).get("Content-Type") || "";
      return new Response(JSON.stringify({ ok: true, count: 95 }), { status: 200 });
    }) as unknown as ApiFetch;
    const portals = Object.fromEntries(Array.from({ length: 95 }, (_, i) => [`portal-${i}`, true]));
    const result = await discoveryApi.portalsSave(api, portals);
    expect(result.ok).toBe(true);
    expect(contentType).toBe("application/json");
    expect(Object.keys((body as unknown as { portals: Record<string, boolean> }).portals)).toHaveLength(95);
  });

  it("surfaces server validation details instead of replacing them with a generic 422", async () => {
    const api = (async () => new Response(JSON.stringify({ detail: "unknown portal ids: ghost" }), { status: 422 })) as unknown as ApiFetch;
    await expect(discoveryApi.portalsSave(api, { ghost: true })).resolves.toEqual({
      ok: false,
      error: "unknown portal ids: ghost",
    });
  });
});
