# PHASE FREEZE — IB/CTP (XT reserved)

Repo: `D:\quant\HeptaTrader-master`
Status: **Frozen for IB+CTP integration phase**

## Done
- IB hardening in adapter path (preflight/gate diagnostics improvements)
- CTP adapter scaffold added and runtime hook introduced
- Release gate scripts aligned to IB+CTP policy
- Reconcile critical-block check integrated

## Explicitly reserved
- XT path is scaffold/reserved only
- XT is excluded from release policy until SDK is approved and integrated

## Known gaps
- CTP adapter is scaffold-level (not yet full parity migration of all old direct paths)
- Some docs are implementation notes and need consolidation post-merge

## Next actions (after XT SDK approval)
1. Link XT C++ SDK include/lib/runtime
2. Implement `adapter_xt` real connect/order/cancel/status callbacks
3. Normalize XT events into OMS journal schema
4. Add XT healthcheck + regression round
5. Expand release policy to IB+CTP+XT once stable

## Freeze exit criteria
- IB+CTP gate pass on clean tree
- Build Release|x64 pass
- Commit plan executed A→B→C→D

## Build verification
Command:
`D:\VSstudio\MSBuild\Current\Bin\amd64\MSBuild.exe HeptaTrader.sln /t:Build /p:Configuration=Release /p:Platform=x64 /m`

Result: PASS (validated in current phase)
