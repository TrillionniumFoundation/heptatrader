#!/usr/bin/env bash
# Shared campaign lifecycle. Evidence is visible at its final path BEFORE any
# possible send. A failed, cancelled or killed run must never erase its journal.
# Source only after source/binary/harness identities have been verified.

campaign_write_state() {
  local outcome="$1" rc="$2"
  python3 - "$EVIDENCE_DIR" "$EXPECTED_SHA" "$BINARY_SHA256" \
    "$QUALIFIER_SHA256" "${STAGE:-certify}" "$outcome" "$rc" <<'PY'
import json, os, sys, tempfile, time
from pathlib import Path
root, source, binary, harness, stage, outcome, code = sys.argv[1:]
root = Path(root)
value = dict(schema="heptatrader.campaign-exit.v1", candidate_sha=source,
             binary_sha256=binary, harness_sha256=harness, stage=stage,
             outcome=outcome, exit_code=int(code), observed_at_ns=time.time_ns(),
             authorization_effect="NONE", paper_authorized=False,
             live_authorized=False)
# This receipt describes wrapper termination, NEVER trading/reconciliation success.
fd, temporary = tempfile.mkstemp(prefix=".campaign-exit.", dir=root)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / "campaign-exit.json")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

campaign_finish() {
  local rc=$? state=failed_or_interrupted
  trap - EXIT INT TERM HUP
  set +e
  if [[ "$rc" == 0 ]]; then state=harness_returned_success; fi
  campaign_write_state "$state" "$rc"
  local receipt_rc=$?
  # HOME is private scratch, deliberately outside the exported evidence root.
  # The harness contract forbids writing evidence here or secrets in evidence.
  [[ -z "${HARNESS_HOME:-}" ]] || rm -rf -- "$HARNESS_HOME"
  if [[ "$rc" == 0 && "$receipt_rc" != 0 ]]; then rc=74; fi
  printf 'Campaign evidence retained: stage=%s exit=%s path=%s\n' \
    "${STAGE:-certify}" "$rc" "$EVIDENCE_DIR" >&2
  exit "$rc"
}

campaign_evidence_init() {
  [[ ! -e "$EVIDENCE_DIR" && ! -L "$EVIDENCE_DIR" ]] || return 73
  local parent
  parent="$(dirname -- "$EVIDENCE_DIR")"
  mkdir -p -- "$parent"
  # mkdir without -p is the atomic no-overwrite admission for this attempt.
  mkdir -m 0700 -- "$EVIDENCE_DIR"
  WORK_DIR="$EVIDENCE_DIR"
  HARNESS_HOME=""
  trap campaign_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  campaign_write_state running 0
  HARNESS_HOME="$(mktemp -d --tmpdir="$parent" .hepta-harness-home.XXXXXX)"
  chmod 0700 "$HARNESS_HOME"
}
