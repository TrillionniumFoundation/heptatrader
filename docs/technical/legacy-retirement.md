# Retired execution assets and retained compatibility boundaries

Status: CURRENT
Applies to: source retirement; no trading authorization or persistent-schema change
Implementation: `CMakeLists.txt`, `HeptaTrade/CMakeLists.txt`, `cmake/HeptaLegacy.cmake`
Tests: `tests/python/test_legacy_retirement.py`, canonical build and installed acceptance

## Retirement decision and consumer closure

The maintained product is the canonical Agent/Gateway/Execution composition,
not a second monolithic multi-venue application. The optional historical
monolith and Pegasus simulator are now retired instead of remaining nominally
supported default-off options requiring private binary overlays.

| Retired assets | Previous in-repository consumer | Disposition |
|---|---|---|
| `HeptaStrategy/` | root monolith branch; old simulator; monolith target link | retire the optional library and both consumers together |
| `HeptaSimulator/` | root `HEPTA_BUILD_LEGACY_SIMULATOR` branch | retire old simulator, not `HeptaTrade/simulator/` |
| `HeptaDemoStrategyTrader.cpp`, `ib_fx_multi_strategy.*` | `cmake/HeptaLegacy.cmake` monolith source list | retire orchestration, not canonical coordinator/IB adapter |
| `openclaw_0dte_bridge.*`, `order_watchdog.*`, `reconcile/` | monolith source/include graph | remove dedicated bridge/watchdog/CSV reporter |
| top-level FM XML, Instrument XML, old HeptaTraderConfig and IBRisk template | legacy-only configuration; absent from canonical install | remove with retired execution paths |
| CTP overlay link/import branches, old include/link directories, legacy DLL target branch | optional monolith CMake graph only | remove machinery, not a new overlay download or fallback |

This is an explicit retirement of those optional products, not a claim that
no external user has ever used their headers or files. Exact pre-retirement
source is retained in commit `3ada6d3157d603e63a47f660d41bfb3937bbec9c`:

```sh
git show 3ada6d3157d603e63a47f660d41bfb3937bbec9c:cmake/HeptaLegacy.cmake
```

No redundant archive of the removed source is shipped in release packages.
Git history is not rewritten. Canonical targets, source lists, SDK ABI probe,
security checks, installed paths, Broker profiles and schemas are unchanged.

## Explicit failure instead of compatibility theatre

`HEPTA_BUILD_LEGACY_MONOLITH`, `HEPTA_BUILD_LEGACY_SIMULATOR` and
`HEPTA_ENABLE_LEGACY_0DTE_BRIDGE` set to ON fail both root and standalone
HeptaTrade configuration with `HEPTA_LEGACY_RUNTIME_RETIRED`. Explicit OFF values
remain accepted. The old include path retains only a fatal diagnostic. Nothing
silently compiles a different service or grants it old configuration authority.

The regression test runs actual CMake configurations and reads the resulting
File API codemodel. It does not assert source spelling, arbitrary line counts
or the absence of every historical word. Full canonical compilation and install
acceptance detect remaining transitive build consumers; SDK-free results do not
claim that a new real Broker campaign was performed.

## Earlier completed cleanup

Eight unused Visual Studio solution/project assets were previously retired.
Their earlier provenance is commit `d003f54c7c6c2bd19002627f2bcd9081228b01cd`.
The twelve assignment-only risk sinks and `PreTradeRiskLegacyWriteOnly`, the
redundant documentation wrapper/core split and token-presence gap-closure
machinery were also removed previously. This change does not count those
historical deletions as new work or resurrect their artificial gates.

## Retained intentionally

Shared `Interface/` and `Tools/` source/data remain labelled LEGACY pending
separate retained-consumer/licensing review; they are not canonical deployment
products. The 2023 PDF remains historical provenance. No licence permission is
inferred from a directory name or a green build.

Maintained OMS readers, HSL migration layouts, command deduplication and terminal
recovery remain intact. These preserve deployed state and are not deleted with
an old application. See [persistence support](persistence-support-window.md).
Canonical reconciliation remains [the authoritative engine](reconciliation-engine.md),
not the removed CSV reporter. The restricted CTP/XT stubs and SHADOW pipeline
remain separately experimental; no new live venue capability is claimed.

A deployed legacy installation must retain its source, immutable executable and
protected journal/lease/key state until an independently validated migration.
This source retirement does not authorize a binary rollback, rewrite any host
file, erase history or expand PAPER/LIVE permission.
