#!/usr/bin/env bash
# One implementation for required PR/merge checks and periodic compiler lanes.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_runtime_resilience.sh <g++|clang++>' >&2; exit 64; }
case "$1" in
  g++) export CC=gcc CXX=g++; build=build/reliability-gcc;;
  clang++) export CC=clang CXX=clang++; build=build/reliability-clang;;
  *) exit 64;;
esac
# Only demonstrably documentation-only PRs skip expensive instrumentation.
# Unknown/new paths and the complete Gateway/Session/native client surface run.
if [[ -n "${HEPTA_CI_BASE_SHA:-}" ]]; then
  [[ "$HEPTA_CI_BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 65
  if python3 - "$HEPTA_CI_BASE_SHA" <<'PY'
import subprocess, sys
paths = subprocess.check_output(['git', 'diff', '--name-only', '-z', sys.argv[1]+'...HEAD']).split(b'\0')
paths = [p.decode('utf-8', 'strict') for p in paths if p]
# Exit 0 means C++ evidence is not applicable. A Git error is NEVER a skip.
only_docs = bool(paths) and all(p.endswith('.md') or p in ('LICENSE', 'NOTICE') for p in paths)
raise SystemExit(0 if only_docs else 1)
PY
  then
    echo '[SANITIZERS] NOT_APPLICABLE: documentation-only PR; no runtime evidence claimed'
    exit 0
  else
    rc=$?
    [[ "$rc" == 1 ]] || exit "$rc"
  fi
fi
export CXXFLAGS='-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer'
export ASAN_OPTIONS='detect_leaks=1:strict_string_checks=1:check_initialization_order=1'
export UBSAN_OPTIONS='print_stacktrace=1:halt_on_error=1'
cmake -S . -B "$build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXE_LINKER_FLAGS='-fsanitize=address,undefined' \
  -DCMAKE_SHARED_LINKER_FLAGS='-fsanitize=address,undefined' \
  -DBUILD_TESTING=ON -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_ENABLE_LEGACY_0DTE_BRIDGE=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build "$build" --target hepta_core_test_binaries --parallel "${BUILD_PARALLEL:-2}"
ctest --test-dir "$build" --output-on-failure -L core --parallel "${BUILD_PARALLEL:-2}"
