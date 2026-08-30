#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CXX_BIN="${CXX:-g++}"
COMPILER_TAG="$(basename "${CXX_BIN}")"
BUILD_DIR="${HEPTA_RELIABILITY_BUILD_DIR:-${ROOT_DIR}/build/reliability-${COMPILER_TAG}}"
JOBS="${HEPTA_JOBS:-2}"
SANITIZERS="-fsanitize=address,undefined -fno-omit-frame-pointer"
GENERATOR="${HEPTA_CMAKE_GENERATOR:-Ninja}"

if [[ ! "${JOBS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "HEPTA_JOBS must be a positive integer" >&2
  exit 2
fi

# This lane is allowed to refresh only an explicitly scoped build directory.
# Refuse root/home/workspace-wide paths before invoking CMake's removal helper.
BUILD_DIR_ABS="$(realpath -m -- "${BUILD_DIR}")"
ROOT_BUILD_PREFIX="$(realpath -m -- "${ROOT_DIR}/build")/"
RUNNER_TEMP_ROOT="${RUNNER_TEMP:-/tmp}"
RUNNER_TEMP_ABS="$(realpath -m -- "${RUNNER_TEMP_ROOT}")"
case "${BUILD_DIR_ABS}" in
  "${ROOT_BUILD_PREFIX}"*|"${RUNNER_TEMP_ABS}"/*)
    ;;
  *)
    echo "HEPTA_RELIABILITY_BUILD_DIR must be under ${ROOT_DIR}/build or RUNNER_TEMP" >&2
    exit 2
    ;;
esac
if [[ "${BUILD_DIR_ABS}" == "${ROOT_DIR}" || "${BUILD_DIR_ABS}" == "${RUNNER_TEMP_ABS}" || "${BUILD_DIR_ABS}" == "/" ]]; then
  echo "refusing to remove an unsafe reliability build directory" >&2
  exit 2
fi

cmake -E remove_directory "${BUILD_DIR_ABS}"
cmake -S "${ROOT_DIR}" -B "${BUILD_DIR_ABS}" -G "${GENERATOR}" \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_COMPILER="${CXX_BIN}" \
  -DCMAKE_CXX_FLAGS="${SANITIZERS}" \
  -DCMAKE_EXE_LINKER_FLAGS="${SANITIZERS}" \
  -DBUILD_TESTING=ON \
  -DHEPTA_INSTALL_RUNTIME=ON \
  -DHEPTA_ENABLE_IBAPI=OFF
cmake --build "${BUILD_DIR_ABS}" \
  --target hepta_reliability_test_binaries \
  --parallel "${JOBS}"
ASAN_OPTIONS="detect_leaks=0:abort_on_error=1" \
UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1" \
ctest --test-dir "${BUILD_DIR_ABS}" --output-on-failure \
  -L reliability --no-tests=error
