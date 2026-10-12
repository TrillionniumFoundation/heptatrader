# Train V2 2026-10-13: exact finite 3x4 original-price cohort audit

This is an optional read-only research reviewer, stacked on the existing
finite original price-only outcome reconciliation branch at d85d960.
No PAPER order/Execution, quote source writer, systemd capture timer,
original previous trial, existing outcome receipt or Laya model is changed.

## Why this is necessary

The original 00:50 NEW cohort has genuinely observed 5-, 15- and
30-minute price outcomes. The 60-minute original source price is
not yet qualified for sealing at the first check because the original
append-only NUMERIC segment covering its endpoint is still open.
Showing only the three successful observed horizons would omit the
original predeclared total of three source slots and four horizons.

This code always displays all TWELVE immutable source/horizon cells.
A future or missed capture is **not** a zero-return outcome. A matured
label without a source segment seal is WAIT, not an observed quote.
The 5/15/30/60-minute horizon decision times are independently bound
to the same original 00:50 input receipt. Source identity, original
one-shot timer result, as-of 6-quote horizon and the existing exact
post-maturity output are checked by the pre-existing strict verifier.

The reviewer loads the original quote archive ONCE, from already SEALED
segments only, independently rederives every existing price label and
rejects rehashed changed PRICE or unauthorized effect fields.
No output result is modified or refit; there is only a new create-once
audit snapshot with exact original campaign/timer/code/source SHA.

## Actual first independent 12-cell audit

Campaign:
0b0f40d0013199eb43fdb97886744b80e29f1479955c695f7d239d07bebf1ece

Frozen original decision: 2026-10-13 00:50:05.110 UTC+8,
original quote locally available 00:50:04.892 with original IB TTL.

At 2026-10-13 01:53 UTC+8, actual status:
- Slot 0 / 5m: OBSERVED original midpoint move +0.268878641625... bps.
- Slot 0 / 15m: OBSERVED +1.702898063625... bps.
- Slot 0 / 30m: OBSERVED +3.092104378688... bps, **newly sealed in
  this continuation**, independent second read confirms.
- Slot 0 / 60m: WAIT_ORIGINAL_SEALED_ENDPOINT_COVERAGE; matured but
  the source segment covering the endpoint has NOT sealed yet.
- Slot 1 (02:10 original timer) and slot 2 (03:30 original timer):
  all eight horizons remain NO_SOURCE_INPUT_NO_PRICE_OUTCOME because
  those slots have not yet arrived. They are NOT eight missed inputs.

All three observed absolute hindsight price moves are below the
original frozen hypothetical spread+commission+slippage+margin hurdle
10.9133557 bps. This statement is ONLY about price excursions, not
expected return or an executable strategy. A large hindsight move
would still have financial_alpha_qualified=false and no model effect.

Original 30-minute sealed result:
~/.local/state/heptatrader/operator-evidence/train-v2-fixedslot-20261013/
timed-campaign-v1/slot-00-h30.outcome.json

SHA256: 37f5417cfa7a71597f404f0047a4073b01212de3526a6ef1bc27ba752330b308

First 12-cell independent read-only audit:
~/.local/state/heptatrader/operator-evidence/train-v2-price-cohort-audit-20261013/
first-complete-three-horizon-check.json

SHA256: 4ee5ccc5ec1981d50892415dce62b4f8c14b779f3838b704977962840c1c6721

These existing bytes are immutable and are not rewritten after later
quote segments or horizons seal.

## Adversarial test boundaries

The tests verify true 3x4 denominator, future slot vs real missed slot,
partial timer/input source refusal, symlink refusal, rehashed price
forgery, maturity before source, missing sealed endpoint, source original
identity loss, idempotent unchanged auditor digest, price movement far
above a hypothetical cost hurdle never granting model/financial alpha,
and censored gaps staying UNKNOWN rather than zero PnL.

## Subsequent acceptance

1. Slot 0/60m can be independently observed **only when** the original
   NUMERIC source segment later seals and the exact endpoint is covered.
   Do not force writer rotation, interpolate a quote or loosen maturity.
2. Existing 02:10 and 03:30 user timers are unchanged. Their real
   systemd results must be checked after they actually occur.
3. A true new independent model forecast, original REQUEST/PREPARED
   sealing, native Generator/Observer LedgerWriter with durable witness,
   price-vs-fill economics and current signed Artifact Owner remain
   entirely separate and CLOSED. This price-only corpus cannot satisfy
   those by retrospective labelling.
4. All stacked draft PRs need exact-commit CI and separate review.


## Exact pinned code-tree regression (2026-10-13)

- New denominator and economic non-promotion adversarial tests: 14 pass.
- Historical research: 400 pass, 7 pre-existing skips.
- Full Python core discovery: 2,436 pass, 23 pre-existing skips.
- Source/build/documentation lane: 106 pass.
- Existing source writer and original 02:10/03:30 timers were not
  modified or restarted. The user timers remain active with original
  250ms delivery accuracy, zero randomized delay and 384 MiB service
  budget. Real delivery is not guaranteed until the actual service logs
  and sealed one-shot receipts exist.
- The scorecard is an extra immutable READ-ONLY receipt and does not
  create or overwrite any original price label or broker order.
