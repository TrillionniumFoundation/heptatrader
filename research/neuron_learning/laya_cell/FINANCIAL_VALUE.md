# Independent Laya financial value readout

## What is trained

A full, strictly loaded pretrained Laya checkpoint remains frozen. Its encoder
produces a masked-mean, L2-normalized 768-dimensional representation. A new
independent 768-to-1 linear readout plus bias (769 coefficients) learns realized
EUR/USD roundtrip net USD after actual reported commissions. This monetary
readout must be initialized because the original checkpoint has no such USD
regression target. The pretrained backbone is not randomly rebuilt, and existing
scorer/action adapters and their selected/candidate files are not updated.

The exact local infer prompt is retained. The selected direction, fixed 25,000
EUR request size and recorded behavior-model identity are features known before
the OPEN. Neither the close fill, future return, commission amount, net reward,
realized holding duration nor post-outcome rationale is input. This is a value
estimate conditional on the recorded remote behavior, not a Laya on-policy
critic or a policy-gradient authorization.

Initial inspection found 1,283 tokens in the original prompt and 1,328 with value
context. The first 1,024-token profile correctly rejected it without truncating
or emitting a candidate. The independently revised financial profile caps at
2,048, below the strictly verified encoder's 8,192-position capacity. The existing
1,024-token price/scorer profile is unchanged. Failed first-run evidence is kept.

## Eligible target and economic checks

Before fitting, replay the CURRENT committed OMS prefix and registered exact
attribution graphs. Only settled, fee-complete, flat-to-flat, exact-input-bound
EUR/USD rewards with original 25,000 EUR exposure are eligible. Recompute signed
cash flows and all actual commissions; duplicate executions, residual positions,
changed input hashes, unknown costs and incompatible profile/currency reject.
Only a reward that remains present and byte-equivalent in current replay can be
used by a child worker. Old unbound history is not magically recovered.

Missing fee is not zero. Gains and losses are both retained. No account NAV,
unrealized position mark or remote confidence is substituted for reward.
Financing, taxes and unreported non-execution charges remain outside this target.
The prototype excludes values outside +/-$800, explicitly, rather than clipping
them into false monetary labels. BTC requires a separately reviewed value scope.

## Bounded update and native evidence

Targets are scaled by fixed $100 units. Sixteen SGD steps use Huber loss and
an explicit gradient bound. The new readout starts at zero, giving a neutral
zero-dollar baseline, and has a 0.1 absolute L2 update bound. A relative norm bound
against a zero initialized head would be undefined, so this does not claim the
old nonzero-weight Plasticity norm rule applied. The actual observed signed
monetary targets also pass through the existing native NDU utility operation;
NDU is a receipt/utility primitive, not the backpropagation gradient.

The first requested -$11.50 example (-$7.50 gross, $4 commission) produced a real
pocket4 gfx1150 update. Its training-set value moved from $0 to about -$9.37 with
all 769 readout coefficients changing; the full pretrained Laya hashes were
unchanged. That is one distinct observed reward replayed for 16 optimizer steps,
NOT 16 independent examples. Loss reduction on one trained example does not
establish out-of-sample skill, profitability or permission to trade.

## Resident behavior and recovery

`financial_value_service` periodically revalidates exact rewards and creates at
most one fit per changed reward-set fingerprint. The minimum interval is 15
minutes. Repeated identical status reads and restarts do not retrain the same set.
Corrections have new identities and preserve previous immutable candidates.
Each job fits the complete active subset for its behavior scope from a neutral
readout; it does not compound unaccepted heads into a selected trading model.

Behavior model and producer-policy identity define separate value-head scopes.
Old direction-only and new FX hysteresis outcomes are not silently pooled as if
produced by the same policy. Each candidate exposes its behavior scope and exact
reward IDs. At most 64 eligible rewards are accepted in this initial profile;
512 MiB of output is a finite storage budget, with no automatic evidence pruning.

The original price/scorer trainer, action-head pipeline and their future slots
continue independently. The new service owns only financial-value files and a
small recoverable jobs database. Interrupted jobs without a committed candidate
are marked interrupted, never silently repeated over a partial artifact. A
candidate is checked against its admitted dataset/profile and tensor hashes.

## Runtime and authority

The worker enters Landlock/seccomp before importing torch. Declared base,
original attribution prefixes, manifests, OMS ledger and code are read-only;
only its task directory is writable. New network sockets, trading bearer reads
and out-of-output writes are denied and probed. Real training requires ROCm
`gfx1150`; the CPU numerical tests are not a training fallback.

`heptatrader-laya-financial-value.service` is user-level, low priority, bounded
CPU/memory/tasks. Installation touches no trading, existing scorer, feed or
future-evaluator unit. It has no order method, model-selection API, actor update,
canonical owner acknowledgement or automatic promotion. `latest-candidate.json`
is a diagnostic pointer, not a production selected-artifact pointer.

The first finite training proves the true net reward reached parameters. Future
financial predictions, independent outcome scoring, calibrated uncertainty and
canonical publication/selection remain separate qualification. Never present
training-set USD predictions as predictions made before the original trade.
