# Frozen original-price five-session observer

The historical 24-feature price model needs an observation path built from the same closed-bar feature contract. This path records new original-source predictions before their targets become known. It also records missing opportunities and local persistence failures.

Both entry points are research-only, restricted to Pocket4, and have no broker client or order submission path:

- `scripts/research_laya_price_five_session.py`: bounded original-source observation.
- `scripts/research_laya_price_cost_proxy.py`: frozen historical action-book cost diagnosis.

## Frozen assets and source identity

The observer loads the previously selected 24 → 32 tanh → 7 tiny MLP. It verifies the exact weight bytes before parsing them with safetensors. NumPy inference reuses the train-only mean, standard deviation, clipping rule and class prior; no training or Torch import is required.

The observer config uses schema `heptatrader.frozen-price-observer-config.v1` and requires:

| Field | Contract |
| --- | --- |
| `preparation_path` | Immutable preparation containing candidate, train selection, dataset contract, training result, normalizer and prior identities. |
| `source_config_path` | Original-source configuration with automatic trading disabled, no canonical owner endpoint and no completed historical sources. |
| `file_pins` | Exact path/SHA256 pairs covering the above assets, training protocol, PnL period and the actual project runtime dependencies. |

The runner rejects omitted mandatory pins. Deployment also records the actual imported project module paths and checks that they belong to the pinned worktree and are covered. Pins are checked at startup and at least every minute. Runtime package versions and the deployed commit belong in the private deployment receipt.

`original_price_source.py` reads a fixed 75-minute original-quote window. It verifies the source archive inventory and sealed prefixes, checks source rotation, and filters both event time and local receipt time against the requested cutoff. Old sealed segments remain hash-verified even when their JSON need not be decoded. Historical bars cannot fill gaps in this source.

The existing feature builder requires 13 consecutive complete five-minute bars. Its quote coverage rules, feature order, returns and calendar fields remain unchanged. There is no forward-fill or imputation.

## Admission and fixed schedule

Registration is durable before observation begins. It freezes the model/config identity and the following rules:

1. Check original-source admission at fixed 60-second slots for at most seven days.
2. Require the existing source-readiness rule, a new original quote, fresh bid/ask evidence and the complete closed-bar warmup.
3. Persist the first verified READY evidence, then commit the next **full** regular calendar session and the following four sessions.
4. Start each session's eligible anchors after a structural 65-minute warmup. The default regular calendar produces 272 anchors per session, or 1,360 total.
5. Keep every committed opportunity in the denominator. Later gaps, downtime or restarts cannot move the schedule.

Admission uses source quality only. It does not inspect model scores or choose a favorable outcome window. A persisted first-READY receipt determines the same schedule after a restart, including recovery from a failed plan write.

The calendar uses the existing New York regular FX session profile. It is not proof of exchange holidays, exceptional outages or actual broker availability. The registration hard end is calculated from the latest permitted admission and the fifth session's last outcome deadline; an arbitrary ten-day cap could truncate this contract.

Seven days without admission produces `START_FAILED_NO_ORIGINAL_SOURCE`. Waiting has no eligible-opportunity or successful-coverage claim.

## Causal cutoffs and durable receipts

For an eligible anchor A:

- Input quote event and receipt times must be no later than A.
- Input evidence, prediction, local seal and commit must be durable within A + 5 seconds.
- The target is the next five-minute bar.
- Target quote event and receipt times must be no later than A + 5 minutes.
- Outcome assessment has a further 60-second persistence grace. That grace does not admit later market information.

The loop prioritizes the new prediction over grading the preceding target. Missing data, late persistence and partial commits remain censored; subsequent files cannot turn an expired opportunity into a successful prediction.

Recovery verifies the original input, prediction, seal, commit and assessment content. An orphan assessment without its final receipt is censored. Persisted actual event times form a clock floor across restarts; scheduled future anchors do not. Integrity or bootstrap failures publish a FAILED status instead of leaving a stale OBSERVING status.

These are local durable receipts, not independent externally timestamped attestations. Report this evidence scope with the results.

## Bounded deployment

Use a dedicated private campaign directory and a fixed source worktree. Do not repoint an active campaign to a different model, config or code revision.

A startup canary uses the same registration and can be repeated to verify recovery:

```sh
python -B scripts/research_laya_price_five_session.py \
  --config /absolute/pinned-observer-config.json \
  --output /absolute/private-campaign \
  --max-cycles 1
```

