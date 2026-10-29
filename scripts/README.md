# Runtime, research, validation, and qualification scripts

Status: CURRENT

## Development and documentation

- `dev_core.sh` configures, builds, and runs the canonical core CTest label.
- `check_documentation.py` checks local documentation links; header/catalog synchronization is not a development gate.
- `source_json.py` is the shared bounded JSON reader for documentation, component/build ownership, gap and owner-Ruleset planning tools. It is development-only, not an installed privileged reader.
- `check_component_coverage.py` derives the production file set from the exact Git index and assigns each path to the most-specific module implementation boundary. It does not consume a duplicate build graph.
- `verify_build_ownership.py` observes the selected live CMake File API model, verifies canonical module ownership and proves every tracked owned C/C++ implementation source is built or explicitly declared `unbuilt`. `--report` can emit a diagnostic graph without making it repository source truth.
- `check_gap_register.py` validates an extensible issue inventory; open work is allowed. `--release-profile core` or `ib-paper` additionally rejects explicitly scoped unresolved release blockers. It never grants Broker authority.
- `resolve_hepta_config.py` supports the legacy Windows / `HeptaTrade` XML configuration paths. Current Pocket4 runtime identification uses the installed unit and environment, release manifest, and observed process identity; see [Deployment](../docs/modules/deployment.md).
- `verify_oms_journal_replay.py` verifies the journal replay contract used by recovery checks.

## Release and preflight

- `build_release_package.py` installs or consumes a canonical staging tree and emits a deterministic archive, SHA-256 sidecar and private package receipt. It rejects links, special files, secret-like paths, size overflow and output replacement.
- `hepta_preflight.py` verifies the package without extraction and can inspect the installed static host boundary. It never grants PAPER/LIVE authority and never changes the host.
- `run_release_simulator_smoke.py` takes a preflight-admitted core archive through a new candidate slot, executes the installed Agent/Gateway/Execution/simulator E2E binary once, verifies atomic rollback and re-promotion pointer transitions, and writes one no-replace non-authorizing deployment record. The previous slot is seeded from the same verified artifact; N-1 compatibility requires a separately verified prior artifact.

The package digest is the identity carried into later qualification and deployment. Rebuilding from the same source is a new candidate unless the complete package bytes and digest are identical.

## Agent and host runtime

- `hepta_agent_mcp_launcher.py` launches the MCP bridge under the expected identity.
- `hepta_agent_trust_domain.py` validates the Agent trust-domain configuration.
- `hepta_broker_egress_policy.py` applies the fixed UID/port Broker boundary.
- `heptatrader training-pause` uses `hepta_laya_pause.py` to disable both trainer boot activation and the readmission timer persistently, then request a natural supervisor drain. For an active trainer it first writes the fixed user runtime drop-in `systemd/user/heptatrader-laya-trainer.service.d/90-heptatrader-training-pause.conf` containing `[Service]` and `Restart=no`, reloads units and verifies the effective policy. This prevents a failed supervisor from restarting during drain. It never stops the trainer service or signals its worker; `trainer_paused=false` means completion is still pending. An idle pause needs no drop-in.
- A paused trainer's existing wrapper supports `python -B -m historical_training.resident_resource_guard --config WRAPPER --role trainer --refresh-status`. It acquires the original scheduler lock and refreshes queue/terminal status through the original writer, without job recovery, registration, data collection, a GPU lease or hold removal. Refreshed status is a projection, not permission to restart. Use the selected deployment's Python/PYTHONPATH and hash-pinned wrapper, never an unrelated checkout with old pins.
- After the exact operator hold's conditions are satisfied and its owner has reconciled it, the same wrapper's `--once` runs one normal guarded trainer step. It preserves source/resource admission, waits for the bounded child and records completion before exit; no second training step is dispatched. A denied admission can finish with no new job, so inspect the returned state and job count rather than treating exit zero as training success. The service and readmission timer remain independently controlled.

The restart override lasts for this boot. Before an explicitly authorized same-boot
training restart, remove only that owned drop-in from
`${XDG_RUNTIME_DIR:-/run/user/$(id -u)}` after checking its exact contents, then run
`systemctl --user daemon-reload`. Preserve other drop-ins. A reboot clears the
runtime override while the disabled service and timer remain disabled.
- `hepta_oms_report.py` validates one service's observations and optionally publishes atomic metrics; `hepta_telemetry_collect.py` collects bounded service-manager messages. They have no trading authority. See [collection and deployment limits](../docs/technical/telemetry-collection.md).

