#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${HEPTA_BUILD_DIR:-$root/build/core}"
jobs="${HEPTA_JOBS:-2}"

# Focused local feedback uses the same production objects and test bodies.
# CI/release callers omit the option and still run the complete core aggregate.
if (( $# > 1 )) || [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
  echo "Usage: $0 [--core|--storage] (HEPTA_JOBS must be a positive integer)" >&2
  exit 64
fi
lane=core
targets=(hepta_core_test_binaries)
selection=(-L core)
case "${1:---core}" in
  --core) ;;
  --storage)
    lane=storage
    targets=(hepta_session_supervisor_lease_store_migration_tests
             hepta_audit_journal_lifecycle_tests
             hepta_oms_journal_durability_tests
             hepta_oms_journal_schema_v4_tests)
    expression="$(IFS='|'; printf '%s' "${targets[*]}")"
    selection=(-R "^($expression)$")
    ;;
  *) echo "Usage: $0 [--core|--storage]" >&2; exit 64 ;;
esac

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
build_dir="$(cd "$build_dir" && pwd)"
cmake --build "$build_dir" --target "${targets[@]}" --parallel "$jobs"
ctest --test-dir "$build_dir" --output-on-failure --no-tests=error "${selection[@]}" --parallel "$jobs" \
  --test-output-size-passed 65536 --output-junit "$build_dir/$lane-results.xml" \
  --output-log "$build_dir/$lane-ctest.log"
