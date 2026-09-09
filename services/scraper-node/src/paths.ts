/**
 * Shared paths — survives the deletion of src/registry.ts (the D5 gate).
 *
 * Extracted because `config.ts` and `adapters/career-ops.ts` both need REPO_ROOT and importing
 * it from the registry pulled the whole multi-project gate machinery in with it.
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, '../../..');
