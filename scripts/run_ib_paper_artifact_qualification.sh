#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 <verified-artifact-dir> <expected-candidate-sha> <evidence-dir>" >&2
  exit 64
}
[[ $# -eq 3 ]] || usage

TRUSTED_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ARTIFACT_INPUT="$1"
EXPECTED_SHA="$2"
EVIDENCE_DIR="$(realpath -m -- "$3")"
QUALIFIER_INPUT="${HEPTA_IB_PAPER_QUALIFIER:-}"
EXPECTED_QUALIFIER_SHA="${HEPTA_IB_PAPER_QUALIFIER_SHA256:-}"
MUTATIONS="${HEPTA_QUALIFICATION_MUTATIONS:-0}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 65
[[ "$EXPECTED_QUALIFIER_SHA" =~ ^[0-9a-f]{64}$ ]] || {
  echo "HEPTA_IB_PAPER_QUALIFIER_SHA256 must pin the trusted external harness" >&2
  exit 78
}
[[ "$MUTATIONS" == "1" ]] || {
  echo "full qualification requires explicit HEPTA_QUALIFICATION_MUTATIONS=1" >&2
  exit 78
}
[[ -n "$QUALIFIER_INPUT" && ! -L "$QUALIFIER_INPUT" ]] || {
  echo "HEPTA_IB_PAPER_QUALIFIER must name a regular non-symlink harness" >&2
  exit 78
}
[[ ! -L "$ARTIFACT_INPUT" ]] || exit 66
ARTIFACT_DIR="$(realpath -e -- "$ARTIFACT_INPUT")"
QUALIFIER="$(realpath -e -- "$QUALIFIER_INPUT")"
[[ -d "$ARTIFACT_DIR" && -f "$QUALIFIER" && -x "$QUALIFIER" && ! -L "$QUALIFIER" ]] || exit 66
[[ "$(stat -c '%h' -- "$QUALIFIER")" == "1" ]] || {
  echo "qualification harness must have exactly one hard link" >&2
  exit 78
}
QUALIFIER_MODE=$((8#$(stat -c '%a' -- "$QUALIFIER")))
(( (QUALIFIER_MODE & 0022) == 0 )) || {
  echo "qualification harness must not be group/world writable" >&2
  exit 78
}
QUALIFIER_SHA256="$(sha256sum -- "$QUALIFIER" | awk '{print $1}')"
[[ "$QUALIFIER_SHA256" == "$EXPECTED_QUALIFIER_SHA" ]] || {
  echo "qualification harness digest mismatch" >&2
  exit 78
}

BINARY="$ARTIFACT_DIR/hepta-ib-executiond"
MANIFEST="$ARTIFACT_DIR/manifest.json"
[[ -f "$BINARY" && -x "$BINARY" && ! -L "$BINARY" ]] || exit 66
[[ -f "$MANIFEST" && ! -L "$MANIFEST" ]] || exit 66
[[ "$(stat -c '%h' -- "$BINARY")" == "1" ]] || exit 66
BINARY_SHA256="$(sha256sum -- "$BINARY" | awk '{print $1}')"

python3 - "$MANIFEST" "$EXPECTED_SHA" "$BINARY_SHA256" <<'PY'
import json
import re
import sys
from pathlib import Path

path, expected_sha, binary_sha = sys.argv[1:]
value = json.loads(Path(path).read_text(encoding="utf-8"))
if value.get("schema") != "heptatrader.ib-candidate-artifact.v1":
    raise SystemExit("candidate manifest schema mismatch")
if value.get("candidate_sha") != expected_sha or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
    raise SystemExit("candidate manifest SHA mismatch")
binary = value.get("binary")
if not isinstance(binary, dict) or binary.get("name") != "hepta-ib-executiond":
    raise SystemExit("candidate binary identity missing")
if binary.get("sha256") != binary_sha:
    raise SystemExit("candidate binary digest mismatch after extraction")
isolation = value.get("isolation")
if isolation != {
    "network_namespace": "unshared",
    "environment": "cleared",
    "source_mount": "read-only-archive",
    "sdk_mount": "read-only",
    "candidate_output": "captured-not-replayed",
}:
    raise SystemExit("candidate build isolation claim mismatch")
PY

[[ ! -e "$EVIDENCE_DIR" && ! -L "$EVIDENCE_DIR" ]] || {
  echo "evidence destination must not already exist" >&2
  exit 73
}
PARENT_DIR="$(dirname -- "$EVIDENCE_DIR")"
mkdir -p -- "$PARENT_DIR"
PARENT_DIR="$(realpath -e -- "$PARENT_DIR")"
WORK_DIR="$(mktemp -d --tmpdir="$PARENT_DIR" .hepta-ib-paper-qualification.XXXXXX)"
chmod 0700 "$WORK_DIR"
cleanup() {
  if [[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]]; then
    rm -rf -- "$WORK_DIR"
  fi
}
trap cleanup EXIT INT TERM HUP

RESULT_PATH="$WORK_DIR/qualification-result.json"
RECEIPT_PATH="$WORK_DIR/qualification-verification.json"
REQUIRED_SCENARIOS="connect_authoritative_snapshot,disconnect_reconnect,partial_fill,duplicate_out_of_order_status,broker_reject,stale_quote,outcome_uncertain,cancel_race,reconcile_divergence,lease_fencing,kill_switch,terminal_recovery"
TRUSTED_RUNNER_SHA256="$(sha256sum -- "${BASH_SOURCE[0]}" | awk '{print $1}')"
TRUSTED_VERIFIER_SHA256="$(sha256sum -- "$TRUSTED_ROOT/scripts/verify_ib_paper_qualification.py" | awk '{print $1}')"

export HEPTA_QUALIFICATION_EXPECTED_GIT_SHA="$EXPECTED_SHA"
export HEPTA_QUALIFICATION_EXPECTED_BINARY="$BINARY"
export HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256="$BINARY_SHA256"
export HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256="$QUALIFIER_SHA256"
export HEPTA_QUALIFICATION_TRUSTED_RUNNER_SHA256="$TRUSTED_RUNNER_SHA256"
export HEPTA_QUALIFICATION_TRUSTED_VERIFIER_SHA256="$TRUSTED_VERIFIER_SHA256"
export HEPTA_QUALIFICATION_REQUIRED_SCENARIOS="$REQUIRED_SCENARIOS"
export HEPTA_QUALIFICATION_RESULT_PATH="$RESULT_PATH"

# Only the independently pinned external harness may launch the candidate
# binary. Candidate source, CMake, tests and repository scripts are absent from
# this credential-bearing job. The harness starts from an empty environment and
# must enforce a broker-proxy-only operation allowlist; raw workflow/runner
# credentials are never inherited by the candidate process.
HARNESS_HOME="$WORK_DIR/harness-home"
mkdir -m 0700 "$HARNESS_HOME"
env -i \
  PATH=/usr/bin:/bin \
  HOME="$HARNESS_HOME" \
  LC_ALL=C \
  HEPTA_QUALIFICATION_EXPECTED_GIT_SHA="$EXPECTED_SHA" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY="$BINARY" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256="$BINARY_SHA256" \
  HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256="$QUALIFIER_SHA256" \
  HEPTA_QUALIFICATION_TRUSTED_RUNNER_SHA256="$TRUSTED_RUNNER_SHA256" \
  HEPTA_QUALIFICATION_TRUSTED_VERIFIER_SHA256="$TRUSTED_VERIFIER_SHA256" \
  HEPTA_QUALIFICATION_REQUIRED_SCENARIOS="$REQUIRED_SCENARIOS" \
  HEPTA_QUALIFICATION_RESULT_PATH="$RESULT_PATH" \
  HEPTA_QUALIFICATION_MUTATIONS=1 \
  "$QUALIFIER" \
  --execution-binary "$BINARY" \
  --expected-binary-sha256 "$BINARY_SHA256" \
  --expected-git-sha "$EXPECTED_SHA" \
  --required-scenarios "$REQUIRED_SCENARIOS" \
  --operation-allowlist "$REQUIRED_SCENARIOS" \
  --candidate-environment cleared \
  --candidate-network-policy broker-proxy-only \
  --credential-delivery harness-only \
  --evidence-dir "$WORK_DIR" \
  --result "$RESULT_PATH" \
  --mode bounded-mutations

python3 "$TRUSTED_ROOT/scripts/verify_ib_paper_qualification.py" \
  --result "$RESULT_PATH" \
  --evidence-root "$WORK_DIR" \
  --expected-git-sha "$EXPECTED_SHA" \
  --expected-binary "$BINARY" \
  --expected-harness "$QUALIFIER" \
  --receipt "$RECEIPT_PATH"

chmod 0700 "$WORK_DIR"
mv -T -- "$WORK_DIR" "$EVIDENCE_DIR"
WORK_DIR=""
trap - EXIT INT TERM HUP
printf 'IB PAPER qualification evidence committed: %s\n' "$EVIDENCE_DIR"
