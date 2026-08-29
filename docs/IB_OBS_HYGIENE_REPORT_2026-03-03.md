# IB Observability & Workspace Hygiene Report (2026-03-03)

## Scope executed
1. Added low-risk sampling/rate-limit guard for IB advanced observability timing logs.
2. Removed accidental temporary patch artifacts (`tmp_patch_*.py`, `tmp_p0_patch.py`) from workspace root.
3. Collected current build state and documented linker-warning hygiene plan (LNK4098/LNK4099) without touching third-party binaries.

## Code change
### File changed
- `HeptaTrade/adapter_ib/ib_gateway_adapter.cpp`

### What changed
- Added env-driven guard in the latency logging path (`EmitLatency`) so high-frequency timing logs can be downsampled/throttled:
  - `HEPTA_IB_ADV_OBS_LAT_SAMPLE_EVERY` (default `1`, emit every Nth latency log)
  - `HEPTA_IB_ADV_OBS_LAT_MIN_INTERVAL_MS` (default `0`, optional per path/stage minimum interval)
- Defaults preserve current behavior (no sampling/throttle unless env vars are set), minimizing regression risk.

## Temp artifacts removed
Deleted from repo root:
- `tmp_p0_patch.py`
- `tmp_patch_ib_cpp.py`
- `tmp_patch_ib_header.py`
- `tmp_patch_ib_include.py`

## Build verification (Release|x64, IB-enabled)
Command used:
- `powershell -ExecutionPolicy Bypass -File scripts/build_release_ib.ps1`

Result:
- Compile passed for modified code path.
- Final link failed with:
  - `LNK1104: cannot open file D:\quant\HeptaTrader-master\x64\Release\HeptaTrader.exe`

Interpretation:
- This is consistent with output binary being locked/in-use by another process (not a compile correctness failure in the new code).

## Remaining non-critical linker-warning hygiene plan (LNK4098/LNK4099)
Current build run did not reach a successful final link because of `LNK1104`, so no new LNK4098/LNK4099 emissions were captured in this run. If they appear in subsequent links, use the plan below.

### LNK4098 (defaultlib conflicts)
Practical remediation (no third-party binary changes):
1. Keep CRT linkage consistent across solution projects (`/MD` Release, `/MDd` Debug).
2. Avoid global `/NODEFAULTLIB` unless narrowly scoped and justified.
3. Align static libs imported into `HeptaTrader` with the same runtime model where possible.
4. If mismatch is isolated to an external prebuilt lib, document and localize ignore settings at the smallest project scope only.

### LNK4099 (PDB not found)
Practical remediation (no third-party binary changes):
1. Treat as informational for release packaging if symbols are not required.
2. Keep warning visible in CI logs, but do not fail build on external-lib symbol absence.
3. If needed for debugging, obtain matching PDBs from vendor package/source build; do not patch vendor binaries.

## Next safe step to complete build
- Ensure `HeptaTrader.exe` is not running/locked, then rerun the same Release|x64 IB build command.
