# Current100k readout study, 2026-11-03

## Scope and result

The frozen model has a normalized 768-dimensional encoder output and a 769-parameter
FP32 monetary head. All 54 historical labels had already been inspected. These
are development results, with no held-out, prospective or profitability claim.
A single bias edit improves mean calibration. The constrained analytic readout
and SGD given the same two-second construction ceiling achieve essentially
identical errors. Direct editing has not beaten that training control.

No production model, service or trading authority was changed. Private prompts,
labels, features and weights remain on Pocket4. Every candidate was serialized,
reloaded and checked by actual FP32 nn.Linear forward on the same CPU features.

| Arm | MAE USD | RMSE USD | Output std USD | Joint L2 |
| --- | ---: | ---: | ---: | ---: |
| Parent | 16.786469 | 23.379921 | 0.005976 | 0.072193 |
| Bias mean correction | 16.334965 | 23.263678 | 0.005976 | 0.090183 |
| Constant mean | 16.335332 | 23.265109 | 0 | 0.125266 |
| Constant median | 16.167994 | 23.373604 | 0 | 0.147760 |
| Constrained analytic readout | 16.345895 | 23.223326 | 0.092902 | 0.100000 |
| Original 16-step SGD | 16.786469 | 23.379920 | 0.005976 | 0.072193 |
| Two-second-ceiling SGD | 16.345895 | 23.223326 | 0.092903 | 0.100000 |
| Common-direction half ablation | 16.786175 | 23.379925 | 0.004252 | 0.080682 |
| Covariance-direction patch | 16.312187 | 23.227420 | 0.081987 | 0.100000 |

Constants are analytic baselines outside the original norm constraint. Other arms
satisfy joint L2 <= 0.1 within FP32 tolerance. Scores above are raw; future scoring
must apply the fixed 15 USD cap uniformly and retain raw predictions.

The one-coordinate bias edit is -0.0232850835 scaled units. Its measured forward
shift agrees with the formula within 0.000000954 USD. CPU parent outputs differ
from original GPU logs by at most 0.000003815 USD. No paired metric mixes backends.

## Measured mechanism

Feature cosine min/median/max is 0.986923 / 0.996844 / 0.999349. Mean-vector norm is
0.998225 and centered RMS distance is 0.059563. Centered rank is 53 and entropy
effective rank 13.485: a strong common component, not literally identical vectors.

For these 54 vectors, any linear head with weight norm <= 0.1 has output std at
most 0.354269 USD and pair separation at most 1.617224 USD. Target std is 23.265109
USD. The std bound is 100 * 0.1 * largest_centered_singular_value / sqrt(54), an
operator-norm bound independent of optimizer. It is an empirical limitation of
this feature matrix and constraint, not proof of future impossibility or that all
target variation is predictable.

With identical unit features and bias, zero-init SGD at lr .05 in Smooth L1's
quadratic region predicts mean_target * (1 - .9**16) = -10.205356 USD. Actual mean
is -10.198044 USD, a 0.007312 USD difference. Original clipping is inactive and
residuals remain within beta=1. This supports underconverged common-mean fitting.

Four fixed requests were probed. Maximum parent change: action flip 0.001566 USD;
10-pip price shift 0.037657 USD; supported trend reversal 0.011801 USD. JSON
reserialization has zero difference. One reversal exceeds 2,048 tokens and is
recorded unsupported without truncation. These are sensitivity tests, not outcomes.

## Cost and pre-open feasibility

The 54 original forward receipts sum to 309.988 seconds; 15 valid probes sum to
76.620 seconds. Extraction resumed after a context-limit failure: final-manifest
wall time covers only the last invocation, not the total extraction cost.

The base recipe group used 0.9702 wall / 0.8940 CPU seconds. The extra SGD arm
reached 8,192 steps in 1.0524 wall / 1.0569 CPU seconds. The covariance patch used
0.000603 wall / 0.000770 CPU seconds. All fit the two-second allowance; utilization
differs and shared encoder cost is separate. The 16-step arm is only a reference.

Warmed CPU, four threads, joint BUY/SELL batch2 took 22.512, 19.524, 14.225, 15.956
seconds for four historical requests. Load took 3.804 seconds and peak RSS was
2,770,612,224 bytes. GPU was uninitialized. Features and parent scores matched the
single-row CPU cache exactly. This misses the approximately 5.6-second pre-open
window. A separate authorized, resource-admitted HIP experiment is still needed.
No prospective plan should be frozen on an unproven backend.

Seven numerical/identity tests passed without loading a pretrained model. Future
benefit needs new pre-open requests, valid paired outcomes and the full horizon.

## Private evidence identities

Root: `/home/alex/.local/state/heptatrader/operator-evidence/current100k-weight-study-20261103-r1`.

| Artifact | SHA-256 |
| --- | --- |
| Original head | `1f3071b3b1aaa89966b2d72abb2aec3f0841e2eb9bb24964abc54f30ac22b759` |
| Frozen dataset | `cfd5d40c9979c5f4a3a4c0b2b14c731ba44663b9e6e3058553cec7feda37aa35` |
| `features/original-fp32.safetensors` | `22d0afbf910ae6b7161b6c5476fdf8e6e921895514c6b6842d0e239f4cc8c483` |
| `candidate-bundle.json` | `9068e3c6bbb6f753646e9b5fccc354099e3fd51107ce603d26961fbcc7b191b7` |
| `summary.json` | `06fd382620851b9aa1534d11b928ee7f604307f151eaccd76df857940d53eb9f` |
| `batch2-threads4-benchmark.json` | `cc2282ffe20469d03cd1b58b994789315929eedd91b4940b96f0db1bce60d302` |
| Actual pinned loader model.py | `eb1603de93f3ed833b7cd2f47ea8bb085efb65180ea04a918b470a598ea5910b` |

The private candidate bundle lists exact per-arm paths/hashes and all training
identities. Its 54-row dataset is development-only; no arm has been selected for
live trading from these scores.
