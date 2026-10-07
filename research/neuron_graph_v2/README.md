# Hepta Neuron v2: 256-D contracts and staged meta-network research

This is an additive research module, not a new trading driver or another full
Laya trainer. FX/BTC execution, native risk, model ownership and the existing
Laya/mmBERT checkpoints remain outside this package. There is no order,
activation, account, credential or model-selection API.

## Confirmed starting point (2026-10-08 JST)

The running FX producer has behavior profile
`EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL`, confidence 0.62 and
reference notional budget USD100,000. No FX churn profile is attached. The sizing
rule floors USD budget / executable-side reference quote into integer EUR units;
it is not EUR100,000 and not a guarantee of final fill cash plus fees. Reapplying
this deployment is neither needed nor part of this change.

The original temporal-GRU + Laya probability-gate prospective result is retained:
10 observed, 2 censored, REJECTED. Mean Brier improvement versus numeric alone
was -0.0001852375. Expanding nodes or vector width does not retroactively turn
that into 1+1>1. Numeric-only remains an indispensable independent baseline.

## Implemented modules

- `contracts.py`: versioned NeuronOutput[256], immutable context, exact evidence
  identities, event/availability/freeze clocks, descriptor metrics and explicit
  uncertainty meaning. Price probability entropy is NOT calibrated win rate.
  USD-PnL and price-bps targets cannot silently mix. The legacy 32-D experiment
  is not relabelled: `lift_legacy32` is an explicit norm-preserving 32-to-256
  isometry with its own basis ID and no claim of new information.
- `observers.py`: deterministic causal Execution, Regime and Risk descriptors.
  They consume available real facts and keep missing fees/latencies missing.
  They do not claim learned predictive expertise, invented probability vectors,
  native risk enforcement, or policy authority. Execution data must match the
  period/behavior and may not postdate the original input. The common context
  must be supplied by an independently verified input producer; a checksum alone
  is not proof of trustworthy evidence or canonical owner authentication.
- `graph.py`: 256-D external embeddings with a 32-D internal compute bottleneck.
  Soft routing is the default. Explicit top-k (max four) and zero/one/two/three
  dynamic attention-round ablations share the same class. Initial price output
  is the neutral convex mixture of actual forecast nodes; descriptor nodes add
  context but cannot supply fabricated uniform forecasts. At least one real
  forecast survives sparse selection. Canonical node ordering and stable
  sorting handle ties without epsilon perturbations.
- `protocol.py`: a pure immutable paired-diagnostic contract. Model identity,
  chronological train/freeze/window times, paired arms and missing slots remain
  explicit. MID-Brier and actual-command net-USD error are separate targets.
  This helper is NOT a replacement for the existing canonical financial-forward
  protocol, its witnesses, actual settlement or independent statistical rule.
  It returns descriptive diagnostics only and cannot select a model.
- `probe_gpu.py`: synthetic-fixture kernel/autograd verification on pocket4's
  gfx1150 GPU. It does not train a market expert or generate efficacy evidence.
- `checkpoint_probe_once.py`: a bounded same-real-input engineering probe of
  the complete official Laya-mmBERT and Laya-English checkpoints. All local model,
  encoder and tokenizer assets are checked against fixed upstream Git/LFS
  identity; full state dictionaries load with strict=True and remain frozen.
  The paired encoding uses identical UTF-8 input and no tokenizer truncation.
  This is NOT a net-PnL A/B and performs no model training or selection.

These graph operations happen after expert outputs are available. Top-k reduces
aggregation, not the computation already spent on upstream encoders. A genuinely
compute-saving dispatcher is a separate lightweight pre-inference experiment.

## User's implementation sequence, with truthful promotion boundaries

| Step | Implementation / acceptance boundary |
|---|---|
| 1 FX original 0.62 / USD100k / no hysteresis | Already observed running; no repeated trade changes |
| 2 Laya/mmBERT main | Retained; no checkpoint overwrite |
| 3 Numeric Temporal Neuron | Existing trained GRU reused as a baseline |
| 4 Laya + Numeric fusion | Existing failure retained; no expansion claimed to prove synergy |
| 5 ModernBERT-large challenger | Full fixed weights and same-input GPU encoding now testable; task-specific paired future PnL still required |
| 6 NeuronOutput[256] | Implemented versioned contract; legacy 32 remains distinguishable |
| 7 Execution / Regime / Risk | Causal descriptor implementations; trained experts not claimed |
| 8 Soft then top-k | Both structural paths, default soft; independently frozen future comparison before activation |
| 9 Dynamic graph attention | Research forward/backward and permutation checks; no market qualification yet |
| 10 Distributional value | Deferred until enough distinct exact closed episodes and tail/calibration support; row count alone is not a guarantee |
| 11 IQL/CQL plus SPIBB fallback | Deferred: behavior coverage, trajectory semantics and a real baseline policy are prerequisites; remote confidence is not an action propensity |
| 12 Independent validation + canonical owner | Required externally; this package cannot mint acceptance or order permission |

The next fusion experiment must freeze full Laya encoder features, numeric-only,
Laya-only, a simple fixed mixture and the learned gate as distinct arms before
fresh inputs. An initial Laya representation reduced to seven probabilities is
not equivalent to exposing the full encoder state. Whether richer complementary
information helps is a hypothesis, not a conclusion drawn from the already
observed failed window. Preserve all HOLD, late, gap and censored inputs; never
retune cutoff, temperatures or routing on that same window.

Fair English A/B uses the same exact original input, quantity-policy identity,
training-end and prospective outcome protocol. Default English context 512 is
not comparable with silently untruncated multilingual context; measure actual
token counts and reject an unpaired over-budget case. The encoders have different
widths (768/1024), so native-width readouts have different parameter counts; a
separate predeclared equal-256-D adapter ablation is useful but not magically equal
information. Record encoder latency/memory separately from predictive quality.

A USD100k policy's rounded EUR quantity is a sample attribute, not a new policy
identity every time the exchange rate changes. Later PREPARED quantity/reference
is economic-verification data; it must not appear in earlier request-time features.
This module does not secretly reactivate old EUR25k financial services.

## Reproduce isolated checks

From the repository root, with the already installed pocket4 PyTorch environment:

```sh
PYTHONPATH=research python research/neuron_graph_v2/tests/test_neuron256.py
PYTHONPATH=research python research/neuron_graph_v2/probe_gpu.py --output /new/path/gpu-proof.json
```

All unit-test frames, rewards and gradients are explicitly synthetic. The full
checkpoint probe uses one existing real WATCH input, not a newly generated market
result. No cold-start single measurement is presented as a stable speed ranking.
This code may be committed on main without being wired into resident execution.

## Primary references

Laya model card: https://huggingface.co/convaiinnovations/laya
ModernBERT-large config: https://huggingface.co/answerdotai/ModernBERT-large/blob/main/config.json
Graph Attention Networks: https://arxiv.org/abs/1710.10903
Safe Policy Improvement with Baseline Bootstrapping: https://proceedings.mlr.press/v97/laroche19a.html

These sources motivate experimental designs, not claims that HeptaTrader can earn
profits. Calibration, uncertainty, behavior-policy evidence and effect authority
remain separate concepts throughout.
