# EUR/USD original-source cost-aware research control (2026-10-12)

**Status: RESEARCH-ONLY PASS / TRADING AND MODEL PROMOTION HOLD**

This lane adds a deliberately **zero-return** prospective control, **not** a
trainable policy, and a separate original-quote price-only maturity verifier.
It exercises the complete local preregistration, fresh original IB PAPER feed,
frozen fee assumptions, decision-before-quote-expiry, mature-price evidence,
and independent X230 reparse **without submitting any order**.

Do not interpret any returned MIDPOINT movement as a fill, an actionable
forecast, on-policy reward, or original opaque LLM behavior propensity.
The existing PAPER FX/BTC services and risk exits are not touched.

## Model/control and economics

Module: `research/train_v2/fx_cost_shadow.py`

- A separate 0700 evidence directory contains `manifest.json`,
  `feature-spec.json`, `plan.json`, and `local-registration.json`.
  All are exclusive 0600 fsynced writes. A local SQLite registry seals
  the event window at least 30 seconds before the first decision.
- `behavior_policy_id` commits the frozen historical cost-receipt SHA256,
  period scope, code SHA256, **zero-return baseline**, spread formula,
  per-side slippage stress and minimum modeled margin. Code or cost drift
  fails closed. Tests use injected clocks only inside the research Python
  functions, not a CLI clock argument.
- The source is the configured pocket4
  `laya-resident-reset-.../input/segment-.../quotes.jsonl` file.
  `sealed_original_broker_expiry` checks the local SHA256 chain, original
  PAPER source, exact period, source timestamps and 5s quote lifetime.
  Six **distinct** quotes are mandatory and must be known before the
  decision; freshness is additionally limited to 1,500ms.
- Conservative modeled hurdle, basis points:
  `max(original_live_full_spread, historical_live_quote_p99_spread)`
  `+ 2 * IB_PAPER_commission_callback_p90`
  `+ 2 * assumed_slippage_per_side(2bps) + minimum_margin(2bps)`.
  A full bid/ask spread is counted **once**, not twice. The historical
  cost receipt is frozen prior to decision, and is not an attestation
  of real fill-time slippage or future brokerage fees.
- The only legal action is `NO_TRADE`. There is no trained forecasting
  model in this lane; `expected_midpoint_move_bps=0` is an explicit
  negative safety control. Any research gate result cannot become a
  production PREPARED order or Canonical Agentd Owner approval.
- A write-once episode claim prevents reusing the same (plan, source, cost).
  A rejected or interrupted claim is indeterminate, not quietly rerolled.

## Original-market maturity

Module: `research/train_v2/fx_cost_shadow_outcome.py`

- Accept only the original frozen and fsynced NO_TRADE receipt and its
  matching local registration, cost receipt, period, and code identity.
- The verifier can run **only after** the preregistered 300,000ms price
  horizon. It reads an original locally SHA256-chained quote, not an
  IB-revised historical bar.
- The exact decision-time source prefix is independently rediscovered
  within the append-only original file by SHA256. The chosen decision
  quote must remain the **first** hash-chain receipt per original broker
  timestamp, as in the pinned original source parser. A later duplicate
  with identical bid/ask is not a new input. Conflicting duplicate prices,
  source-prefix tampering, absent original outcome quotes, early labels
  or source/plan/cost drift fail closed.
- A mature quote must carry a broker `observed_at_ms` in the five seconds
  from the frozen deadline, be available no later than the proof time,
  and revalidate inside the same original source chain.
- The only label is `(future_mid / frozen_input_mid - 1)*10000` bps.
  Neither actual broker execution economics nor historical training labels
  are inferred. A NO_TRADE control has no trading PnL to report.

## Real Pocket4 single-episode evidence

Evidence root (private, on pocket4):

`~/.local/state/heptatrader/operator-evidence/fx-cost-shadow-20261012`

At original research decision **2026-10-12 ~16:12 CST**:

- Plan ID: `3ffc9f142303f5f273553455789a67e2633e265688093a6c33ca6ad9c2606b45`
- Source prefix: `735a88b580e8244a8d3d71b14dd14147509f1b9480c5366ce2489d1d3125306e`
- Frozen decision file SHA256:
  `116c25f752e7fe17261694eb60f789e5c20c3b81a8a6b7fd00e2b3aa32a33c97`
