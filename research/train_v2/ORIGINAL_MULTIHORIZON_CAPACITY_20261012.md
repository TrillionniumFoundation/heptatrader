# Train V2: original PAPER multi-horizon price-capacity audit

2026-10-12, pocket4. **Retrospective exploratory NO_TRADE** / no model
training / no policy or product-model promotion. Based on original request
denominator branch 4ff13ad. The old original four-window Ridge evaluation,
actual PAPER orders, Execution, OMS, risk vetoes, Laya service and canonical
Owner are untouched.

## Why this experiment is separate

The previously frozen original-source **15-minute** Ridge model selected
NO_TRADE. On the original inspected 359 development rows its largest
predicted absolute return was ~0.2563 bps and a perfect hindsight-direction
price move never exceeded its originally fixed 10.9133557-bps cost-plus-margin
hurdle. This did **not** establish which alternative hold period could work,
or whether any such period would provide enough causally observed,
non-overlapping future evidence. Training another 15-minute Ridge on the
same inspected labels would be multiple testing, not progress.

The present code fixes a *separate* four-horizon research question in
source code before consuming outcome values. It never selects a "winning"
horizon from the resulting economic table, changes the frozen cost inputs,
builds a financial return training set or creates an independent OOS test.

## Original source and frozen cost

- Source is the complete original EUR/USD PAPER locally SHA-chained
  Laya numeric quote segment archive, under the original reset period
  61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f.
- The unchanged original scanner validates each segment, period,
  original quote authority, source ownership and monotonic hash chain.
  The newest unsealed segment is EXCLUDED; older unsealed segments reject.
- A first exploratory read produced 114,607 eligible original source quotes
  in 254 sealed numeric segments. Their exact frozen **sealed-only**
  fingerprint is f2a878bd6c80397c9d39af233d44482f3a5c861b90c6bb1681911d9e8864cf23.
  The fingerprint is now a code-level exact equality check. A later
  sealed-segment growth requires a **new** versioned research population.
- The upstream source scanner's raw byte counter includes the unsealed
  newest segment for memory/resource accounting. That byte counter can
  grow even while the sealed-only fingerprint stays unchanged. It is
  explicitly named as such; it is NOT a historical new label population.
- The original fee/quote-spread research source is the pinned IB PAPER
  cost receipt SHA256
  12b251c4b6226abe501ff2a044e0618b59e6ab88170caed0db80387442a79e8c.
  Same reset period, 190 fee-complete callback executions, no missing
  commission, no verified actual execution slippage and no broker Flex
  expense/flow proof. No revised historical bar is an executable quote.
- The old frozen stress is used unchanged:

      original full spread p99       3.4837759 bps
      2 x original fee one-way p90  1.4295798 bps
      assumed 2 bps slip each side  4.0000000 bps
      -------------------------------------------
      modeled roundtrip cost        8.9133557 bps
      separate safety margin        2.0000000 bps
      original absolute hurdle     10.9133557 bps

  These are historical quantiles/assumptions, **not** an executable
  cost guarantee. Code and negative tests reject a lowered hurdle or
  replacement of the pinned original source.

## Predefined causal window extraction and censoring

- Fixed UTC 5-minute grid; each hypothetical decision is one second
  after the grid anchor. The most recently locally **AVAILABLE** original
  quote must be at or before decision time and still within the ORIGINAL
  IB stale-after bound. No older quote substitution after an expired
  latest quote.
- Hold times **5, 15, 30, 60 minutes** are fixed in source code;
  first actual observed quote event at or after anchor plus horizon,
  strictly no more than 5 seconds late, supplies a *price-only* outcome.
  Original event-time and locally-known-time remain distinct. There is
  no invented midpoint, bar interpolation or artificial broker fill.
- Path must contain no original event gap longer than 30 seconds. Absent
  endpoint, unknown next quote or a gap is reported as CENSORED with
  explicit reason, NOT zero PnL or missing winner recovery.
- Input-eligible time anchors are collected first. Time-disjoint
  selection is greedy increasing anchor time **before reading future
  price labels**, with a minimum 80-minute spacing or
  horizon+5-minute embargo, whichever is longer. The selected denominator
  includes censored outcomes. Time-disjoint means non-overlapping under
  that time rule; it does not prove statistical independence.
- All four studies have 1,524 scheduled grid anchors and 808
  original-quote-eligible inputs; 716 input anchors held due to
  expired original quotes. Each yielded 58 pre-label time-disjoint
  anchor slots. These are NOT policy action opportunities.

## Actual frozen original-source price-only findings

