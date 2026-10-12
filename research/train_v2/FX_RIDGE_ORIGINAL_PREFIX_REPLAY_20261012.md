# Independent original-source replay for the finite FX Ridge cohort

This research-only checker is deliberately separate from the original
`fx_ridge_finite_campaign` producer. It uses only Python standard library
and does **not** import producer Train V2 modules, use IB credentials, place
orders, change any strategy, train, or promote a model.

## What is verified

1. Exactly four immutable, predeclared future EUR/USD windows spaced
   20 minutes apart, with fixed 15-minute outcomes, 4.5-second decision
   deadlines, unique plan IDs and a common frozen Ridge50 model.
2. Byte-exact model, feature specification, cost receipt, registration,
   local SQLite WAL rows and individually sealed registration JSON.
3. For each real, eligible decision, read only exact frozen original
   IB PAPER quote-file prefixes. Recompute every locally chained record
   and header, verify all 13 causal five-minute pseudo-closes, reproduce
   the 8D feature vector and Ridge prediction using independent arithmetic,
   and reproduce the original spread/commission/stress hurdle.
4. For each mature price-only result, reread exact mature source prefixes
   and pick the earliest eligible **original** quote in the precommitted
   5-second future observation band. Verify selected original receipt,
   quoted midpoint, Decimal return, prediction error and unchanged NO_TRADE.
   Historical OHLC revisions and later favorable quotes are prohibited.
5. Reject any mutation or unauthorized upgrade. Missing or failed
   windows remain CENSORED or UNRESOLVED and never gain sample credit.
   Final counts must match exactly the producer's frozen file hashes.

`fx_ridge_campaign_challenges` separately attacks **private copies** of
a previously matured real original-market research episode. Nine rehashed
copies alter model predictions, numeric features, original source identity,
action, cost, future result, forecast direction or selected quote. Each
must be refused; the genuine private evidence is never changed.

## Commands on pocket4

```sh
cd ~/work/heptatrader-fx-ridge-prefix-replay-20261012-1847
export PYTHONPATH=research:research/neuron_learning:scripts
E=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-finite-campaign-20261012-v1
A=$HOME/.local/state/heptatrader/operator-evidence/fx-ridge-finite-independent-20261012

python3 -B -m unittest discover -s tests/python \
  -p test_fx_ridge_campaign_independent.py -v

python3 -B -m train_v2.fx_ridge_campaign_independent \
  --root "$E" --output "$A/replay-FRESH-NAME.json"
```

Use a new output filename for every audit. Verify each intended result is
mature before claiming any price observation; unknown future outcomes remain
pending. Source data, full broker quotes, local account identifiers, tokens
and frozen model weights are not committed.

The checker proves local bytes and mathematical reconstruction, **not**
independent IB-signed quote timestamps, actual trade fills, real execution
slippage, native root/Owner/LedgerWitness admission, or economic alpha.
Originally inspected four historical folds all chose NO_TRADE.
Promotion and trading release remain HOLD even if all four price-only
forecasts eventually have correct directions.

The producer's already-active transient unit is untouched. Its original
hash-pinned source and precommitted observation windows remain unchanged.
