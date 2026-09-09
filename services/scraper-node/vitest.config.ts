import path from 'node:path';
import { defineConfig } from 'vitest/config';

/**
 * Scoped deliberately to `test/` only.
 *
 * The include/exclude guard stays even though ever-jobs (and its 1,561 vendored spec files) is
 * gone: the career-ops vendor tree ships upstream tests elsewhere (`career-ops/tests/`, not
 * vendored), but a future re-vendor mistake or an errant `*.test.mjs` in the tree would otherwise
 * be discovered and run in the wrong harness. Cheap insurance, keep it.
 *
 * We test our own mappers, recency policy, config loader, role gate, and corpus client. Whether
 * the providers themselves still work live is what `src/probe.ts` and a real scrape run answer.
 */
export default defineConfig({
  test: {
    include: ['test/**/*.test.ts'],
    exclude: ['vendor/**', 'node_modules/**'],
  },
  resolve: {
    alias: {
      '@galaxyhire/contract': path.resolve(__dirname, '../../packages/contract/src/index.ts'),
    },
  },
});
