# GATE STABILITY REPORT (actual repo run)

Repo: `D:\quant\HeptaTrader-master`
Date: 2026-02-28

## Scope
- IB + CTP only
- XT excluded from release policy

## Reviewed scripts
- `scripts/ci_gate.ps1`
- `scripts/ci_gate_release.ps1`

## Stability notes
1. Gate now runs end-to-end with deterministic step output JSON/TXT.
2. `ci_gate_release.ps1` correctly enforces required checks:
   - BUILD
   - IB_HEALTHCHECK
   - IB_REGRESSION_ROUND
   - CTP_REGRESSION_ROUND
   - RECONCILE_CRITICAL_BLOCK
3. Release policy rejects skipped required checks.

## Final run result
- `RELEASE_POLICY=PASS`
- `RELEASE_POLICY_SCOPE=IB+CTP_ONLY (XT excluded)`
- `SUMMARY_JSON=D:\quant\HeptaTrader-master\runtime-logs\ci-gate-20260228-193755\ci_gate_summary.json`

## Residual risk
- Gate correctness still depends on local broker runtime prerequisites (IB/TWS/Gateway availability and CTP env).
- XT remains intentionally out of release scope until SDK approval.
