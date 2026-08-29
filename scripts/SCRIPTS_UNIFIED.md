# Hepta Script Consolidation

Use the native Python release gate on Linux/CI and the PowerShell wrapper on
Windows:

1. `python3 scripts/hepta_ops.py release check --phase dev|rc|paper`
2. `D:\quant\HeptaTrader-master\scripts\hepta.ps1`
3. `D:\quant\HeptaTrader-master\hepta.bat`

The Python command is the canonical phase implementation; PowerShell forwards
to the existing Windows checks for hosts that provide those tools.

Legacy operational entry points:

`release_check.ps1` remains available for Windows compatibility.

## Examples

- Set strategy profile:
  - `hepta.bat -Action profile -Profile balanced`

- Build Release (IB enabled):
  - `hepta.bat -Action build`

- Launch trader:
  - `hepta.bat -Action launch`

- Monitor latest OMS:
  - `hepta.bat -Action monitor -Tail 300`

- Canonical phase-based release check (static checks run once and are summarized):
  - `python3 scripts/hepta_ops.py release check --phase dev`
  - `python3 scripts/hepta_ops.py release check --phase rc --build-dir <build-dir>`
  - `python3 scripts/hepta_ops.py release check --phase paper --build-dir <build-dir> --soak-profile release --rc-report <prior-rc-summary> --rootful-report <receipt> --p1-report <receipt> --authority-report <receipt>`
  - `hepta.bat -Action release -Phase dev`
  - `hepta.bat -Action release -Phase rc`
  - `hepta.bat -Action release -Phase paper -SoakBuildDir <build-dir> -SoakProfile release`

Soak profiles are `pr-smoke` (two rounds) and `release`/`nightly` (eight rounds
by default). Rootful, P1, and PAPER authority gates remain separate mandatory
promotion evidence; this shortcut does not grant or bypass those authorities.
The optional `--rc-report` hand-off is accepted only for `paper` and is
strictly bound to the current config and soak receipt when provided.

## Legacy scripts (kept for compatibility)
- set_strategy_profile.ps1
- build_release_ib.ps1
- monitor_ib_session.ps1

Avoid calling legacy scripts directly unless debugging.
