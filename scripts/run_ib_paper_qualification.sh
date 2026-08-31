#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 <build-dir> <evidence-dir>" >&2
  exit 64
}

[[ $# -eq 2 ]] || usage

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BUILD_DIR="$(realpath -e -- "$1")"
EVIDENCE_DIR="$(realpath -m -- "$2")"
QUALIFIER_INPUT="${HEPTA_IB_PAPER_QUALIFIER:-}"
MUTATIONS="${HEPTA_QUALIFICATION_MUTATIONS:-0}"

if [[ "$MUTATIONS" != "1" ]]; then
  echo "full qualification requires explicit HEPTA_QUALIFICATION_MUTATIONS=1" >&2
  exit 78
fi
if [[ -z "$QUALIFIER_INPUT" ]]; then
  echo "HEPTA_IB_PAPER_QUALIFIER must name a controlled executable harness" >&2
  exit 78
fi
QUALIFIER="$(realpath -e -- "$QUALIFIER_INPUT")"
if [[ ! -f "$QUALIFIER" || ! -x "$QUALIFIER" || -L "$QUALIFIER" ]]; then
  echo "qualification harness must be an executable non-symlink regular file" >&2
  exit 78
fi
if [[ ! -d "$BUILD_DIR" || -L "$BUILD_DIR" ]]; then
  echo "build directory must be a non-symlink directory" >&2
  exit 66
fi

BINARY=""
for candidate in \
  "$BUILD_DIR/bin/Release/hepta-ib-executiond" \
  "$BUILD_DIR/HeptaTrade/hepta-ib-executiond" \
  "$BUILD_DIR/bin/hepta-ib-executiond"; do
  if [[ -f "$candidate" && -x "$candidate" && ! -L "$candidate" ]]; then
    BINARY="$(realpath -e -- "$candidate")"
    break
  fi
done
if [[ -z "$BINARY" ]]; then
  echo "broker-enabled execution binary is missing" >&2
  exit 66
fi

if [[ -e "$EVIDENCE_DIR" || -L "$EVIDENCE_DIR" ]]; then
  echo "evidence destination must not already exist: $EVIDENCE_DIR" >&2
  exit 73
fi
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

GIT_SHA="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
if [[ ! "$GIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "unable to establish canonical source commit" >&2
  exit 65
fi
if ! git -C "$ROOT_DIR" diff --quiet -- || \
   ! git -C "$ROOT_DIR" diff --cached --quiet --; then
  echo "qualification refuses a modified source checkout" >&2
  exit 65
fi

BINARY_SHA256="$(sha256sum -- "$BINARY" | awk '{print $1}')"
HARNESS_SHA256="$(sha256sum -- "$QUALIFIER" | awk '{print $1}')"
RESULT_PATH="$WORK_DIR/qualification-result.json"
RECEIPT_PATH="$WORK_DIR/qualification-verification.json"
REQUIRED_SCENARIOS="connect_authoritative_snapshot,disconnect_reconnect,partial_fill,duplicate_out_of_order_status,broker_reject,stale_quote,outcome_uncertain,cancel_race,reconcile_divergence,lease_fencing,kill_switch,terminal_recovery"

export HEPTA_QUALIFICATION_EXPECTED_GIT_SHA="$GIT_SHA"
export HEPTA_QUALIFICATION_EXPECTED_BINARY="$BINARY"
export HEPTA_QUALIFICATION_EXPECTED_BINARY_SHA256="$BINARY_SHA256"
export HEPTA_QUALIFICATION_EXPECTED_HARNESS_SHA256="$HARNESS_SHA256"
export HEPTA_QUALIFICATION_REQUIRED_SCENARIOS="$REQUIRED_SCENARIOS"
export HEPTA_QUALIFICATION_RESULT_PATH="$RESULT_PATH"

"$QUALIFIER" \
  --repository-root "$ROOT_DIR" \
  --build-dir "$BUILD_DIR" \
  --execution-binary "$BINARY" \
  --expected-binary-sha256 "$BINARY_SHA256" \
  --expected-git-sha "$GIT_SHA" \
  --required-scenarios "$REQUIRED_SCENARIOS" \
  --evidence-dir "$WORK_DIR" \
  --result "$RESULT_PATH" \
  --mode bounded-mutations

python3 "$ROOT_DIR/scripts/verify_ib_paper_qualification.py" \
  --result "$RESULT_PATH" \
  --evidence-root "$WORK_DIR" \
  --expected-git-sha "$GIT_SHA" \
  --expected-binary "$BINARY" \
  --expected-harness "$QUALIFIER" \
  --receipt "$RECEIPT_PATH"

chmod 0700 "$WORK_DIR"
mv -T -- "$WORK_DIR" "$EVIDENCE_DIR"
WORK_DIR=""
trap - EXIT INT TERM HUP
printf 'IB PAPER qualification evidence committed: %s\n' "$EVIDENCE_DIR"
