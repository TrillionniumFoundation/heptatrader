# Current100k shared features for an independent family (2026-11-03)

The optional shared-feature collector lets a newly registered Current100k head
family consume successful features from an existing research collector. The new
family runs its own CPU heads and publishes through its own `RequestLedger` and
`ShadowStore`. It does not load a second encoder while the original collector is
responsible for the shared input interval.

This is a research execution path. It does not grant selection, actor-update or
trade authority, repair an earlier experiment, or establish predictive quality.
The Pocket4 verification recorded below covers durable identities, timing and
real 11-head FP32 parity. Long-term coverage and the live source-end guard
handoff remain separate observations.

## Independent registration and preserved original experiment

Use a new immutable release, a new study directory and a newly registered family.
The source study keeps its existing plan, registration, activation, predictor
configuration, candidate heads, request receipts, service and fixed input/outcome
windows. The original continuation allowlist, resource guard and trade owner are
unchanged.

The new plan must have a different identity, a later registration and input start,
and a later fixed input end than the source plan. Existing plan validation still
requires the original 14-day input window and settlement grace. The new predictor
configuration must be bound by the new activation before that new input window
starts. This is not a way to attach new heads to an already frozen family.

An original missing, failed, late or interrupted pre-command prediction remains
missing, failed, late or interrupted. In particular, new publications cannot
repair the old window's immutable missing predictions. Previously viewed labels
remain development data; the new family does not inherit an earlier seal time.

## Optional predictor configuration

Add the following object to the predictor template before the new supervisor
creates its immutable predictor configuration and activation:

```json
{
  "shared_feature_source": {
    "schema": "heptatrader.current100k-shared-feature-source.v1",
    "plan": {
      "path": "/absolute/original-study/plan.json",
      "sha256": "ORIGINAL_PLAN_FILE_SHA256"
    },
    "registration": {
      "path": "/absolute/original-study/registration.json",
      "sha256": "ORIGINAL_REGISTRATION_FILE_SHA256"
    },
    "activation": {
      "path": "/absolute/original-study/activation.json",
      "sha256": "ORIGINAL_ACTIVATION_FILE_SHA256"
    },
    "predictor_config": {
      "path": "/absolute/original-study/predictor-config.json",
      "sha256": "ORIGINAL_PREDICTOR_CONFIG_FILE_SHA256"
    }
  }
}
```

The displayed digest strings are placeholders. All four references require the
SHA-256 of the actual file bytes and absolute paths to the named original files.
`registration` here means the source **study registration receipt**. It is
distinct from `producer_registration`, which identifies the native input and
command producer.

The source activation must bind the supplied original predictor configuration.
The source request-ledger directory is obtained from that pinned configuration.
This remains compatible with an authorized original-runtime continuation that
preserves the original configuration and ledger. A source configuration that is
itself a shared-feature consumer is rejected; this implementation uses one
original encoder source, without a chain of feature consumers.

The source and destination storage roots must not overlap. The collector checks
that both families have the same producer registration, period, behavior model,
base identity, feature profile, numeric profile, encoder backend, thread count,
batch size and HIP runtime manifest. Both families must use `TORCH_CPU_FP32`
heads. The complete `encoder_runtime_files_sha256` inventories must also match,
including the original feature-text helper. The later guarded encoder therefore
retains the declared encoder implementation and input contract.

## Entrypoint and integrity bindings

The executable entrypoint remains
[`scripts/run_current100k_preopen_research.py`](../../scripts/run_current100k_preopen_research.py).
There is no new standalone collector command. The existing supervisor launches
the predictor through its verified-release launcher and passes the immutable
predictor configuration path and SHA-256.

The internal optional API is:

```python
from laya_cell.current100k_shared_features import run_shared_features

shared = run_shared_features(config)
if shared["handoff_to_guarded_encoder"]:
    # The helper has closed the new ledger. The runner revalidates the
    # configuration before continuing through the original guarded path.
    ...
```

The hook runs after configuration validation, environment limits and CPU affinity
have been applied, and before encoder construction or guard acquisition. The
existing HIP environment/re-exec boundary in the CLI remains in place. Enabling
the option makes `current100k_shared_features.py` a required entry in the runner's
`runtime_files_sha256` inventory; the runner verifies every listed file hash.