- Original quote's full spread: **0.08934754 bps**.
- Frozen historical P99 stress spread: **3.4837759 bps**.
- Fee p90 roundtrip: **1.4295798 bps**.
- Assumed stressed slippage roundtrip: **4 bps**.
- Precommitted minimum net margin: **2 bps**.
- Total modeled hurdle: **10.9133557 bps**.
- Frozen choice: **NO_TRADE**.
- Decision and qualification persisted **before** original broker-report
  quote TTL; no queries, orders, cancellations, actor update, or model use.

At mature source observation **2026-10-12 ~16:17 CST**:

- Actual **price-only midpoint return**: **+0.44673769796 bps**.
- Native PAPER fills: **NONE requested by this research experiment**.
- Mature outcome file SHA256:
  `98abf51ce51ad6c1bbcfbcb9b155703e72bfeba643532e7dcb34557dd7d9b01d`.
- Cost, decision, and outcome were NOT reselected after future price.

Original quote stream contains same-timestamp callbacks. A premature
strict verifier that compared the raw last quote to the first unique
source receipt correctly failed CLOSED; the distinct-timestamp
first-witness reconstruction was added, regression-tested and then the
unchanged original decision successfully matured. The original failed
attempt was not overwritten or called a pass.

## Independent X230 readback and failure injection

Code: `fx_cost_shadow_x230_readback.py` and
`fx_cost_shadow_x230_faults.py`. The former runs using only Python
standard library on **qian-ThinkPad-X230**. A private, finite
`original-source-through-outcome.jsonl` prefix was sealed on pocket4;
the bundle was copied to X230 over the authorized SSH connection.
The ten payload hashes matched after transport.

X230 independently rebuilt the original 586-row SHA256 hash chain
(584 quote event records), the decision-time prefix, 293 same-time
duplicate quote events, precise input/outcome identities, time fences,
and cost/math calculations. It reproduced the same +0.44673769796 bps
price-only result. Independent readback receipt SHA256:

`f3b773d5ed13bfa1cfa9e6d1de1ceeb701374cdc2304dee3fa30784ccf1ad580`

Six isolated copied-input adversarial variants were **all rejected**:
mutated decision time, changed cost receipt, modified quote source
content, absent maturity source quote, pinned code-byte drift, and a
forged mature quote time. The original Pocket4 research receipts and
X230 accepted bundle were not mutated.

**Trust limit:** X230 verified a file copy originally collected by
Pocket4; this is not a second independent broker quotation, broker
signature, physical immutable storage, product root enrollment, or
canonical LedgerWriter ACK.

## Tests / release gates

- New research preaction, cost and maturity tests: **36/36**.
- Together with the existing Train V2 TTL, original source, local
  preregistration and preaction tests: **107/107**.
- Full existing Train V2 unit suite: **411 tests, 2 skipped, 0 failures**.
- Full historical-training unit suite: **400 tests, 7 skipped, 0 failures**.
- Independent X230 positive proof and **6/6** copied-input negative
  checks were executed on the authorized physical host.
- No historical test-set reoptimization; prior 4-fold IB historical
  Ridge validation remains `NO_TRADE` and `NO_PROMOTION`.
- Existing resident Laya learning and account-wide PnL/NAV blockages
  are unaffected. All previous negative evidence stays retained.

## Remaining actual qualification

This experiment is a **negative control**, not a replacement strategy.
The next separate candidate must produce a **typed, genuinely
available-before-decision predicted EUR/USD *midpoint move* and a
precommitted expected-return horizon** from a version-pinned model.
Raw opaque confidence, random UP/DOWN sampling, a revised historical
label, or a post-outcome price cannot substitute for that forecast.

Only after frozen, unreused, truly future PAPER market observations
show independent cost-after-fill advantages, all original command/
true behavior propensity/production generator-observer signatures,
native durable LedgerWriter/Witness and an authorized Canonical Owner
can production promotion be considered. The active strategy, all
protective exits and the old historical train/test splits stay
unchanged; no automated promotion is allowed by this code.
