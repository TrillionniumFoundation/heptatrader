#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <build-dir> <evidence-dir>" >&2
  exit 64
fi

BUILD_DIR="$(realpath "$1")"
EVIDENCE_DIR="$(realpath -m "$2")"
QUALIFIER="${HEPTA_IB_PAPER_QUALIFIER:-}"
MUTATIONS="${HEPTA_QUALIFICATION_MUTATIONS:-0}"

if [[ -z "$QUALIFIER" || ! -x "$QUALIFIER" ]]; then
  echo "HEPTA_IB_PAPER_QUALIFIER must name a controlled executable harness" >&2
  exit 78
fi
if [[ "$MUTATIONS" != 0 && "$MUTATIONS" != 1 ]]; then
  echo "HEPTA_QUALIFICATION_MUTATIONS must be exactly 0 or 1" >&2
  exit 78
fi
if [[ ! -x "$BUILD_DIR/HeptaTrade/hepta-ib-executiond" && \
      ! -x "$BUILD_DIR/bin/Release/hepta-ib-executiond" ]]; then
  echo "broker-enabled execution binary is missing" >&2
  exit 66
fi

mkdir -p "$EVIDENCE_DIR"
chmod 700 "$EVIDENCE_DIR"
MODE=read-only
if [[ "$MUTATIONS" == 1 ]]; then
  MODE=bounded-mutations
fi

exec "$QUALIFIER" \
  --repository-root "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" \
  --build-dir "$BUILD_DIR" \
  --evidence-dir "$EVIDENCE_DIR" \
  --mode "$MODE"
