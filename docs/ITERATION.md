# Iteration contract

Status: CURRENT
Applies to: canonical source development

Local native and source validation requires `cmake`, `ctest`, a C++ compiler and
Python 3 on `PATH`; Ninja is used when present. The canonical entry points reject
missing CMake/CTest before loading tests or starting a build, so an incomplete
workstation does not produce a page of misleading product failures.

`./scripts/dev_core.sh` configures the broker-disabled/default-off legacy profile,
builds canonical native targets and runs the core CTest label. Python test
ownership is defined once by `scripts/run_python_tests.py`; core, source, install
and isolated process partitions are disjoint. Use `--lane core` or `--lane source`
for ordinary unprivileged development. Install/process prerequisites are real
and must not be converted into successful skips. The partition runner rejects
missing install/process prerequisites before importing tests; `--lane all`
requires both. A selected skipped test makes the runner fail rather than claim
complete acceptance. `--list` remains read-only and needs no fixture opt-in.

`python3 scripts/check_documentation.py` validates navigation and capability
facts, not prose depth. Structural checks do not prove implementation correctness;
small executable regressions should falsify the changed behavior.

Tagged core releases use `scripts/accept_core_release.py` for package and
installed acceptance. Ordinary pull-request and main-push CI do not run this
driver. Its privileged process/systemd fixtures require a disposable CI VM,
never a trading host. See [the acceptance contract](technical/core-release-acceptance.md).

This is owner-operated development, not an artificial multi-team governance
workflow. Server-side Ruleset changes remain separate explicit administrative
acts; [the transition procedure](technical/owner-ruleset-transition.md) does not
claim they have occurred. Ordinary CI never receives Broker credentials. Optional
IB PAPER activation still requires exact immutable-artifact qualification and
actual host/account evidence. Later main movement cannot mutate an already bound
candidate. PAPER and LIVE remain unauthorized by source checks.

Pull requests and main pushes use `scripts/ci_scope.py` to select native, Python
core/source and learning checks. Pure prose changes retain documentation and
whitespace diagnostics without a native rebuild. Unknown or shared changes
retain full validation. Sanitizer acceptance, five-run recovery stress and
standalone SDK relocation remain explicitly dispatched workflows.

`heptatrader progress` is the current read-only operational summary;
`heptatrader status` includes the same summary alongside service and PnL status.
Use `heptatrader progress --format json` for structured evidence and
`--value-timing-report FILE` for an explicitly captured signed OPEN cohort.
PRICE updates, ACTION sample support, prospective evaluation, Value inventory
growth and trading permission are separate observations. Completed job counts
are not cumulative model updates; inventory growth is not efficacy. A previous
driver error remains history unless current service, process-bound heartbeat
or pause evidence confirms a present blocker. A clear hold does not grant trading
permission. Runtime output distinguishes the selected configuration from the
configuration of the process actually running.