The existing supervisor pins the release manifest and predictor template. Its
activation stores the new `plan_id` and predictor-configuration SHA-256. The
predictor CLI verifies that configuration file, and the helper verifies that the
loaded configuration exactly matches the activated file. The four source pins
are consequently part of the activated configuration. The helper rechecks those
source pins and the new study's plan, registration, activation and predictor
configuration before consuming features and before source-window handoff.

Release preparation must include the helper and its dependencies in the reviewed
release inventory. These checks do not authorize rewriting a running release.
The immutable release and overlay used for the recorded Pocket4 qualification
are identified in the verification section below.

### Handoff dependency revalidation

The shared observer can remain alive for much longer than an encoder residency.
The runner calls `validate_config(c)` before entering that observer and again
immediately after a successful shared handoff, before `hip_binding` or
`original_guard`. The second check verifies the actual runtime inventory, period,
guard, warmup and encoder source pins after the potentially long wait. It
complements the helper's four-source-file and target-activation checks.

Two integration tests execute the actual runner `run()` boundary. They verify
that a changed dependency is rejected during the second validation before guard
or encoder admission, and that a stopped shared phase returns without entering
the guarded path. Both passed on Pocket4.

## Per-request evidence path

The collector reads the current verified native ingress. It records eligible
requests in the **new** ledger, remembers original commands in the new store,
and reconciles any commands already present. The default `ResidentIngress`
adapter explicitly calls `read(retry_deadline_ms=deadline)`, preserving the
request's nonrenewable deadline during bounded source-snapshot retries.

For an unexpired request without an original command, the source must provide all
of the following evidence:

1. An original `SEALED_AWAITING_ORIGINAL_COMMAND` terminal for the exact request,
   producer and source plan.
2. The matching original request input, attempt and protocol. The source attempt
   must satisfy its original wall-clock and monotonic deadline checks.
3. A complete `ShadowStore` publication and observation, identical to the source
   terminal's retained publication receipt, with valid commit and file hashes.
4. The matching immutable `features.json`, including request, base, backend,
   feature-text and numeric identities, finite normalized feature vectors and
   the required HIP publication proof when applicable.

A complete prediction file left behind by a failed or late source attempt is
insufficient. Missing or unsuccessful source evidence produces a wait or
incident; it does not trigger early encoder fallback. The new request eventually
receives the existing command/deadline outcome if usable source evidence never
arrives.

Before recording a new attempt, before the head forward and before sealing, the
collector obtains another current ingress snapshot and checks the exact request,
original-command absence and fixed deadlines. It performs at most one head
forward per polling iteration. After the attempt, another capture reconciles
commands that arrived while publication was in progress.

The new feature document retains the original feature vectors and encoding
provenance and adds `shared_feature_source` consumption metadata. This contains
the source plan/configuration pins and the original input, attempt, terminal,
publication, observation and feature hashes. The new feature digest and commit
bind that metadata. The original source files are never rewritten or locked by
this collector.

## Clocks and the publication race

| Record or field | Meaning |
| --- | --- |
| Source feature `computed_at_ms` | The actual original encoder computation time, retained with the shared feature. |
| Consumption `consumed_at_ms` | The current new collector's observation when preparing this new prediction. |
| New prediction `computed_at_ms` | The current new `ShadowStore.seal` time after the new heads have run. |
| New publication and observation | Fresh post-fsync timestamps and hashes generated by the new store. |
| New attempt and terminal | The new ledger's own current wall-clock and monotonic deadline evidence. |

Old publication timestamps remain source provenance. They are never copied into
the new publication or used to make a new head appear to have predicted earlier.

A native command can arrive between the last ingress check and publication
fsync. This collector does not hold the producer's command lock, so it does not
claim that a late file can never be created. The new publication times, subsequent
`reconcile` and original strict pre-open evaluator deny credit when publication
was not durably observed before the original command. Deadline failure or an
interrupted attempt also cannot be repaired by replaying that request.

Independent evaluation must continue to require the original successful terminal
and exact command binding. Durable files alone do not establish pre-open credit.

