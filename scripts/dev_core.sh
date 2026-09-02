#!/usr/bin/env bash
set -euo pipefail

# Canonical core verification validates every build path before any refresh.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD_TYPE="${HEPTA_BUILD_TYPE:-Release}"
GENERATOR="${HEPTA_CMAKE_GENERATOR:-}"

if [[ ! "${BUILD_TYPE}" =~ ^[A-Za-z][A-Za-z0-9_-]*$ ]]; then
  printf 'invalid HEPTA_BUILD_TYPE=%s\n' "${BUILD_TYPE}" >&2
  exit 2
fi
RAW_BUILD_DIR="${HEPTA_BUILD_DIR:-${ROOT_DIR}/build/core-${BUILD_TYPE,,}}"
if [[ "${RAW_BUILD_DIR}" == /* ]]; then
  BUILD_DIR="${RAW_BUILD_DIR}"
else
  BUILD_DIR="${ROOT_DIR}/${RAW_BUILD_DIR}"
fi
if ! command -v realpath >/dev/null 2>&1; then
  printf 'realpath is required to validate HEPTA_BUILD_DIR\n' >&2
  exit 2
fi
BUILD_DIR="$(realpath -m -- "${BUILD_DIR}")"
ROOT_BUILD_DIR="$(realpath -m -- "${ROOT_DIR}/build")"
RUNNER_TEMP_ROOT="${RUNNER_TEMP:-/tmp}"
if [[ "${RUNNER_TEMP_ROOT}" != /* ]]; then
  RUNNER_TEMP_ROOT="${ROOT_DIR}/${RUNNER_TEMP_ROOT}"
fi
RUNNER_TEMP_DIR="$(realpath -m -- "${RUNNER_TEMP_ROOT}")"
case "${BUILD_DIR}" in
  "${ROOT_DIR}"/*)
    case "${BUILD_DIR}" in
      "${ROOT_BUILD_DIR}"/*) ;;
      *)
        printf 'HEPTA_BUILD_DIR inside the source tree must be under %s: %s\n' \
          "${ROOT_BUILD_DIR}" "${BUILD_DIR}" >&2
        exit 2
        ;;
    esac
    ;;
esac
case "${BUILD_DIR}" in
  "${ROOT_BUILD_DIR}"/*|"${RUNNER_TEMP_DIR}"/*) ;;
  *)
    printf 'HEPTA_BUILD_DIR must be under %s or RUNNER_TEMP (%s): %s\n' \
      "${ROOT_BUILD_DIR}" "${RUNNER_TEMP_DIR}" "${BUILD_DIR}" >&2
    exit 2
    ;;
esac
if [[ "${BUILD_DIR}" == "/" ||
      "${BUILD_DIR}" == "${ROOT_DIR}" ||
      "${BUILD_DIR}" == "${ROOT_BUILD_DIR}" ||
      "${BUILD_DIR}" == "${RUNNER_TEMP_DIR}" ]]; then
  printf 'refusing to use a broad HEPTA_BUILD_DIR: %s\n' \
    "${BUILD_DIR}" >&2
  exit 2
fi
if [[ -e "${BUILD_DIR}" && ! -d "${BUILD_DIR}" ]]; then
  printf 'HEPTA_BUILD_DIR is not a directory: %s\n' "${BUILD_DIR}" >&2
  exit 2
fi

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
  -DHEPTA_INSTALL_RUNTIME=ON
  -DHEPTA_ENABLE_IBAPI=OFF
)
if [[ ! -f "${BUILD_DIR}/CMakeCache.txt" ]]; then
  if [[ -z "${GENERATOR}" ]] && command -v ninja >/dev/null 2>&1; then
    GENERATOR=Ninja
  fi
  if [[ -n "${GENERATOR}" ]]; then
    configure_args+=( -G "${GENERATOR}" )
  fi
fi

bash -n "${ROOT_DIR}/scripts/run_ib_paper_qualification.sh"
python3 -m py_compile "${ROOT_DIR}/scripts/verify_ib_paper_qualification.py"
python3 "${ROOT_DIR}/scripts/generate_contract_bindings.py" --check
python3 "${ROOT_DIR}/scripts/generate_documentation_views.py" --check
python3 "${ROOT_DIR}/scripts/check_documentation_control_plane.py"
python3 "${ROOT_DIR}/scripts/check_repository_integrity.py"
python3 "${ROOT_DIR}/scripts/check_systemd_documentation.py"
python3 "${ROOT_DIR}/scripts/check_workflow_check_contexts.py"
python3 "${ROOT_DIR}/scripts/check_schema_catalog.py"
python3 "${ROOT_DIR}/scripts/check_research_registries.py"
python3 "${ROOT_DIR}/scripts/check_module_discipline.py"
python3 "${ROOT_DIR}/scripts/check_change_impact.py" --self-test
python3 "${ROOT_DIR}/research/run_protocol.py" verify \
  --manifest "${ROOT_DIR}/research/manifest-v1.json"

# The File API query must exist before configure.  The subsequent checker reads
# the actual configured codemodel rather than inferring ownership from CMake text.
python3 "${ROOT_DIR}/scripts/check_cmake_module_graph.py" \
  --prepare --build-dir "${BUILD_DIR}"
cmake "${configure_args[@]}"
python3 "${ROOT_DIR}/scripts/check_cmake_module_graph.py" \
  --check --build-dir "${BUILD_DIR}"

cmake --build "${BUILD_DIR}" \
  --target hepta_core_test_binaries hepta_runtime_binaries \
  --parallel "${JOBS}"
ctest --test-dir "${BUILD_DIR}" \
  --output-on-failure \
  --parallel "${JOBS}" \
  -L core \
  --no-tests=error

if [[ "${HEPTA_RUN_PYTHON_TESTS:-1}" == "1" ]]; then
  python3 -m unittest discover \
    -s "${ROOT_DIR}/tests/python" \
    -p 'test_*.py'
fi
