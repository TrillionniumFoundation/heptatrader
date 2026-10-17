# Pocket4 original EUR/USD USD100k Value study — 343 attributable rewards

Research only. This study does not select trades, set live risk or modify
Laya Trainer, evaluator, Gateway, IB Gateway, execution state, live
PAPER drivers, live model weights, any reward ledger or source archive.
All source processing, train fit, testing and private evidence stay on
**Pocket4**.

## Original sources, 2026-10-16

The original Laya `rewards-status.json` had **343 genuinely
input-bound EUR/USD net-PnL financial rewards** (361 economic execution
episodes total including ineligible rows and BTC). The 343 all have
`laya_on_policy=false` and `policy_gradient_allowed=false`.
They are NOT Laya actor-policy rewards or 343 gradient steps.

The rewards belong to three original producer/behavior manifests:
335 from `6957e3fc0a86443581c4fcef86b08d655f128ccfb298d433e57abc6d5f39934c`,
six from a separate manifest, two from another. Mixing them would blend
policies and invite hindsight. **Only the exact 335-row producer scope**
is used. No source is moved to ROG, Mac or another machine.

Independent reconstructions link the 335 rewards to **335 original
REQUEST -> RESPONSE -> DECISION -> durable COMMAND_BINDING** chains
and to **675 unique exact original broker-execution IDs**, each with
an original execution and commission event. Every original execution
price, commission, gross sum, flat position and net after-fee value
matches the saved economic ledger *exactly*. This is not a simulated
or spread-estimated cost target. The original source has prior verified
hash-chained, owner-private append-only prefixes; this research also
rechecks every full byte prefix hash and original request digest.

### Important corrected predecessor contract

The older `financial_value_data.to_row` asserts exactly **25,000 EUR**
of opening quantity. Applying it to these 335 rows correctly fails
`VALUE_REWARD_ECONOMIC_RECOMPUTATION`, but this is NOT financial corruption:
the current original prepared command expressly requires
`quantity_mode=BASE_NOTIONAL_TARGET` with **USD 100,000 reference
notional**, where integer EUR quantity varies with contemporaneous FX
reference prices (observed roughly 88,762–89,955 EUR).

The new research implementation checks **the exact original prepared
command quantity**, 100,000 USD reference notional, command/decision
time order and original action side before recomputing all gross,
commission and net Decimals. It does not relax the old 25k contract
globally or silently relabel the original behavior. Actual USD
reference-notional rounding deviations were <=$1.12 across all 335
commands; this dedicated USD100k research path permits <=$2, never
an arbitrary-sized order.

## Features available *before* the original OPEN

Original `REQUEST.history_snapshot` contains up to 24 timestamped
prior bid/ask/mid quote observations. Inputs consist only of that
pre-request snapshot, original chosen BUY/SELL **before** the open, and
request calendar time: a fixed **17-dimensional** vector with signed
short/mid/long momentum, historical step volatility/range, spreads,
quote age, previous movement signs, window span and time-of-day/week
cyclical encodings.

Reject any future, reordered, invalid or stale input quote, mismatched
original request/decision/order, changed input digest or changed
recorded fee. No exit price, future return, exit clock, realized PnL,
settlement result, or other post-open field is an input feature.

For conservative target availability, the original OMS ledger
`broker_execution` and `broker_commission` events provide the
earliest independently observed close+fee time for EVERY underlying
execution ID. Original open/request precedes all these events.
The longest historic request-to-complete target was nearly 49 hours;
assuming fixed five-minute labels would leak the future.

## One fixed chronological study — NO test-driven tuning

Sort all 335 original requests by original request timestamp/row ID.
Without shuffling or inspecting the target, assign the first 65%
(**217**) to eligible train, the following 15% (**51**) to a strict
ignored/embargo partition, and final 20% (**67**) to historical holdout.
**Every** train reward was already available before the first
holdout request at 2026-10-15 13:41:52.586 UTC; the latest train
outcome was available by 2026-10-14 18:10:17.111 UTC.
The holdout comprises 25 samples Oct15 and 42 samples Oct16.
This is an already-seen historical study, NOT a newly minted
independent future market window.

Train-only standardization, fixed 5-sigma clipping, fixed 17-feature
linear value readout, 20 deterministic IRLS Huber updates,
Huber delta $25 and ridge coefficient 20. No hyperparameter is
selected on the holdout. The model predicts the **observed original
behavior's net USD** under actual recorded reported commissions.
It cannot estimate the unobserved counterfactual had that original
strategy chosen HOLD or an opposite side.

## Actual results on Pocket4 (historical Oct15–16 67-sample holdout)