## Resource ownership and fixed-window handoff

| Phase | Work | Ownership and end condition |
| --- | --- | --- |
| Source input window remains open | Warm the new CPU heads, observe native ingress and consume valid original features. | Own only the new request-ledger lock. No encoder lease is acquired. |
| Source fixed input end has arrived | Close the shared phase and return the handoff decision. | Drop the CPU heads and close the new ledger before returning. |
| Remaining new-family input window | Continue the existing original encoder runner on the same new protocol and ledger. | The existing guard controls admission, cooldown, fair yielding and cleanup. |
| New input window ended, or stop requested | Exit the shared observer. | Return `handoff_to_guarded_encoder=false`. |

The shared observer has no default 1,800-second residency cutoff. It runs until
the fixed source/new window or a stop signal, with sleeping, bounded polling. The
`max_seconds` argument is a test bound. This avoids deliberately creating a
relaunch gap every encoder-residency interval.

CPU heads are loaded before the first request is processed, normally during the
new registration's five-minute waiting period. `shared-heads-ready-*.json`
records completion. This loads only the small head tensors; it does not compute
features, run a head prediction or acquire a guard lease.

The shared phase sets CPU intra-op threads and does not call
`torch.set_num_interop_threads`, the encoder's CPU configuration helper, or any
`torch.cuda` API. The original encoder keeps responsibility for its existing
interop initialization. The runner hook adds no duplicate interop setter. Actual
11-head CPU FP32 prediction has passed in the selected Pocket4 Torch environment.
The long-running collector has not yet reached its source-end transition into a
live guarded encoder. The source inspection and hook-boundary tests are not a
claim that this later operational transition has already occurred.

`protocol_for(config, plan)` reproduces the original runner's protocol fields,
including limits, CPU affinity, model profile and the new study-plan hash. The
guarded phase can therefore reopen the same ledger without changing plan or
request identity. An attempt interrupted in either phase remains
`INTERRUPTED_UNKNOWN_NO_REPLAY` under the existing recovery policy.

Source-window handoff does not renew a request deadline or evict the old worker.
If the old worker still holds its lease while cleaning up, normal guard admission
must wait. The collector never changes the existing continuation allowlist or
guard to accelerate that transition. A stop signal takes precedence over a
handoff decision.

## New-study receipts

The new study retains the ordinary request and prediction files plus:

- `shared-feature-source.json`: the static source binding and handoff policy in
  the new request-ledger directory.
- `shared-heads-ready-*.json`: CPU-head load completion with new plan identity,
  head count and zero encoder/guard acquisitions.
- `shared_feature_source` inside each new feature document: the consumed source
  proof chain, bound by the new feature digest and commit.
- `shared-session-*.json`: current phase end, counters, reason, closed-ledger
  state and handoff decision.

These are local research receipts. A head-ready receipt proves neither future
prediction coverage nor head usefulness. Source feature costs must remain shared
encoding costs; the new collector must not be described as having recomputed that
encoder work.

## Verification as of this note

Run the focused tests from the repository root:

```sh
PYTHONPATH=scripts:research:research/neuron_learning \
python -B -m unittest discover \
  -s tests/python -p test_current100k_shared_features.py -v
```

The Pocket4 result is **20 passed out of 20, with no skips**. The tests use the
real request ledger, shadow store, request/feature helpers and strict evidence
checker. They cover fresh clocks and new plan identity; unchanged source bytes;
commands before inference, during the head forward and during seal; source
failure, pin drift and tampered features; exact base/producer/numeric/runtime
matching; retained HIP feature proofs; no replay after restart or interruption;
fixed-end lock handoff; stop precedence; a shared observer lasting beyond 1,800
seconds; the default ingress's keyword-only deadline API during a busy read;
and the actual runner's dependency-revalidation and stop boundaries.

The test
`test_real_eleven_fp32_heads_match_direct_shared_feature_inference` creates 11
real FP32 heads and compares the collector's publication with direct
`HeadEnsemble.predict` over the same features. It **passed on Pocket4** and is
included in the 20-test count. The earlier development environment had reported
17 passes and one skip because it lacked PyTorch; the Pocket4 result supplies the
real-head evidence and includes the two subsequently added runner tests.

