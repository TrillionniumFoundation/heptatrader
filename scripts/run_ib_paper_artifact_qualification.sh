#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 <verified-artifact-dir> <expected-candidate-sha> <evidence-dir>" >&2
  exit 64
}
[[ $# -eq 3 ]] || usage

ARTIFACT_INPUT="$1"
EXPECTED_SHA="$2"
[[ ! -L "$3" ]] || { echo "evidence destination must not be a symlink" >&2; exit 73; }
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
[[ -n "$QUALIFIER_INPUT" && ! -L "$QUALIFIER_INPUT" && ! -L "$ARTIFACT_INPUT" ]] || exit 78
ARTIFACT_DIR="$(realpath -e -- "$ARTIFACT_INPUT")"
QUALIFIER="$(realpath -e -- "$QUALIFIER_INPUT")"
[[ -d "$ARTIFACT_DIR" && -f "$QUALIFIER" && -x "$QUALIFIER" && ! -L "$QUALIFIER" ]] || exit 66
[[ "$(stat -c '%h' -- "$QUALIFIER")" == "1" ]] || { echo "qualification harness must have one hard link" >&2; exit 78; }
QUALIFIER_MODE=$((8#$(stat -c '%a' -- "$QUALIFIER")))
(( (QUALIFIER_MODE & 0022) == 0 )) || { echo "qualification harness is writable by group/world" >&2; exit 78; }
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
import json, re, sys
from pathlib import Path
path, expected_sha, binary_sha = sys.argv[1:]
value = json.loads(Path(path).read_text(encoding="utf-8"))
if value.get("schema") != "heptatrader.ib-candidate-artifact.v2":
    raise SystemExit("candidate manifest schema mismatch")
if value.get("candidate_sha") != expected_sha or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
    raise SystemExit("candidate manifest SHA mismatch")
binary = value.get("binary")
if not isinstance(binary, dict) or binary.get("name") != "hepta-ib-executiond" or binary.get("sha256") != binary_sha:
    raise SystemExit("candidate binary identity mismatch")
expected_isolation = {
    "network": "none",
    "environment": "cleared",
    "rootfs": "read-only-digest-pinned-oci",
    "source_mount": "read-only-git-archive",
    "sdk_mount": "read-only-stable-snapshot",
    "writable_filesystem": "dedicated-size-bounded-mount",
    "resource_control": "oci-cgroup-memory-cpu-pids-plus-tmpfs-limits",
    "candidate_output": "captured-not-replayed",
}
if value.get("isolation") != expected_isolation:
    raise SystemExit("candidate build isolation claim mismatch")
PY

[[ ! -e "$EVIDENCE_DIR" && ! -L "$EVIDENCE_DIR" ]] || exit 73
PARENT="$(dirname -- "$EVIDENCE_DIR")"
mkdir -p -- "$PARENT"
PARENT="$(realpath -e -- "$PARENT")"
# Reserve the final evidence namespace before any possible Broker side effect.
# A failed, interrupted, or killed campaign is never automatically re-executed
# under this identity. The directory remains available even after SIGKILL.
mkdir -m 0700 -- "$EVIDENCE_DIR" || exit 73
WORK_DIR="$EVIDENCE_DIR"
HARNESS_HOME=""
HARNESS_PID=""
CAMPAIGN_STATE=INCOMPLETE
QUALIFICATION_TIMEOUT_SECONDS=900
RESULT_PATH="$WORK_DIR/qualification-result.json"
REQUIRED_SCENARIOS="connect_authoritative_snapshot,disconnect_reconnect,partial_fill,duplicate_out_of_order_status,broker_reject,stale_quote,outcome_uncertain,cancel_race,reconcile_divergence,lease_fencing,kill_switch,terminal_recovery"

write_campaign_record() {
  python3 - "$WORK_DIR" "$1" "$2" "$3" "$EXPECTED_SHA" "$BINARY_SHA256" "$QUALIFIER_SHA256" <<'RECORD'
import json, os, sys, time
root, name, state, code, source, binary, harness = sys.argv[1:]
value = {
    "schema": "heptatrader.paper-campaign-attempt.v1",
    "state": state, "exit_code": int(code), "recorded_at_unix": int(time.time()),
    "candidate_sha": source, "binary_sha256": binary, "harness_sha256": harness,
    "authorization_effect": "NONE", "paper_authorized": False, "live_authorized": False,
}
# Exclusive creation prevents the harness from replacing a wrapper-owned receipt.
dirfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(value, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.fsync(dirfd)
finally:
    os.close(dirfd)
RECORD
}

finish_campaign() {
  local status="$1"
  trap - EXIT INT TERM HUP
  if [[ "$status" == 0 && "$CAMPAIGN_STATE" != COMPLETED ]]; then status=70; fi
  if ! write_campaign_record campaign-status.json "$CAMPAIGN_STATE" "$status"; then
    echo "campaign status could not be committed; retained evidence: $WORK_DIR" >&2
    [[ "$status" != 0 ]] || status=74
  fi
  # Credential/private HOME scratch is deliberately outside the uploaded tree.
  # Never delete WORK_DIR: it may be the only record of a possible send.
  if [[ -n "$HARNESS_HOME" ]]; then rm -rf -- "$HARNESS_HOME" || status=74; fi
  printf 'PAPER campaign %s; retained evidence: %s\n' "$CAMPAIGN_STATE" "$WORK_DIR" >&2
  exit "$status"
}

interrupt_campaign() {
  local status="$1"
  trap '' INT TERM HUP
  if [[ -n "$HARNESS_PID" ]]; then
    kill -TERM "$HARNESS_PID" 2>/dev/null || true
    wait "$HARNESS_PID" 2>/dev/null || true
  fi
  exit "$status"
}
trap 'finish_campaign "$?"' EXIT
trap 'interrupt_campaign 130' INT
trap 'interrupt_campaign 143' TERM
trap 'interrupt_campaign 129' HUP
write_campaign_record campaign-start.json RUNNING 0
HARNESS_HOME="$(mktemp -d --tmpdir="$PARENT" .hepta-ib-harness-home.XXXXXX)"
chmod 0700 "$HARNESS_HOME"

# Only the independently pinned external harness may launch the candidate.
# It starts from an empty environment. Raw Actions, runner, GitHub and Broker
# credentials remain inside the harness implementation and are never inherited
# by the candidate process. The harness must enforce the exact operation list,
# broker-proxy-only networking and harness-only credential delivery.
command -v timeout >/dev/null 2>&1 || {
  echo "GNU timeout is required for bounded PAPER qualification" >&2
  exit 78
}
timeout --foreground --signal=TERM --kill-after=30s "${QUALIFICATION_TIMEOUT_SECONDS}s" env -i \
  PATH=/usr/bin:/bin \
  HOME="$HARNESS_HOME" \
  LC_ALL=C \
  HEPTA_QUALIFICATION_EXPECTED_GIT_SHA="$EXPECTED_SHA" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY="$BINARY" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256="$BINARY_SHA256" \
  HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256="$QUALIFIER_SHA256" \
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
  --mode bounded-mutations &
HARNESS_PID=$!
if wait "$HARNESS_PID"; then
  HARNESS_PID=""
else
  STATUS=$?
  HARNESS_PID=""
  if [[ "$STATUS" == "124" || "$STATUS" == "137" ]]; then
    echo "external PAPER harness exceeded ${QUALIFICATION_TIMEOUT_SECONDS}s timeout" >&2
    exit 124
  fi
  echo "external PAPER harness failed (exit=$STATUS)" >&2
  exit "$STATUS"
fi

[[ -f "$RESULT_PATH" && ! -L "$RESULT_PATH" ]] || {
  echo "external PAPER harness did not produce qualification-result.json" >&2
  exit 70
}
CAMPAIGN_STATE=COMPLETED
printf 'IB PAPER campaign evidence retained for separate verification: %s\n' "$EVIDENCE_DIR"