| Predictor | Mean absolute error (USD), lower | RMSE (USD), lower | Huber error (USD), lower |
| --- | ---: | ---: | ---: |
| Zero net | 24.3067 | **32.6758** | 14.2272 |
| Train-only average reward | 21.5482 | 32.8197 | 13.1199 |
| **Train-only median reward** | **21.4717** | 33.2338 | 13.2211 |
| Train-only negative average fee | 22.8937 | 32.3700 | 13.4649 |
| **New 17-D Huber/ridge Value fit** | 21.7642 | 32.8910 | **13.0097** |

The new model made **20 real CPU optimizer updates** and changed
all 17 feature coefficients, but **did not beat** trivial
train-median MAE or zero-prediction RMSE. Huber error decreased
slightly against the train average, not consistently across losses.
It predicted no positive-net transactions on the 67 heldout
episodes, 21 of which were actually positive, so it does not
support executing new trades.

Under this one behavior scope: original gross -$1,065.30617,
actual reported commissions $1,340.096875, realized net
**-$2,405.403045** over 335 episodes. The selected 67 historical
holdout net is **-$300.606022**. Fees are actually deducted;
financing, taxes and other unreported/non-execution costs are NOT
included. No claim is made about current account-wide NAV or the
profitability of a hypothetical policy that skips trades.

## Private actual execution receipts

Executed in isolated **user systemd scope** on Pocket4, under 1.5 GiB
memory limit, zero swap allocation and 100% CPU quota; GPU, production
training/evaluation, live quote feed, broker or trade services untouched.

Create-only private evidence on Pocket4:
`~/.local/state/heptatrader/operator-evidence/value343-chronological-oos-20261016/`

- `study-v1.json`, SHA256
  `c0974e2c18d61f3e2837911bfb7e39c67be5668bab1cf09aca3bc48824e0c3ba`.
- Independent second full read and fit `study-v2.json`, SHA256
  `3153f10cd01f11eaf99beab6a7e120808f3ff69cafe69f5a178a010db761ac13`.
- Tighter exact USD100k reference-notional/subject scope
  `study-v3.json`, SHA256
  `c04bbef5ce1973d52e957aace796303b20b8b396874726735b6d82a156149df1`.
  Source implementation SHA256
  `574972ecfe7370f148e5419b5b8aac682e9ecf04a899d06d2ed96f88782074e4`.
  The strict <=$2 original reference-notional check holds for all 335
  genuine original prepared commands and changes **no model coefficient
  or evaluation score** compared with the first two independent runs.

- Final strict 100K source-graph variant `study-v4.json`, SHA256
  `dc0e463e6acc2a11a095afcc7623498959327f00d2835b9b2a0bdc8cf659f060`.
  Pinned source implementation SHA256:
  `73354f5d1fdaa0ace5c60211597c9c2d4d2f5f74ecb2f5e9ecef63cb206f246c`.
  The original 100K producer was independently re-verified to have
  **335 valid original bindings and zero malformed attribution graphs**.

The original request-derived 335 rows, chronological cutoffs, all
train-only model coefficients, 20-step result and holdout metrics
match **exactly** across all four experiments. The full receipts differ
because the live reward-status timestamp and append-only OMS journal
naturally advanced; therefore **do NOT claim byte-identical
whole-file replay**. Both original source hashes are recorded.

An additional create-only four-run reproduction comparison is held
privately on Pocket4 at:
`~/.local/state/heptatrader/operator-evidence/value343-chronological-oos-20261016/four-repeat-fixed-model-metrics.v1.json`.
SHA256: `e6445784a6e4a894a2d7f18f876f02a7d16fc056ed41a20c6639fc48f78e16fe`.
The comparison records all four actual receipt hashes, separate source
snapshots, strictly equal train-only numerical coefficients and holdout
scores, and explicitly refuses whole-report byte equality.

Exact-source Pocket4 regression after the v4 source correction: **11/11
focused adversarial, 116/116 Source and 705/705 historical-training
unit tests pass**, documentation and whitespace checks pass. This does
not claim all project-wide Core or hosted GitHub CI passes.

The versioned source/fit script is
`research/neuron_learning/laya_cell/value343_oos.py`.
Neither the repository nor PR stores original prompts, model API
messages, private account values, per-trade returns, raw quotes or
original broker journal records.

## Disposition

**TRAIN ENGINEERING: PASS. FORWARD VALUE / ECONOMIC EDGE: FAIL TO
DEMONSTRATE.** Do not infer on-policy action gradients from these
reward rows, self-promote this Value predictor or use its historical
scores to approve trading. Before considering a new version, require
future precommitted model predictions, original outcomes after
settlement, strict account cost attribution and consistent superiority
over simple train-only models across genuinely later source windows.
