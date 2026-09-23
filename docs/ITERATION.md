# Iteration contract

Status: CURRENT
Applies to: canonical source development

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

Main and tagged release share `scripts/accept_core_release.py` for package and
installed acceptance. Its privileged process/systemd fixtures require a disposable
CI VM, never a trading host. See [the acceptance contract](technical/core-release-acceptance.md).

This is owner-operated development, not an artificial multi-team governance
workflow. Server-side Ruleset changes remain separate explicit administrative
acts; [the transition procedure](technical/owner-ruleset-transition.md) does not
claim they have occurred. Ordinary CI never receives Broker credentials. Optional
IB PAPER activation still requires exact immutable-artifact qualification and
actual host/account evidence. Later main movement cannot mutate an already bound
candidate. PAPER and LIVE remain unauthorized by source checks.

Pure prose/image PRs run source validation without a native rebuild; the exact
main commit still runs full core/release and independent sanitizer acceptance.
SDK publication consumes the already-tested canonical build, while standalone
platform SDK jobs remain independent. Five-run recovery stress stays on main
and merge candidates rather than every PR iteration.
