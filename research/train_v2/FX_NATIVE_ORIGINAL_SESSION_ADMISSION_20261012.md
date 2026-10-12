# Original EUR/USD causal quote dataset and IB session-scope audit

**Date:** 2026-10-12. **Release:** HOLD / offline research only / no new training or trading.

## Why this exists

The frozen 18:10–19:25 EUR/USD Ridge research campaign is already terminal:
3 matured price-only outcomes, 1 censored quote-age event, NO_TRADE for
every valid window. It must **not** be rerun or retroactively repaired.
A prior source-drift audit measured a 4,609-ms source age at the censored
18:10 decision against the pre-frozen 4,500-ms limit, plus real-time
pseudo-close vs revised historical 5-minute OHLC differences.

The original Ridge weights were trained on revised historical IB data.
A new candidate using the *same* original PAPER quote feature domain
as inference needs a different independently registered training/holdout
population. Retrospectively collecting examples is **not** a new
prospective effectiveness test.

## Retained original population

The research reader uses existing locally SHA-chained original IB PAPER
quote segments whose completed bytes were captured before the old
campaign's registration seal. They are *not* broker-signed timestamps
and cannot independently prove original file mtime against a privileged
host operator. Historical OHLC is never used to backfill missing
original quote events.

- Original quote-event timestamps: **105,436**
- Original byte stream independently re-parsed: **176,653,386 bytes**
- Fixed original five-minute grids: **1,452**
- Grids with causally available quote within the frozen five-second rule: **740**
- Missing causal quote grids: **712**
- Candidate price-only examples with 13 distinct causal input closes,
  a live-eligible one-second decision and the earliest matured original
  post-horizon quote: **359**
- After purging overlapping 80-minute input/output blocks: **29**
- Frozen independent-block research-model threshold: **1,024**
- New model training, automatic selection and trading admission: **HOLD**.

Every example includes its original quote IDs, input availability and
expiry, strictly anchored target maturity and the first eligible
original price outcome, *not* actual executable fills or money PnL.
It is explicitly labeled retrospective development data.

## Disjoint original Broker SCHEDULE provenance

The separately pinned original IB PAPER history request was:

`~/.local/state/heptatrader/neuron-learning/historical-offline/eurusd-5m-extension-20261012-v4/chunks/6b88d87b825661262852197175de415e89d55e668094caecc8bbf14812d94adf/`

Its `request.json`, `receipt.json`, `response.json` are rehashed,
and the actual received SCHEDULE completion and 30 declared sessions
verified before any use. **This cached request covers September 9–
October 9 UTC only.** It is not signed proof of current October 12
trading hours.

| Session class for the fixed 1,452 grids | Grid count | Eligible original close | Valid +1s decision |
| --- | ---: | ---: | ---: |
| Within original IB historical SCHEDULE open time | 718 | 589 | 543 |
| Within cached request, broker SCHEDULE break/closed | 6 | 2 | 2 |
| Outside the broker SCHEDULE request: **UNKNOWN** | 728 | 149 | 137 |

The third class must **never** be labeled 'closed', 'broker outage'
or 'a known active market' without fresh broker session evidence. In
particular, **129 of 718** historically open grid points had no causal
original quote close, and another 46 quote closes failed the fixed
one-second freshness/expiry rule. Do not change the original 4.5-second
decision age or 5-second expiry to rescue them.

## Independent reproduction in a clean isolated worktree

The source and tests were copied byte-for-byte from a separate active
development worktree into:

`~/work/heptatrader-fx-native-session-audit-20261012/`

The other worktree and production services were left untouched.
The new dedicated `fx_original_native_admission_audit.py` is a second
small **read-only** verifier. It enforces:

1. Original campaign seal, preseal drift receipt, native population and
   immutable private examples have matching SHA-256 identities.
2. Cached Broker SCHEDULE request/receipt/response byte hashes, its
   historical scope, explicit UNKNOWN strata and disjoint grid counts
   agree with the native source population.
3. Every original feature/target example is ordered, quote-ID-unique,
   available by the decision, valid at the target grid, and labeled only
   by a first post-horizon source event **before the original cutoff**.
4. Candidate 80-minute non-overlapping block count is independently
   reconstructed. 359 overlapping rows **cannot** silently become 359
   independent training blocks. The explicit 29<1024 veto is retained.
5. Trade authority, monetary reward, automatic training, product Owner,
   Witness and model promotion remain **false**.

Research outputs on pocket4, all owner-private and write-once:

`~/.local/state/heptatrader/operator-evidence/fx-native-session-independent-20261012/`

- `native-source-price-examples-independent-v1.json` SHA256:
  `50e780b057d90a26593c8a7b92f19c70e6c156e21959ce4b58f418d2562ede6f`
- `native-source-contract-independent-v1.json` SHA256:
  `38ab65185848be6627402166657941cf73d5a15299c23826c626ad6c58d69035`
- `broker-schedule-coverage-independent-v1.json` SHA256:
  `3969013e1a93de942a30c8c87a89643050d65da82e08f2993b8f43e016a93a8c`
- `native-source-independent-admission-v1.json` SHA256:
  `de1bf7121972006bb309569c0f24f99fb51767e4d0502e59e48cc37c5c78fc64`

The independently regenerated examples match the earlier v3 examples
**byte-for-byte**. All business fields in earlier vs regenerated
native and calendar reports also match; differences are generated
timestamps and references to the newly written native report file.
No new Broker/TWS API request, order, cancellation, history download,
old data deletion, model retraining, threshold adjustment or execution
service restart was made.

## Verification / next true hard gate

`PYTHONPATH=research:research/neuron_learning:scripts python3 -B -m
unittest discover -s tests/python -p 'test_fx_original_*.py' -v`

**49/49** pass: native causal dataset (19), cached broker calendar (14),
new independent admissions and hostile-file boundary (16). Older
failed test receipts, if any, are never erased or treated as passes.

The current separate Laya source-health service may show variable
`INPUT_UNAVAILABLE`, `ENDPOINT_GAP` or `PATH_GAP`. This source
audit must not force its status green; its current rolling window
requires its own prospective quality evidence.

Next **P0** is new truly original, independently time-qualified
distinct-quote and market-session source coverage. Separate 'no new
market quote' from collector lag and Gateway readiness, without
synthesizing ticks. Never reclassify prior censored samples or widen
the old TTL. Next **P1** is enough *new*, non-overlapping,
prospectively registered quote-native training and unseen validation
data; do not weaken the original 1,024-block admission gate or claim
that same-domain features alone prove a tradable edge. Native production
Owner, signatures/Witness, independent executed net-after-cost PnL
and complete account Activity/Flex/NAV evidence remain separate HOLDs.
