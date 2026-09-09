/**
 * Load-and-run probe. `npm run probe`
 *
 * Answers the question the whole Node worker rests on: can we load career-ops `.mjs` providers
 * in-process and actually get jobs out of them? Fastest way to tell a vendored-tree problem
 * (missing helper, a provider that can't load) apart from a bug in the harness built on top of
 * it.
 *
 * Hits the real network. Not part of `npm test`.
 */

// NOTE: vendored modules must be loaded with dynamic `import()`, never a static named import.
// The vendored tree transforms to CJS under tsx, and Node's named-export detection cannot see
// through barrel chains — a static named import fails to link while the same module destructured
// after `await import()` works fine.
//
// Paths are built rather than written as literals, which also keeps the vendored `.mjs` out of
// our typecheck program: career-ops providers carry `// @ts-check` and would be checked under
// our config otherwise.

async function probeCareerOps() {
  const providerPath = new URL('../vendor/career-ops-providers/greenhouse.mjs', import.meta.url).href;
  const mod: any = await import(providerPath);
  const provider = mod.default;
  console.log(`  loaded provider id=${provider.id} fetch=${typeof provider.fetch}`);

  // career-ops hands providers a transport in `ctx` rather than letting them build one.
  const ctx = {
    transport: 'http' as const,
    async fetchText(url: string) {
      const r = await fetch(url, { headers: { 'user-agent': 'GalaxyHire/0.1 probe' } });
      if (!r.ok) throw new Error(`${r.status} ${url}`);
      return r.text();
    },
    async fetchJson(url: string) {
      const r = await fetch(url, { headers: { 'user-agent': 'GalaxyHire/0.1 probe' } });
      if (!r.ok) throw new Error(`${r.status} ${url}`);
      return r.json();
    },
    async fetchResponse(url: string) {
      const r = await fetch(url, { headers: { 'user-agent': 'GalaxyHire/0.1 probe' } });
      if (!r.ok) throw new Error(`${r.status} ${url}`);
      return r;
    },
    maxPages: 1,
  };

  const jobs = await provider.fetch(
    { name: 'GitLab', api: 'https://boards-api.greenhouse.io/v1/boards/gitlab/jobs' },
    ctx,
  );
  const withDesc = jobs.filter((j: any) => (j.description ?? '').trim()).length;
  console.log(`  fetched ${jobs.length} jobs, ${withDesc} with description`);
  if (jobs[0]) console.log(`  sample: ${JSON.stringify(jobs[0]).slice(0, 200)}`);
  if (jobs.length === 0) throw new Error('probe: career-ops greenhouse returned zero jobs');
}

async function probeRegistry() {
  const regPath = new URL('../vendor/career-ops-providers/_registry.mjs', import.meta.url).href;
  const dir = new URL('../vendor/career-ops-providers', import.meta.url).pathname;
  const reg: any = await import(regPath);
  const providers = await reg.loadProviders(dir);
  console.log(`  _registry.loadProviders → ${providers.size} providers`);
  if (providers.size < 70) throw new Error(`probe: only ${providers.size} providers loaded — vendor tree damaged`);
}

const probes: Array<[string, () => Promise<void>]> = [
  ['career-ops registry', probeRegistry],
  ['career-ops greenhouse', probeCareerOps],
];

let failed = 0;
for (const [name, fn] of probes) {
  console.log(`\n── ${name} ──`);
  try {
    await fn();
    console.log('  ✓ ok');
  } catch (err) {
    failed += 1;
    console.error(`  ✗ ${(err as Error).message}`);
  }
}
process.exit(failed ? 1 : 0);