The old `validate_sim_data.py` depended on a fixed personal Windows XML path
and inspected only a sample row of retired simulator CSV data. It is removed
from source and the install helper list, not relabelled as a supported validator.
Historical source remains at `6cdae64e04a92d234852aa14670a54538e9e5f9c`.

## SHADOW research

`hepta_market_*`, `hepta_official_source_capture.py`, `hepta_strategy_*`, `hepta_eurusd_confirmed_momentum_strategy.py`, and `validate_hepta_strategy_decision_receipt.py` implement read-only evidence and replay components. The repository does not currently contain a canonical bounded observer/controller and does not install these research scripts automatically.

## Trusted qualification

`check_qualification_trust_boundary.py`, `build_ib_candidate_artifact.sh`, `verify_ib_candidate_artifact.py`, `run_ib_paper_artifact_qualification.sh`, `verify_ib_paper_qualification.py`, and `verify_canonical_ib_paper_profile.py` are trusted-main qualification components. They do not make external runners, credentials, Broker sessions or receipts exist; the verifiers fail when those controls are absent.

Do not add developer-specific paths, untrusted `eval`/`source`, Broker secrets, encoded transfer payloads, an alternate order path, or a branch-mutating remediation carrier to this directory.

The IB candidate builder still requires an explicit operator-selected
`HEPTA_IB_BUILDER_IMAGE=repository@sha256:digest`, SDK root, and dedicated
size-bounded quota mount. There is no built-in image default or image allowlist.
The selected image, external SDK, and resulting artifact do not grant Broker
authority. The retired workflow's repository variable remains an owner input,
not an automatically discovered local default.

For local use, `HEPTA_IB_BUILD_ENGINE` accepts `docker` (default) or rootless
`podman`. `HEPTA_IB_BUILD_PULL_POLICY=never` requires that exact digest already
exist locally and performs no registry pull; the default `always` preserves the
original explicit pull. Docker Hub's omitted registry/namespace form is compared
to the actual inspected RepoDigests, whose original bytes are retained. Every
container run uses the inspected immutable image ID with `--pull=never`.
Podman uses `keep-id`; the existing toolchain probe checks the actual UID/GID
and writable `/build/home`. Missing required tools fail before configure.
The image-selected `CXX`/`CC` executables (fallback `c++`/`cc` when unset) are
checked and recorded by path/hash/version, then fixed in configure arguments
and environment. Compiler values containing whitespace/flags are rejected
without evaluation; use separate compiler flag settings instead.
`HEPTA_IB_BUILD_PODMAN_USER_MAPPING=owner-root` is an explicit rootless-Podman
compatibility option for owner-only FUSE mounts. It maps container UID/GID 0 to
the non-root calling host user via `keep-id:uid=0,gid=0`; it does not map host
UID 0. Docker and host-root callers are refused in this mode. The default is
`keep-id`. Both host and container IDs and the actual namespace option are
recorded, while network/capability/NNP/resource restrictions stay unchanged.
No global FUSE configuration or `allow_other` is required by this option.

`HEPTA_IB_BUILD_JOBS` defaults to 2 and controls both CMake parallel settings.
`HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS` defaults to 2700 for each engine metadata, pull, toolchain,
configure, and compile invocation; this is not a whole-build deadline. Create, inspect and attach share one monotonic phase deadline; cleanup has
separate short command limits. The synthetic loopback behavior probe retains
one shared 60-second bound. Containers are created stopped with private CID
files and random names/labels, verified against the image and creation intent,
then started. A zero client status is insufficient: fresh same-proof container
state must be exited with integer exit code 0, no OOM kill, and no runtime error.
Timeout/exit cleans only proven owned containers. Workspace removal
requires confirmed absence; uncertain ownership/cleanup keeps it and fails.
Missing CID publication is recovered only by exact-name identity proof or a
successful bounded name/operation listing proving absence.
Existing memory, CPU, pids, nproc, tmpfs, and writable-filesystem settings retain
their original names and defaults; engine selection does not bypass them.

The TAR remains exactly `manifest.json` plus `hepta-ib-executiond`. The external
`.diagnostics` directory retains the hash-bound builder/resource/toolchain,
actual image identity, and SDK/BID inventory metadata. Completed builds fail
if this provenance cannot be retained; a failed build keeps its original exit
status. These metadata files contain no source/SDK bytes or credentials.
Optionally supply `HEPTA_IB_BUILD_DEPENDENCY_RECEIPT` for the existing owner
receipt containing `sdk_version`, upstream `sdk_sha256`, and `bid_sha256`.
The actual BID digest must match. The release version and upstream archive hash
remain explicitly owner-declared; without a separate archive-to-source check,
this does not attest that mapping or invent a BID release version. Missing
version evidence is reported as unknown. SDK snapshot integrity and the native
Decimal ABI test remain required regardless of that optional metadata.

