#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${HEPTA_BUILD_DIR:-${ROOT_DIR}/build/core}"
JOBS="${HEPTA_JOBS:-2}"

cmake -S "${ROOT_DIR}" -B "${BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF

cmake --build "${BUILD_DIR}" \
  --target hepta_core_test_binaries \
  --parallel "${JOBS}"

ctest --test-dir "${BUILD_DIR}" \
  --output-on-failure \
  --parallel "${JOBS}" \
  -L core
