#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${HEPTA_BUILD_DIR:-${ROOT_DIR}/build/core}"
STAGE_DIR="${HEPTA_STAGE_DIR:-${BUILD_DIR}/stage}"
JOBS="${HEPTA_JOBS:-2}"

python3 "${ROOT_DIR}/scripts/check_repo_contracts.py"
python3 -m unittest discover \
  -s "${ROOT_DIR}/tests/python" \
  -p 'test_*.py'

cmake -S "${ROOT_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF \
  -DHEPTA_ENABLE_HARDENING=ON

cmake --build "${BUILD_DIR}" \
  --target \
    hepta_core_test_binaries \
    hepta_tool_gatewayd \
    hepta_executiond \
    heptactl \
    hepta_sessionctl \
  --parallel "${JOBS}"

ctest --test-dir "${BUILD_DIR}" \
  --output-on-failure \
  --parallel "${JOBS}" \
  -L core

rm -rf "${STAGE_DIR}"
cmake --install "${BUILD_DIR}" --prefix "${STAGE_DIR}/usr"
python3 "${ROOT_DIR}/scripts/verify_install_tree.py" \
  --root "${STAGE_DIR}/usr" \
  --manifest "${STAGE_DIR}/install-manifest.json"
python3 "${ROOT_DIR}/scripts/generate_sbom.py" \
  --root "${STAGE_DIR}/usr" \
  --version-file "${ROOT_DIR}/VERSION" \
  --git-sha "$(git -C "${ROOT_DIR}" rev-parse HEAD 2>/dev/null || printf unknown)" \
  --output "${STAGE_DIR}/heptatrader.spdx.json"
