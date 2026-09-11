#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${HEPTA_BUILD_DIR:-$root/build/core}"
jobs="${HEPTA_JOBS:-2}"

generator=()
if command -v ninja >/dev/null 2>&1; then
  generator=(-G Ninja)
fi

cmake -S "$root" -B "$build_dir" "${generator[@]}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF \
  -DBUILD_IB_PROBE=OFF
cmake --build "$build_dir" --target hepta_core_test_binaries --parallel "$jobs"
ctest --test-dir "$build_dir" --output-on-failure -L core --parallel "$jobs"
