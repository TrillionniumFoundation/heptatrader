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

## Incremental native development

Use the existing CMake targets and CTest names; no second registration system is
needed. For example, after a risk-only change:

```sh
cmake --build build/core --target hepta_pre_trade_risk_engine_tests --parallel 2
ctest --test-dir build/core -R '^hepta_pre_trade_risk_engine_tests$' --output-on-failure
```

The canonical `dev_core.sh` remains final candidate acceptance. Historical
HeptaDLL JSON validation runs once in `run_python_tests.py --lane source`, while
native old-record/client/privilege tests remain in core. The lease-store and
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
the remaining diagnostics; failures still fail the job. Learning retains its
independent job. Within a Python lane, an import failure runs as a failing unittest
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
The progress observer is stdlib-only and selects core/source feedback. Missing
paired tests, new external imports in the observer, shared CLI/install changes
and unknown paths restore full validation.
