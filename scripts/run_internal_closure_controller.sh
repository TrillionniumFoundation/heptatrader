#!/usr/bin/env bash
set -euo pipefail

CONTROLLER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CANONICAL_BRANCH=codex/heptatrader-documentation-control-plane-v2
DATA_BRANCH=codex/heptatrader-review-p1-work
ALLOCATION_BRANCH=codex/heptatrader-review-p1-allocation-work
CANDIDATE_BRANCH=codex/heptatrader-internal-closure-candidate-v3

python3 -m py_compile \
  "$CONTROLLER_ROOT/scripts/internal_closure_finalizer.py" \
  "$CONTROLLER_ROOT/scripts/internal_closure_finalizer_v2.py"

git -C "$CONTROLLER_ROOT" fetch origin \
  "$CANONICAL_BRANCH" "$DATA_BRANCH" "$ALLOCATION_BRANCH"
BASE_SHA="$(git -C "$CONTROLLER_ROOT" rev-parse "origin/$CANONICAL_BRANCH")"
WORKTREE="${RUNNER_TEMP:?}/heptatrader-internal-closure-controller"
rm -rf -- "$WORKTREE"
git -C "$CONTROLLER_ROOT" worktree add --detach "$WORKTREE" "$BASE_SHA"
cleanup() {
  git -C "$CONTROLLER_ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cd "$WORKTREE"
git config user.name "Hepta Internal Closure Bot"
git config user.email "hepta-internal-closure-bot@users.noreply.github.com"

data_paths=(
  HeptaTrade/numeric/fixed_decimal.h
  HeptaTrade/numeric/fixed_decimal.cpp
  HeptaTrade/tool_host/typed_tool_protocol.cpp
  HeptaTrade/marketdata/sharded_market_data.h
  HeptaTrade/marketdata/sharded_market_data.cpp
  HeptaTrade/features/feature_generation.cpp
  tests/fixed_decimal_tests.cpp
  tests/sharded_market_data_tests.cpp
  tests/feature_generation_tests.cpp
)
if git show "origin/$DATA_BRANCH:HeptaTrade/numeric/fixed_decimal.h" | grep -Fq ToDoubleExact; then
  git checkout "origin/$DATA_BRANCH" -- "${data_paths[@]}"
elif git cat-file -e "origin/$DATA_BRANCH:scripts/p1_data_patch.py" 2>/dev/null; then
  git show "origin/$DATA_BRANCH:scripts/p1_data_patch.py" > scripts/.p1_data_patch.py
  python3 scripts/.p1_data_patch.py
  rm -f scripts/.p1_data_patch.py
else
  echo "data repair implementation is unavailable" >&2
  exit 1
fi

allocation_paths=(
  HeptaTrade/proposal/proposal_set.h
  HeptaTrade/proposal/proposal_set.cpp
  HeptaTrade/allocation/global_allocator.h
  HeptaTrade/allocation/global_allocator.cpp
  HeptaTrade/execution/allocation_plan_revalidator.h
  HeptaTrade/execution/allocation_plan_revalidator.cpp
  HeptaTrade/simulator/multi_agent_allocation.cpp
  tests/global_allocator_tests.cpp
  tests/allocation_plan_revalidator_tests.cpp
  tests/strategy_proposal_tests.cpp
  tests/multi_agent_allocation_tests.cpp
)
if git show "origin/$ALLOCATION_BRANCH:HeptaTrade/allocation/global_allocator.h" | grep -Fq GlobalDecisionReceipt; then
  git checkout "origin/$ALLOCATION_BRANCH" -- "${allocation_paths[@]}"
elif git cat-file -e "origin/$ALLOCATION_BRANCH:scripts/p1_allocation_patch.py" 2>/dev/null; then
  git show "origin/$ALLOCATION_BRANCH:scripts/p1_allocation_patch.py" > scripts/.p1_allocation_patch.py
  python3 scripts/.p1_allocation_patch.py
  rm -f scripts/.p1_allocation_patch.py
  if git cat-file -e "origin/$ALLOCATION_BRANCH:scripts/p1_allocation_repair.py" 2>/dev/null; then
    git show "origin/$ALLOCATION_BRANCH:scripts/p1_allocation_repair.py" > scripts/.p1_allocation_repair.py
    python3 scripts/.p1_allocation_repair.py
    rm -f scripts/.p1_allocation_repair.py
  fi
else
  echo "allocation repair implementation is unavailable" >&2
  exit 1
fi

python3 "$CONTROLLER_ROOT/scripts/internal_closure_finalizer_v2.py" --root "$WORKTREE"
python3 scripts/generate_documentation_views.py --write

git add -A
if ! git diff --cached --quiet; then
  git commit -m "fix: close all internally executable P1 gaps"
fi
test -z "$(git status --porcelain)"

grep -Fq ToDoubleExact HeptaTrade/numeric/fixed_decimal.h
grep -Fq ValidateSnapshot HeptaTrade/marketdata/sharded_market_data.cpp
grep -Fq GlobalDecisionReceipt HeptaTrade/allocation/global_allocator.h
grep -Fq AllocationExecutionContext HeptaTrade/execution/allocation_plan_revalidator.h
grep -Fq G-OPT-007 docs/program/gap-registry-v2.json
python3 -m py_compile tests/python/test_internal_verification_evidence.py

HEPTA_BUILD_DIR="$RUNNER_TEMP/heptatrader-internal-closure-core" \
HEPTA_CMAKE_GENERATOR=Ninja HEPTA_JOBS=2 \
  ./scripts/dev_core.sh

test -z "$(git status --porcelain)"

CXX=g++ HEPTA_CMAKE_GENERATOR=Ninja HEPTA_JOBS=2 \
HEPTA_RELIABILITY_BUILD_DIR="$RUNNER_TEMP/heptatrader-internal-closure-gcc" \
  ./scripts/reliability_core.sh

test -z "$(git status --porcelain)"

CXX=clang++ HEPTA_CMAKE_GENERATOR=Ninja HEPTA_JOBS=2 \
HEPTA_RELIABILITY_BUILD_DIR="$RUNNER_TEMP/heptatrader-internal-closure-clang" \
  ./scripts/reliability_core.sh

test -z "$(git status --porcelain)"

HEAD_SHA="$(git rev-parse HEAD)"
git push --force origin "$HEAD_SHA:refs/heads/$CANDIDATE_BRANCH"
git fetch origin "$CANONICAL_BRANCH"
CURRENT_SHA="$(git rev-parse "origin/$CANONICAL_BRANCH")"
if [[ "$CURRENT_SHA" != "$BASE_SHA" ]]; then
  echo "canonical head moved from $BASE_SHA to $CURRENT_SHA; tested candidate preserved at $CANDIDATE_BRANCH" >&2
  exit 75
fi
git push \
  --force-with-lease="refs/heads/$CANONICAL_BRANCH:$BASE_SHA" \
  origin "$HEAD_SHA:refs/heads/$CANONICAL_BRANCH"
printf 'published internal closure: base=%s head=%s\n' "$BASE_SHA" "$HEAD_SHA"
