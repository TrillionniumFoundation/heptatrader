# Read-only exposure preflight (Issue #331)

This is **not a maintenance authorization**, risk-exit watchdog, order monitor,
protective order, or production systemd hook. It cannot close the Issue #331
risk of a driver being intentionally stopped while already exposed.

Run `python3 -B scripts/hepta_maintenance_exposure_preflight.py` as the
Pocket4 user. The code only reads the protected PnL snapshot, validates owner,
mode, symlink refusal, time bound, source scope, both FX/BTC positions and
settlement. It prints narrow, non-account-identifying reasons and returns
nonzero whether the snapshot is exposed (2) or superficially flat (3).
Even if all checks pass, **`advisory_stop_permitted=false` always**: a new
entry may occur immediately after the snapshot and risk protection is not
independent of the driver.

Real Pocket4 result: `EUR.USD:EXPOSED` (PAPER FX short exposure).
At audit time BTC was flat but the PnL snapshot also reported outstanding
settlement evidence; a zero position is not proof of an order-free owner.

Required future closure is an atomic, exact-account owner fence
covering no-new-entry + pending-order terminal identity + broker position
and a separately verified protective mechanism during driver absence.
Only then can an authorized maintenance orchestration use this advisory as
a secondary diagnostic; no bash/systemd service stop is added here.

## Development admission is independent

This is an optional, **read-only diagnostic command**, not a new merge check,
CI job, mandatory operator approval or production stop permit. It never
changes a service, driver, order, model or risk setting. The snapshot JSON
parser refuses duplicate keys, nonfinite constants, symlinked/nonprivate
files and incomplete reads before issuing any finding. Exposed or stale data
produces explicit UNKNOWN/EXPOSED findings, never synthetic FLAT.

Even with clean snapshot data, the report remains an advisory and
`advisory_stop_permitted` is always false. The only real maintenance approval
would be an atomic Broker/Owner stop-entry fence plus independent protection
while a driver is absent.
