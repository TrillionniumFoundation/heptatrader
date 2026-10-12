# 2026-10-12 FX Ridge: real forward maturity and independent physical-Mac replay

## Decision scope

The pre-registered `EUR/USD` Ridge research episode remained **NO_TRADE**.
The frozen deterministic historical Ridge model used 41,243 previously seen
historical training examples; **all original four walk-forward validation folds
selected no profitable trading threshold**. Reusing them for weight fitting was
documented and is **not** an unseen historical OOS test.

The actual predecision prediction was **+0.08164453844351179 bps**, whereas
its frozen stressed price/fee/slippage/margin hurdle was **10.9133557 bps**.
The decision was durably written before the Broker-reported quote expiry, with
no broker queries or orders from the experiment. No model promotion, original
trader propensity attestation, product root, native ACK or LedgerWriter existed.

The predeclared 15-minute horizon matured at 2026-10-12 17:20:00 +08:00.
The original, locally SHA-chained IB PAPER quote delivered a **+0.6254774850
bps** midpoint move; the original forecast error was **-0.5438329466 bps**.
The frozen action was unchanged `NO_TRADE`; both the originally matured
artifact and our independent Pocket4 recomputation retain no-trading and
no-alpha claims. One correctly signed direction is not a cost-adjusted
trading result. No spread/slippage/execution PnL is assumed or backfilled.

## Independent on-pocket4 verification

`fx_ridge_independent_original_check.py` reproduces:
1. Exact frozen model, plan, manifest, registration and decision hashes;
   the plan must have been sealed before the original eligibility window.
2. Original 13 five-minute quote pseudo-closes, feature construction and
   frozen Ridge50 normalized prediction using a second standard-Python
   implementation of the model math.
3. The exact pinned, separate IB PAPER cost receipt, with one stressed full
   spread, both commissions, and 4+2 bps declared slippage/safety reserve.
4. The first valid original quote in the **predeclared** 15-minute future
   window, original predecision source prefixes and matured source prefixes,
   and exact Decimal prediction-error and return recomputation.
5. Explicit `NO_TRADE`, no production owner, no broker-signed quote,
   no model promotion and no execution/PnL attribution.

Pocket4 verified 6 original frozen source prefixes totaling 5,196,053 bytes
and independently reconstructed 4,738 unique normalized recent source
timestamps. Source callbacks are **locally hashed, not independently signed
by IB**. The revalidator shares the original source decoder; it independently
recomputes the forecast and outcome selection, not the upstream broker feed.

Original read-only evidence:

`~/.local/state/heptatrader/operator-evidence/fx-ridge-original-20261012-v1/`

## Mac physical-host verification

`fx_ridge_export_original_bundle.py` creates a **byte-exact**, bounded,
owner-private, write-once portable ZIP with just 7 original source segments
and 12 immutable prefix receipts, plus the necessary frozen model/plan/
cost/decision/outcome research JSON. It deliberately excludes account
credentials, original trading OMS journals, unrelated activities or model
promotion state. The archive SHA-256 was

`1fd2ae188b6fcf76c8ecd38919e43eda6dec686f56e9e739eb9ae0a9da55de55`.

On `MacBook-Pro.local`, Python **3.9 standard library only**:
- Rechecked every source JSONL record, strict sequential `previous` and
  SHA-256 chain, 12 receipt-specific exact prefix byte/hashes, and original
  quote scope/availability.
- Independently reconstructed all 13 predecision input closes, numeric
  features, Ridge weights, 10.9133557-bps cost hurdle and the future
  **first** eligible quote, never a later favorable quote.
- Reproduced +0.08164453844363415-bps prediction and
  +0.625477485044386562956540000-bps subsequent midpoint return.
- Passed **6/6 hostile ZIP variants**, including altered coefficients,
  modified future outcome, local self-resigning of falsified forecast,
  late/reissued registration, original quote body manipulation and a
  self-consistent but cherry-picked later future quote.

The independent verifier and negative tests are retained under the user's
Mac home:

`~/.local/state/heptatrader-crosshost-evidence/20261012-fx-ridge/`

Independent reports are copied into the original Pocket4 operator-evidence
directory. Mac is a **read-only audit host**; this does not constitute a
production independent-owner signed ACK, hardware key attestation, or
proof that the source was digitally signed by IB. An X230 direct connection
failed SSH host-key verification this round; its previous research receipt
cannot substitute for this new experiment.

## Reproduction

```sh
# Pocket4, fixed source/config and research-only original plan:
cd ~/work/heptatrader-fx-ridge-crosshost-audit-20261012
E=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-original-20261012-v1
C=$HOME/.local/state/heptatrader/operator-evidence/ib-history-extension-20261010/ib-live-execution-cost-exact-period-v3.json

PYTHONPATH=research:research/neuron_learning:scripts python3 -B \
  -m train_v2.fx_ridge_independent_original_check \
  --root "$E" --cost "$C" --period ~/.config/heptatrader/pnl-period.json \
  --output "$E/independent-fresh-$(date +%s).json"

# On Mac, use only the bounded offline ZIP, never connect to a broker:
python3 -B fx_ridge_mac_independent_stdlib_verify.py \
  --bundle fx-ridge-frozen-original-portable-v1.zip \
  --output mac-independent-new.json
python3 -B fx_ridge_mac_adversarial_archive_tests.py \
  --bundle fx-ridge-frozen-original-portable-v1.zip \
  --output mac-negative-new.json
```

**Invariant:** every output path must be new; do not edit original frozen
files. Tests do not modify current trading or PnL services, stop or restart
drivers, send broker orders, or register an automatic new model.

## Remaining hard blockers

- Existing 4-fold validation has no cost-positive trading signal; this
  one prospective price result is not proof of alpha. A predeclared batch
  of independent, sufficiently powered future episodes is needed.
- Original trader behavioral propensities are not attested. Product native
  root, canonical owner, signed witness and durable production ACK must
  close before the original admission gate is even considered.
- IB Activity/Flex and exact reset NAV/flows/tax/financing proof remain
  missing. Historical/PAPER callback commissions are not all-in account PnL.
- No independent broker-signed valuation tick for source timestamps;
  X230 host-key trust is unresolved. Do not loosen trust or account scope
  to make experiments pass.

Release: **HOLD / research only / NO_TRADE**. No modifications to the
existing PAPER FX or BTC entry/exit behavior.
