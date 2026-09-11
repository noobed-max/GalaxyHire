#!/usr/bin/env bash
# Unified E2E Test Runner for GalaxyHire
# Runs both Backend Pytest (4 tiers) and Frontend Bun Test suites.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================================="
echo "        GalaxyHire Monorepo E2E Test Suite Orchestrator          "
echo "================================================================="
echo "Project Root: ${PROJECT_ROOT}"
echo "Started at:   $(date -u +"%Y-%m-%d %H:%M:%SZ")"
echo "================================================================="

# 1. Backend Pytest Execution
echo ""
echo ">>> [1/2] Executing Backend E2E Pytest Suite (Tiers 1-4)..."
echo "-----------------------------------------------------------------"
if [ -f "${PROJECT_ROOT}/apps/api/.venv/bin/pytest" ]; then
    PYTEST_BIN="${PROJECT_ROOT}/apps/api/.venv/bin/pytest"
else
    PYTEST_BIN="pytest"
fi

PYTHONPATH="${PROJECT_ROOT}/apps/api:${PYTHONPATH}" "${PYTEST_BIN}" "${PROJECT_ROOT}/apps/api/tests/e2e" -v --tb=short
BACKEND_STATUS=$?
echo ">>> Backend Pytest finished with status: ${BACKEND_STATUS}"

# 2. Frontend Bun Test Execution
echo ""
echo ">>> [2/2] Executing Frontend E2E Bun Test Suite..."
echo "-----------------------------------------------------------------"
cd "${PROJECT_ROOT}/apps/web"
bun test src/features/e2e/
FRONTEND_STATUS=$?
echo ">>> Frontend Bun test finished with status: ${FRONTEND_STATUS}"

echo ""
echo "================================================================="
echo "                   E2E TEST RUN SUMMARY                          "
echo "================================================================="
if [ ${BACKEND_STATUS} -eq 0 ] && [ ${FRONTEND_STATUS} -eq 0 ]; then
    echo "SUCCESS: All Backend and Frontend E2E tests PASSED cleanly!"
    exit 0
else
    echo "FAILURE: One or more E2E test suites failed."
    echo "  - Backend Status:  ${BACKEND_STATUS}"
    echo "  - Frontend Status: ${FRONTEND_STATUS}"
    exit 1
fi
