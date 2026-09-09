import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("./AppContext.tsx", import.meta.url), "utf8");

describe("AppContext state surface", () => {
  it("does not depend on a manually maintained useMemo array", () => {
    expect(source).not.toContain("useMemo");
  });

  it("exposes scan state setters for reconciliation", () => {
    expect(source).toContain("setScanning");
    expect(source).toContain("setReevaluating");
  });

  it("keeps setup guide and settings openers stable callbacks", () => {
    expect(source).toContain("openSettings");
    expect(source).toContain("openSetupGuide");
  });

  it("exposes the apply cart (localStorage-persisted job ids)", () => {
    expect(source).toContain("addToCart");
    expect(source).toContain("removeFromCart");
    expect(source).toContain("galaxyhire-cart-v1");
  });

  it("exposes elevated activeIngestion state and task aliases", () => {
    expect(source).toContain("activeIngestion");
    expect(source).toContain("setActiveIngestion");
    expect(source).toContain("activeIngestTask");
    expect(source).toContain("setActiveIngestTask");
    expect(source).toContain("IngestionJob");
  });
});
