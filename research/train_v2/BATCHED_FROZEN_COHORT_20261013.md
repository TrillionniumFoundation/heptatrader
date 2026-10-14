# Train V2: one-pass archived source for frozen 3×4 price controls

Date: 2026-10-13. Independent pocket4 source branch stacked on
PR #277 / commit 961f97c. Research-only; not a runtime or model cutover.

## Concrete scaling problem

The existing verified paged source loader is correct, but the strict
3×4 frozen cohort's three original 62-minute outcome windows are disjoint,
so their prior replay calls the complete >200 MiB ORIGINAL PAPER hash-linked
archive reader separately for each window. As original source grows this
triples repeated hash-chain parsing and also creates artificial opportunities
for normal archive rotation between scans. Old pinned plans and receipts
must not be rewritten to change their model, source or evaluation rules.

## Implementation — new interface, old entrypoints unchanged

paged_batch_original.py uses the existing unchanged original source
directory enumerator, source archive page inventory and
laya_cell.shadow_source.read_source. It verifies EVERY numeric segment
once, including all 8 hash-linked pages and all unpaged sealed successors.
It enforces the same strict complete prefix and unique quote receipts,
correct original period/PAPER header, broker TTL, no unsealed outcome
input, 64MiB-per-file ceiling, 2-million total-source quote cap, at most
128 archive pages and 18,000 selected quotes per window. Only selected
segments get a second exact-byte read to verify their original broker TTL.
It refuses missing/mutated historical files and any source/page frontier
change across the single scan.

Three windows must be preregistered by original receipt, exact size/shape,
chronologically disjoint and individually <=2 hours. It returns three
**identical schema** old single-window views, including one common
complete sealed-source SHA256, per-window original quote identities, the
first 269-segment frozen prefix and original page inventory. It never
changes source_archive, paged_price_window.py, the old cohort audit,
original numeric service, systemd units, broker operations or actor.

paged_batch_cohort_replay.py constructs these three exact original windows
from immutable campaign/input/timer receipts, calls the batched source once,
then invokes the original strict audit_existing_frozen_12cell UNCHANGED
with a bounded, single-use pager callback. The strict old auditor still
recomputes the original 12 prices and compares every frozen input and
original economic/authority section. Independently, the new wrapper
re-checks all 3 old immutable economic fields and the original 3-decision,
12-label, zero-broker-profit scopes. No new model or strategy evidence is
created. A changed source, old outcome, cost threshold, label count or
unsupported effect authority fails.

paged_batch_compare.py does an independent second audit of immutable
new/old full-source results. It pins exact file SHA256 and verifies 11
frozen identity, source, quote-count, price and cost fields match. It
has its own SHA256-bound source inventory; no numeric-model, product
Owner or trading rights are inferred.

## Real pocket4 one-scan versus three-scan negative control

The actual input had 287 naturally sealed numeric source segments and
8 archived source pages, 18 successors after the original first 269.
There were exactly 4,532 original quote receipts inside the three
frozen 62-minute intervals.

Original old three-source-scan replay: 13.98 s actual command wall time.
First new batch source read: 4,805.141 ms actual internal monotonic timer.
Final same-code batch source read: 4,648.978 ms actual monotonic timer.
This is approximately 2.9-3.0x reduced source-scan work. Measurements
are sequential diagnostic wall times, not an isolated multi-run
statistical performance distribution or target latency guarantee.

The new and old source fingerprints matched byte-identically:
5437cdc85c6c28ca008fbce3783e1f79fc19a7d3cfa4567b5f1d125ce1191dce.
The frozen original first-269 prefix SHA256 also matched exactly:
cd8540b689d4ae75d76ec34fe0e6f72a06a0df729b8dd8d4a12cd13f5a5658db.

All 11 exact new-versus-old fields matched, including original frozen
12 outcomes, 3 correlated source decisions, 4,532 selected quotes,
original maximum absolute MID move 3.360471003615866799890673000 bps,
and old hypothetical cost+margin 10.9133557 bps: 0/12 price cells
above that threshold. There are 0 actual attributed strategy trades.

Original failed synthetic test transcript remains preserved: initial tests
incorrectly expected the V2 ContractError rather than the original
laya_cell integrity ValueError for source/page failure. Underlying
source correctly refused deletion, corruption and duplicate segments.
After aligning the test to the original source exception, all focused
source tests passed without changing the original checker.

## Private immutable machine evidence

Directory:
~/.local/state/heptatrader/operator-evidence/train-v2-batched-paged-cohort-20261013/

First batch receipt:
first-source-scan-v1.json SHA256
4559216b78af98475c43bfa19d48e0ab15b835c15058687172fda3fbb22a5a79.

Old unchanged three-scan negative control:
old-three-scan-negative-control-v1.json SHA256
97e61b807db0285d5d569479675cd82ba12649d439f671902467b2d07df8fc0b.

Final source-identity and economic-pin batch:
final-single-scan-source-pin-v2.json SHA256
6154d42bc156a79209dc69dcf9a7738ba5b2e638eee2d1f9b10ae4f7dc21d6a6.

Independent source-pinned comparison:
final-11-field-source-pinned-comparison-v2.json SHA256
18e37b1c2685d1a4efaa393f4e0b04ff9949cd61b12bf73e33124892487b0abc.

No earlier received source, archived page, frozen registration, plan,
prediction, output or rejected receipt was rewritten.

## Remaining blockers

This improves accurate continuation over growing archives; it does NOT
create a future signed Numeric/Laya forecast, a real original
REQUEST/PREPARED trade decision, a true logged policy propensity,
independent Canonical Generator/Observer, terminal financial reward,
complete account-level after-cost PnL, positive alpha or signed CURRENT
Artifact Owner. Existing Native/Numeric forecasts remain 2
price predictions, 1/2 direction correct, with 0 executed trades.
The correlated original 12 PRICE labels are not 12 independent trades.
Native f64 exact-replay failures are still FAILED.

No unattended task, model-training cadence, fallback, trading authority,
PAPER services, Laya Guard or asset cleanup is changed by this branch.



## Exact final branch regression (same code tree)

- Focused batch source, cohort, immutable comparison: **30/30 pass**.
- Full historical research: **539 tests passed, 7 previously skipped**.
- Full Core Python: **2,673 tests passed, 23 previously skipped**.
- Source/build/ownership: **106 tests passed**, no skips.
- Independently preserved initial wrong-exception-class negative-test
  transcript before its test expectation was corrected. No original
  source or consumer was modified to resolve the test.
- Final one-scan first-269 prefix, current full sealed fingerprint,
  12/12 labels, 4,532 quotes, fixed economic hurdle and zero-economic-
  reward status matched the unchanged 3-scan control 11/11 exactly.
- The final source-checked batch read was 4,648.978 milliseconds;
  unchanged old three-scan CLI wall time was 13.98 seconds. These are
  two sequential machine observations, not a formal speed distribution.
- The complete source active segment was not frozen or used as an
  original outcome; natural append/rotation continues independently.

Original FAILED Native f64 outcomes, HOLD for genuine independent
economic alpha, real behavior propensity, Original REQUEST/PREPARED,
and Account PnL completion all remain separate product blockers.
This PR is experimental read-only and must remain Draft until exact
CI and independent host/source qualification pass.