The formal service omits `--max-cycles`. A file lock permits one writer. Existing market/execution services, maintenance holds and kill-switch state do not need modification.

The deployment profile uses CPUQuota 50%, MemoryMax 768 MiB, MemorySwapMax 0, Nice 15, NoNewPrivileges, UMask 0077 and RestrictAddressFamilies AF_UNIX. Limit BLAS/OpenMP to one thread. This is a CPU file reader; it has no network broker connection. Set the service runtime cap to cover the registration's remaining hard end, allow a short graceful stop, and use no automatic restart loop after integrity failures.

Before activation, verify:

- Actual config/assets and imported module locations.
- The real 1,360-anchor plan through write, bounded JSON read and contract validation.
- Original-source read, NumPy inference and durable writes within the 5-second budget under the deployed resource limits.
- Two bounded starts with an unchanged registration identity.
- A formal service command without the canary limit.

An offline timing exercise must use a separate directory, real completion timestamps and an explicit non-forward label. It cannot populate campaign predictions or outcomes.

## Status and interpretation

`status.json` distinguishes waiting, fixed schedule committed, observing, censored/completed, start failure and integrity failure. It reports the admission deadline, hard end, checks and missed check slots, actual forward predictions, observed/censored/pending counts, paired NLL/Brier scores and per-session coverage.

A bounded canary ends as `CANARY_COMPLETE_RESUMABLE`. A formal service that is waiting for the original source has produced zero forward predictions. Neither state establishes predictive or economic qualification.

The observer does not register a new formal Laya alpha trial, promote a model, change execution authority or enable trading. Five sessions provide operational and paired-score evidence; any later statistical or economic decision requires a separately reviewed protocol.

## Historical action-book cost diagnosis

The cost proxy fixes the candidate, train prior, train-only class conditional returns and an action rule before scoring. It uses 100,000 EUR notional and a hypothetical USD 4 round-trip fee in its action gate. The gate uses only the current known bid/ask spread and price. The action is LONG or SHORT only when the signed expected return strictly exceeds the declared cost gate; otherwise it is FLAT.

Eligibility is built from the fixed calendar and 13 known input bars. Future target availability cannot remove an otherwise eligible current opportunity. Overlapping historical dataset features must match exactly.

The script persists all actions before accessing their future target bars. It then reads that sealed action book back from disk and verifies that scoring preserves every original action field. The fee scenarios of USD 0, 4 and 8 use the same actions and exposures. Unscorable trades remain in the book, and aggregate net/drawdown metrics stay unavailable when any active trade cannot be priced.

The result is explicitly a `HISTORICAL_ZERO_LATENCY_NEXT_BAR_BIDASK_MARK_PROXY`. It assumes next-bar opening and closing bid/ask marks without demonstrated order latency, depth, fill or slippage. Zero historical spreads and zero close-to-open jumps do not prove attainable prices. Changing a fixed fee column does not repair missing bid/ask precision.

Report input availability, zero-spread frequency, direction, daily results, costs and concentration with the marks. The largest-trade share uses the sum of absolute individual net results as its denominator, not aggregate net profit. Historical validation and already inspected test segments cannot be presented as untouched forward evidence.

## Focused verification

The focused contract suite covers schedule boundaries, latest admission, late information, missing targets, source rotation/integrity, persistence deadlines, orphan recovery, assessment binding, clock-floor recovery and current-input eligibility independent of future labels.

The three observer/cost contract files use unittest and are discovered by the repository's core lane. Frozen NumPy identity tests belong explicitly to the learning lane, whose CI environment installs pinned safetensors 0.8.0. Missing research dependencies fail that lane instead of turning into skipped acceptance.

```sh
PYTHONPATH=scripts:research/neuron_learning:tests/python python3 -m unittest \
  test_laya_price_cost_proxy \
  test_laya_price_five_session \
  test_laya_original_price_source_window \
  test_laya_price_original_live_features \
  test_laya_price_year_gpu
```

Run `tests/python/test_laya_frozen_price_numpy.py` with the NumPy/safetensors research runtime. The CI scope keeps learning feedback enabled for a change to the inference module or its test. Numeric agreement with the already frozen training result is checked without retraining.

Keep private broker receipts, data, weights, action books and deployment evidence on Pocket4. Only source, tests and documentation belong in the repository.
