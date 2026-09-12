#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  echo "usage: $0 <verified-artifact-dir> <expected-candidate-sha> <evidence-dir> <canary|pilot|extended>" >&2
  exit 64
}
[[ $# -eq 4 ]] || usage

ARTIFACT_INPUT="$1"
EXPECTED_SHA="$2"
[[ ! -L "$3" ]] || exit 73
EVIDENCE_DIR="$(realpath -m -- "$3")"
STAGE="$4"
TRUSTED_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
POLICY="$TRUSTED_ROOT/docs/ib-paper-rollout-policy-v1.json"
QUALIFIER_INPUT="${HEPTA_IB_PAPER_QUALIFIER:-}"
EXPECTED_QUALIFIER_SHA="${HEPTA_IB_PAPER_QUALIFIER_SHA256:-}"
MUTATIONS="${HEPTA_QUALIFICATION_MUTATIONS:-0}"

[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "candidate SHA is not canonical" >&2; exit 65; }
case "$STAGE" in canary|pilot|extended) ;; *) usage ;; esac
[[ "$EXPECTED_QUALIFIER_SHA" =~ ^[0-9a-f]{64}$ ]] || {
  echo "HEPTA_IB_PAPER_QUALIFIER_SHA256 must pin the trusted external harness" >&2
  exit 78
}
[[ "$MUTATIONS" == "1" ]] || {
  echo "PAPER rollout requires explicit HEPTA_QUALIFICATION_MUTATIONS=1" >&2
  exit 78
}
[[ -n "$QUALIFIER_INPUT" && ! -L "$QUALIFIER_INPUT" && ! -L "$ARTIFACT_INPUT" ]] || exit 78
ARTIFACT_DIR="$(realpath -e -- "$ARTIFACT_INPUT")"
QUALIFIER="$(realpath -e -- "$QUALIFIER_INPUT")"
[[ -d "$ARTIFACT_DIR" && -f "$QUALIFIER" && -x "$QUALIFIER" && ! -L "$QUALIFIER" ]] || exit 66
[[ "$(stat -c '%h' -- "$QUALIFIER")" == "1" ]] || { echo "rollout harness must have one hard link" >&2; exit 78; }
QUALIFIER_MODE=$((8#$(stat -c '%a' -- "$QUALIFIER")))
(( (QUALIFIER_MODE & 0022) == 0 )) || { echo "rollout harness is writable by group/world" >&2; exit 78; }
QUALIFIER_SHA256="$(sha256sum -- "$QUALIFIER" | awk '{print $1}')"
[[ "$QUALIFIER_SHA256" == "$EXPECTED_QUALIFIER_SHA" ]] || {
  echo "rollout harness digest mismatch" >&2
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
PY

readarray -t STAGE_POLICY < <(python3 - "$POLICY" "$STAGE" <<'PY'
import json, math, sys
from pathlib import Path
path, stage_id = sys.argv[1:]
value = json.loads(Path(path).read_text(encoding="utf-8"))
if value.get("schema") != "heptatrader.ib-paper-rollout-policy.v1":
    raise SystemExit("rollout policy schema mismatch")
if value.get("execution_mode") != "PAPER" or value.get("live_authorized") is not False:
    raise SystemExit("rollout policy must remain PAPER-only")
if value.get("profile_order_mode") != "EXTERNAL_P1_CANARY_LMT_DAY":
    raise SystemExit("rollout policy must use the existing P1 PAPER-V4 mode")
stages = [item for item in value.get("stages", []) if isinstance(item, dict) and item.get("id") == stage_id]
if len(stages) != 1:
    raise SystemExit("rollout stage is not unique")
stage = stages[0]
keys = ("max_mutation_cycles", "max_order_quantity", "max_order_notional", "max_active_orders", "max_gross_position")
for key in keys:
    number = stage.get(key)
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)) or float(number) <= 0:
        raise SystemExit(f"invalid rollout policy value: {key}")
if stage.get("require_flat_between_cycles") is not True:
    raise SystemExit("rollout must require flatness between cycles")
print(stage["max_mutation_cycles"])
print(stage["max_order_quantity"])
print(stage["max_order_notional"])
print(stage["max_active_orders"])
print(stage["max_gross_position"])
PY
)
[[ ${#STAGE_POLICY[@]} -eq 5 ]] || exit 78
MAX_CYCLES="${STAGE_POLICY[0]}"
MAX_ORDER_QUANTITY="${STAGE_POLICY[1]}"
MAX_ORDER_NOTIONAL="${STAGE_POLICY[2]}"
MAX_ACTIVE_ORDERS="${STAGE_POLICY[3]}"
MAX_GROSS_POSITION="${STAGE_POLICY[4]}"

# Guard the progressive policy against accidental widening even if the JSON is
# edited. These are the existing PAPER-V4 instantaneous hard limits in the
# execution profile, not new authorization limits.
[[ "$MAX_ORDER_QUANTITY" == "1.0" || "$MAX_ORDER_QUANTITY" == "1" ]] || exit 78
[[ "$MAX_ORDER_NOTIONAL" == "5000.0" || "$MAX_ORDER_NOTIONAL" == "5000" ]] || exit 78
[[ "$MAX_ACTIVE_ORDERS" == "1" ]] || exit 78
[[ "$MAX_GROSS_POSITION" == "1.0" || "$MAX_GROSS_POSITION" == "1" ]] || exit 78

CAMPAIGN="${HEPTA_ROLLOUT_CAMPAIGN:-}"
DRIVER="${HEPTA_ROLLOUT_DRIVER:-}"
DRIVER_SHA="${HEPTA_ROLLOUT_DRIVER_SHA256:-}"
[[ -n "$CAMPAIGN" && -n "$DRIVER" && "$DRIVER_SHA" =~ ^[0-9a-f]{64}$ ]] || {
  echo "rollout requires an admitted campaign and pinned host driver" >&2; exit 78;
}
python3 - "$TRUSTED_ROOT/scripts" "$CAMPAIGN" "$EXPECTED_SHA" "$BINARY_SHA256" "$QUALIFIER_SHA256" "$DRIVER_SHA" <<'PY_CHECK'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from verify_ib_paper_rollout import load_campaign
value=load_campaign(Path(sys.argv[2]))['binding']
if (value['candidate_sha'],value['binary_sha256'],value['harness_sha256'],value['driver_sha256']) != tuple(sys.argv[3:]):
    raise SystemExit("admitted campaign identity differs from candidate or harness")
PY_CHECK

source "$TRUSTED_ROOT/scripts/hepta_campaign_evidence.sh"
campaign_evidence_init

RESULT_PATH="$WORK_DIR/rollout-result.json"

# The same independently pinned external qualifier owns Broker credentials for
# both progressive rollout and full certification. This mode intentionally asks
# for a tiny PAPER-V4 round trip and a terminal flat reconciliation, not the V5
# twelve-scenario resilience campaign.
env -i \
  PATH=/usr/bin:/bin \
  HOME="$HARNESS_HOME" \
  LC_ALL=C \
  HEPTA_QUALIFICATION_EXPECTED_GIT_SHA="$EXPECTED_SHA" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY="$BINARY" \
  HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256="$BINARY_SHA256" \
  HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256="$QUALIFIER_SHA256" \
  HEPTA_ROLLOUT_STAGE="$STAGE" \
  HEPTA_ROLLOUT_MAX_MUTATION_CYCLES="$MAX_CYCLES" \
  HEPTA_ROLLOUT_MAX_ORDER_QUANTITY="$MAX_ORDER_QUANTITY" \
  HEPTA_ROLLOUT_MAX_ORDER_NOTIONAL="$MAX_ORDER_NOTIONAL" \
  HEPTA_ROLLOUT_MAX_ACTIVE_ORDERS="$MAX_ACTIVE_ORDERS" \
  HEPTA_ROLLOUT_MAX_GROSS_POSITION="$MAX_GROSS_POSITION" \
  HEPTA_ROLLOUT_RESULT_PATH="$RESULT_PATH" \
  HEPTA_QUALIFICATION_MUTATIONS=1 \
  "$QUALIFIER" \
  --execution-binary "$BINARY" \
  --expected-binary-sha256 "$BINARY_SHA256" \
  --expected-git-sha "$EXPECTED_SHA" \
  --rollout-stage "$STAGE" \
  --campaign "$CAMPAIGN" \
  --driver "$DRIVER" \
  --driver-sha256 "$DRIVER_SHA" \
  --max-mutation-cycles "$MAX_CYCLES" \
  --max-order-quantity "$MAX_ORDER_QUANTITY" \
  --max-order-notional "$MAX_ORDER_NOTIONAL" \
  --max-active-orders "$MAX_ACTIVE_ORDERS" \
  --max-gross-position "$MAX_GROSS_POSITION" \
  --profile-order-mode EXTERNAL_P1_CANARY_LMT_DAY \
  --require-flat-between-cycles \
  --candidate-environment cleared \
  --candidate-network-policy broker-proxy-only \
  --credential-delivery harness-only \
  --evidence-dir "$WORK_DIR" \
  --result "$RESULT_PATH" \
  --mode p1-progressive-rollout

[[ -f "$RESULT_PATH" && ! -L "$RESULT_PATH" ]] || {
  echo "external PAPER harness did not produce rollout-result.json" >&2
  exit 70
}
# EXIT records the actual outcome; verifier success remains a separate fact.