| Qualification | Pocket4 result |
| --- | --- |
| Shared collector and runner-hook suite | 20/20 passed, no skips |
| Real 11-head FP32 direct-versus-collector parity | Passed; included in the 20 tests above |
| Source strict-review/export suite | 16/16 passed |
| Source-to-runtime cross-repository suite | 3/3 passed |

The qualification release contains **447 Python files and 129 external file
bindings**. Its release-manifest SHA-256 is
`1061860d8155e0ea608cb91adc78c73193c3fa40e6758013389e6a76f3d74b69`.
The qualification overlay SHA-256 is
`edc5a4a68f01cc6fd1b2dbbf615ca97559cee08305d9ee5d2226c9f23c54cb30`.

Implementation files at this qualification point:

| File | SHA-256 |
| --- | --- |
| `scripts/run_current100k_preopen_research.py` | `9b7387120d557c8253b6281ac1d4f0af5c68e6145604f647638eae7ce0b93546` |
| `research/neuron_learning/laya_cell/current100k_shared_features.py` | `62011b16460b0adacea8810fe7cbe90cffbb16a719471c93c3eaad0038176a68` |
| `tests/python/test_current100k_shared_features.py` | `ffd158661a5689542d0fc518aabd407f969aace0d7b7c3f5e7e759d8386952f6` |

These results establish engineering behavior and real-head parity. They do not
establish multi-day prospective coverage, improved model quality or a completed
live source-end transition through the original guard.

## Actual new-family deployment

The qualified release was activated as a separate enabled research service. The
unchanged supervisor performed source-recovery and resource checks and registered
the new eleven-head family. A read-only audit at 2026-11-03 15:49:30.179 UTC found
the service active/running and the supervisor collecting the fixed input window.

| Event | UTC |
| --- | --- |
| Registration | 2026-11-03 15:44:25.673 |
| Input start | 2026-11-03 15:49:25.673 |
| Input end | 2026-11-17 15:49:25.673 |
| Outcome end | 2026-11-18 15:49:25.673 |

The new plan ID is
`3a365ad12cab17e3864d26501dcef0e1bb8ffdb72c2b30503cdc402ffdc219cc`;
its file SHA-256 is
`66d6c95296c9bc9adb2b764d6c8366d48d528e586f5d6070c854d5926ed5402c`.
Activation binds predictor configuration
`385bd3dd8dc7a968b29e5057b62aa50600f66df43df23173b5320b6ea9d3ad3a`.
The original plan and its three permanent missing pre-open predictions remain
unchanged. This deployment does not restart or replace that population.

At 15:55:13.845 and 15:56:04.794 UTC, read-only observations confirmed eleven CPU
heads ready, zero encoder loads and zero guard acquisitions. The heads-ready
receipt has SHA-256
`ad4bdde8de33fb5e398a5630196aa313852168bfd237ea32702cc5d5a49d9eb4`.
Both observations found zero request inputs, terminals and publications for the
new family, so no actual cross-plan prediction or pre-open binding was yet
verified. The independently enabled strict-review service was active/running;
its assessment was PENDING, with a complete current replay, zero commands and
OBSERVED outcomes, and UNKNOWN/null comparison eligibility.

The collector and strict reviewer remain bounded by their own frozen deadlines.
These deployment receipts add evidence without rewriting the candidate
preparation's earlier `cross_plan_shared_encoding_verified:false` field. A live
shared prediction, later settlement, long-term coverage and source-end guarded
handoff require separately timed evidence.

## Coverage and interpretation limits

The new family remains subject to its original completeness, coverage, timing,
support, fee-stress and statistical rules. Sharing features does not guarantee
continuous or long-term coverage. Coverage still depends on successful original
feature production, the remaining interval before each native command, valid
source snapshots, process availability, timely storage and later guard admission.

If the source collector misses a request, this phase cannot fabricate an earlier
feature or prediction. If the later guard is unavailable, the new collector cannot
extend the window, renew deadlines or bypass resource ownership. A failed new
window must remain failed under its frozen rules. No output movement, saved
encoder computation or successful software test establishes improved predictive
accuracy, financial return or adoption eligibility.
