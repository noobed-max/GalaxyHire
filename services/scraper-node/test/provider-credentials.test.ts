import { afterEach, describe, expect, it, vi } from 'vitest';

const arbeitsagenturEntry = { name: 'AA', arbeitsagentur: { keywords: ['engineer'], size: 1 } };
const vdabEntry = { name: 'VDAB', vdab: { keywords: ['engineer'], size: 1 } };

async function loadProvider(relativePath: string): Promise<{ fetch: (entry: unknown, ctx: unknown) => Promise<unknown> }> {
  // Keep the vendored JavaScript out of the strict application typecheck; the provider contract
  // is exercised here through its mocked transport instead.
  const moduleUrl = new URL(relativePath, import.meta.url).href;
  return (await import(moduleUrl)).default as { fetch: (entry: unknown, ctx: unknown) => Promise<unknown> };
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('provider credentials', () => {
  it('requires the Arbeitsagentur key and sends the configured value', async () => {
    const arbeitsagentur = await loadProvider('../vendor/career-ops-providers/arbeitsagentur.mjs');
    vi.stubEnv('ARBEITSAGENTUR_API_KEY', 'unit-test-aa-key');
    const fetchJson = vi.fn(async (_url: string, options: { headers?: Record<string, string> }) => {
      expect(options.headers?.['X-API-Key']).toBe('unit-test-aa-key');
      return { ergebnisliste: [] };
    });

    await arbeitsagentur.fetch(arbeitsagenturEntry, { fetchJson } as never);
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it('fails clearly when the Arbeitsagentur key is absent', async () => {
    const arbeitsagentur = await loadProvider('../vendor/career-ops-providers/arbeitsagentur.mjs');
    vi.stubEnv('ARBEITSAGENTUR_API_KEY', '');
    const fetchJson = vi.fn();

    await expect(arbeitsagentur.fetch(arbeitsagenturEntry, { fetchJson } as never))
      .rejects.toThrow('missing ARBEITSAGENTUR_API_KEY');
    expect(fetchJson).not.toHaveBeenCalled();
  });

  it('requires the VDAB key and sends the configured value', async () => {
    const vdab = await loadProvider('../vendor/career-ops-providers/vdab.mjs');
    vi.stubEnv('VDAB_VEJ_KEY_MONITOR', 'unit-test-vdab-key');
    const fetchJson = vi.fn(async (_url: string, options: { headers?: Record<string, string> }) => {
      expect(options.headers?.['vej-key-monitor']).toBe('unit-test-vdab-key');
      return { resultaten: [] };
    });

    await vdab.fetch(vdabEntry, { fetchJson } as never);
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it('fails clearly when the VDAB key is absent', async () => {
    const vdab = await loadProvider('../vendor/career-ops-providers/vdab.mjs');
    vi.stubEnv('VDAB_VEJ_KEY_MONITOR', '');
    const fetchJson = vi.fn();

    await expect(vdab.fetch(vdabEntry, { fetchJson } as never))
      .rejects.toThrow('missing VDAB_VEJ_KEY_MONITOR');
    expect(fetchJson).not.toHaveBeenCalled();
  });
});