## Incremental native development

Use the existing CMake targets and CTest names; no second registration system is
needed. For example, after a risk-only change:

```sh
cmake --build build/core --target hepta_pre_trade_risk_engine_tests --parallel 2
ctest --test-dir build/core -R '^hepta_pre_trade_risk_engine_tests$' --output-on-failure
```

The canonical `dev_core.sh` remains final candidate acceptance. Historical
HeptaDLL JSON validation uses `run_python_tests.py --lane legacy` when its
archived inputs or validators change; native old-record/client/privilege tests
remain in core. The lease-store and
Supervisor test fixtures accept standard absolute `TMPDIR` for owned temporary
state. Their defaults remain `/tmp`; record filesystem context for measurements.
The existing coordinator `--generation-growth N` diagnostic likewise accepts
`TMPDIR`. A tmpfs run measures software cost, not physical storage durability.

## Runtime identity and driver inspection

From the source checkout, use the read-only views:

```sh
python3 -B scripts/hepta_runtime_identity.py
python3 -B scripts/install_decision_attribution.py --instrument EUR.USD --inspect-only
python3 -B scripts/install_decision_attribution.py --instrument BTC.USD --inspect-only
```

The runtime view emits JSON for the current units, distinguishing configured
executable/configuration hashes from the actual process identities and loaded
native executable hashes. Driver inspection independently verifies the configured
release and the release named by the real process argv, including their source
revision and manifest/runtime hashes. It reports disagreement or an explicit
inspection error; it does not substitute configured provenance for running proof.
`configured_matches_running` is null if either identity cannot be verified.

`hepta_verified_loader.py` is the shared pinned Python loader for BTC, cost and
attribution adapters and installer snapshots. It executes the same source buffer
whose SHA-256 was verified. New attributed runtime manifests and BTC installation
receipts include the copied loader; the CostResearch installation places it beside
the cost entry point. Immutable legacy attributed layouts remain inspectable and
usable by the existing candidate/rollback validation. The attributed and BTC
launchers forward `--recover-owner` to the base driver's existing recovery checks.

## CI result isolation and scope

The Python job gives documentation, core behavior and source contracts their own
steps in one environment. A failed documentation or core step does not suppress
the remaining diagnostics; failures still fail the job. Learning runs in a later
step of the same job after its pinned CPU dependencies are installed.
Within a Python lane, an import failure runs as a failing unittest
alongside valid modules instead of preventing every test in that lane from running.

`ci_scope.py` keeps a bounded list of verified Python operator groups and requires
their support and test files to exist. Activation, runtime-identity, verified-loader,
and attribution-installer edits can run the Python lane without native builds or
model installation. The latter two also check their known imports; adding an
unknown import restores full validation. A support file does not inherit a helper's
scope exemption when the support file itself changes. Source monitoring, PnL
watching, and their shared `hepta_change_watch.py` helper retain Python and learning
feedback. Other shared or unknown source changes and missing, deleted, or retyped
required files restore full scope.

The Desktop IB workflows are retained as non-dispatched templates in
`.github/retired-workflows/`; their executable contract tests remain. Their scope
is described in [Deployment](../docs/modules/deployment.md). Acceptance of the
current trading and training runtime requires Pocket4 evidence.

The whitespace check inspects the event's committed base/head range, with a
bounded parent/root fallback for events lacking a base; an unresolved supplied
base is an error.

## Training and Value progress

Use `heptatrader progress` (`--format json` for structured output), or
`python3 -B scripts/hepta_progress.py` from the checkout. The read-only observer
separates training PRICE/ACTION progress, future evaluation, Value inventory and
current trading blockers; historical errors remain historical. Use
`--value-timing-report FILE` to include an independently reconciled, signed OPEN
cohort report. `heptatrader status` includes the same progress view.

Verified consumer, resident training-episode, Value prediction/reconciliation
and compiler-cache changes select Python core/source and learning feedback.
Resident v5-v8 objectives, closed-ACTION training, PRICE calibration and compact
input transforms use this scope with their numerical and integration fixtures;
they do not need an unrelated native rebuild. Native interfaces, shared integrity,
installation and build changes still restore all lanes. Missing or symlinked
paired source/test files likewise restore all lanes.
The progress observer is stdlib-only and selects core/source feedback. Missing
paired tests, new external imports in the observer, shared CLI/install changes
and unknown paths restore full validation.