| Horizon | Full-input outcomes observed | Prelabel disjoint slots | Disjoint outcomes observed | Censored disjoint | Max abs midpoint move bps | Perfect hindsight move above 10.9133557 bps |
|---|---:|---:|---:|---:|---:|---:|
| 5 min | 701 | 58 | 52 | 6 | 5.0505 | 0 |
| 15 min | 589 | 58 | 39 | 19 | 8.4952 | 0 |
| 30 min | 484 | 58 | 31 | 27 | 12.7650 | 1 |
| 60 min | 373 | 58 | 24 | 34 | 15.0778 | 1 |

The 30/60-minute tail contains rare price excursion larger than the
declared cost proxy, but those excursions are known ONLY IN HINDSIGHT.
Even perfect direction does not prove a real model can learn, select,
enter, survive carrying costs, close or reproduce any of them. Longer
holds also multiply source-path censoring and open-position risk.
There is no validated product horizon or economically qualified
model, and the old 15-minute Ridge remains NO_TRADE.

The original source has already been inspected, so these are DEVELOPMENT
statistics, not new unseen OOS evidence. The time-disjoint outcome
denominator is far below the old declared 1,024 minimum research-fit
size; no training or promotion gate is relaxed.

## Immutable physical evidence

Initial exploratory v1 and refined v2 are retained separately. Their
254 sealed original source segment hashes and 114,607 quote rows agree;
only the excluded, actively growing newest unsealed file changed size.
Neither older output was overwritten.

Final source-frozen post-privacy/guard report:
~/.local/state/heptatrader/operator-evidence/train-v2-original-multihorizon-20261012/observed-original-horizon-capacity-frozen-final.json

SHA256: e49d5b1aceb68461c6877f6aabfd551f13edd81cdadc0a954e58038a487367c2

The receipt binds original sealed source fingerprint, unchanged original
PAPER period, original cost file SHA, exact auditor/reader/cost-validator
Python SHA, all four predeclared horizons, complete selected denominators,
observed/censored counts and an explicit no-trade/no-promotion posture.
No private account number, individual fill ID, prompt, LLM response or
raw quote price sequence is included in the aggregate report.

## Actual next Train gate

1. Do NOT retune the original Ridge threshold/horizon against these
   inspected development outcomes, and do not promote 30/60 min merely
   because one hindsight price move cleared the cost proxy.
2. First close original offline-training vs real online feature/quote-time
   parity, episode policy identity and the original action/veto
   denominator. The opaque LLM BUY/SELL/HOLD confidence is NOT a logged
   behavior selection propensity, and original model REQUEST-only count
   is not all trading opportunities.
3. For any new study, pre-register a future **distinct source cohort**,
   complete label-blind opportunity/window denominator, hold periods
   and censoring rules before outcomes. Require an uninspected window,
   independent signed producer/observer and risk admission.
4. Obtain independent IB Activity/Flex trade cost, execution-time quote
   and actual slippage where possible, then assess *net executable*
   economic benefit over no trade with financing/drawdown.
5. Only then use the canonical root-enrolled LedgerWriter,
   DatasetSnapshotReceiptV3 and signed Artifact Owner CURRENT gate for
   explicitly authorized PAPER shadow/cutover. This branch cannot
   bypass any of those product gates.

No broker query, model training, new model inference, trade order, old
trial repair, production service change or canonical Owner grant occurred
in this isolated audit.

## Final exact-tree regression and source-code-bound limits

This final isolated worktree passed:
- New original-multihorizon focused adversarial cases: **22/22**.
- Combined complete Python / historical research pytest: **2,046 passed**,
  **23 pre-existing skips**, **839 passing subtests**, no failures.
- Exact Source/build/ownership partition: **106 passed**, no failures.
- Original scanned PAPER quote cohort: **114,607** from **254** sealed
  numeric segments; one actively growing newest segment remained unsealed
  and was excluded from all economic statistics.
- Model inference, fit, strategy reconfiguration, producer registration,
  brokerage API calls, and orders performed by this branch: **zero**.

The source-frozen final proof SHA256 is:
e49d5b1aceb68461c6877f6aabfd551f13edd81cdadc0a954e58038a487367c2

Test/source logs are retained beside that private report:
combined-exact-tree-pytest.log, combined-exact-tree-pytest.exit,
source-exact-tree.log, source-exact-tree.exit.

The native Canonical product gate, independent broker Activity/Flex
fees, actual execution slippage and genuine forward return evidence
are NOT proved by these tests. The new 30m/60m horizons are not
statistically admitted and do not authorize any change to the original
held 15-minute model.
