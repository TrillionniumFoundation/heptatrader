#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_TYPE="${HEPTA_BUILD_TYPE:-Release}"
BUILD_DIR="${HEPTA_BUILD_DIR:-${ROOT_DIR}/build/core-${BUILD_TYPE,,}}"
GENERATOR="${HEPTA_CMAKE_GENERATOR:-}"

if [[ -n "${HEPTA_JOBS:-}" ]]; then
  JOBS="${HEPTA_JOBS}"
elif command -v nproc >/dev/null 2>&1; then
  JOBS="$(nproc)"
elif command -v getconf >/dev/null 2>&1; then
  JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
else
  JOBS=2
fi

if [[ ! "${JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'invalid HEPTA_JOBS=%s\n' "${JOBS}" >&2
  exit 2
fi

configure_args=(
  -S "${ROOT_DIR}"
  -B "${BUILD_DIR}"
  -DCMAKE_BUILD_TYPE="${BUILD_TYPE}"
  -DBUILD_TESTING=ON
  -DHEPTA_ENABLE_IBAPI=OFF
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
)

# Select a generator only for a fresh build directory. Reusing an existing
# cache with a different generator is a CMake error and should not surprise a
# developer.
if [[ ! -f "${BUILD_DIR}/CMakeCache.txt" ]]; then
  if [[ -z "${GENERATOR}" ]] && command -v ninja >/dev/null 2>&1; then
    GENERATOR=Ninja
  fi
  if [[ -n "${GENERATOR}" ]]; then
    configure_args+=( -G "${GENERATOR}" )
  fi
fi

cmake "${configure_args[@]}"
cmake --build "${BUILD_DIR}" \
  --target hepta_core_test_binaries \
  --parallel "${JOBS}"
ctest --test-dir "${BUILD_DIR}" \
  --output-on-failure \
  --parallel "${JOBS}" \
  -L core

if [[ "${HEPTA_RUN_PYTHON_TESTS:-1}" == "1" ]]; then
  python3 -m unittest discover \
    -s "${ROOT_DIR}/tests/python" \
    -p 'test_*.py'
fi
