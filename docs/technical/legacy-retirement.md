# Retired assets and retained compatibility boundaries

Status: CURRENT
Applies to: source cleanup; no trading authorization or persistent-schema change

## Removed from the active tree

The following eight IDE assets were not inputs to the canonical CMake core/IB
build or install inventory and have been retired together:

```text
HeptaTrader.sln
HeptaTrader_Linux.sln
HeptaTrade/HeptaTrader.vcxproj
HeptaTrade/HeptaTrader_Linux.vcxproj
HeptaStrategy/HeptaStrategy.vcxproj
HeptaStrategy/HeptaStrategy.vcxproj.filters
HeptaSimulator/HeptaSimulator.vcxproj
HeptaSimulator/HeptaSimulator.vcxproj.filters
```

Their provenance remains in commit
`d003f54c7c6c2bd19002627f2bcd9081228b01cd`. To inspect rather than reintroduce an
old build authority, use for example:

```bash
git show d003f54c7c6c2bd19002627f2bcd9081228b01cd:HeptaTrader.sln
```

No duplicate archive of these files is shipped in release packages. The default
off CMake legacy profiles are unchanged, and deleting IDE files does not imply
that all optional legacy sources are supported or modernized.

## Retained intentionally

`Interface/` and `Tools/` contain compatibility/shared headers used by remaining
source and must not be deleted by directory label alone. The optional legacy
`HeptaStrategy/` and `HeptaSimulator/` CMake sources, old XML/data and bridge
sources remain until their consumers have been migrated or retired. The
historical PDF remains provenance material, not current runtime design.

`HeptaTrade/reconcile/` is the old monolith's CSV reconciliation reporter. Its
catalog ownership is now explicitly LEGACY; canonical reconciliation is
documented in [`reconciliation-engine.md`](reconciliation-engine.md). This
corrects an ownership claim rather than replacing the reporter with a new
execution path.

## Retired migration and validation machinery

The twelve assignment-only risk sink members and `PreTradeRiskLegacyWriteOnly`
were removed after finding no production writers. Readable authoritative risk
snapshot/evidence structures remain unchanged. Compiler tests now require the
retired API to be unavailable and the supported API to compile; see
[`risk-legacy-compatibility.md`](risk-legacy-compatibility.md).

The redundant documentation wrapper/core split, source-token gap-closure
verifier and its token-presence meta-tests were retired. Their absence does not
retire journal, venue, risk, installation or sanitizer behavioral tests. The
gap register is an issue inventory with profile-scoped release blockers, not a
fixed list that must always say READY.

## Acceptance and limits

Validate with the canonical CMake build/CTest, full Python discovery, fresh
install inventory, generated documentation index and Git/CMake ownership
checks. A new proposed deletion must be checked against includes, CMake sources,
installation rules and test fixtures, not only directory ownership. This
cleanup does not change on-disk journal/lease formats, Broker profiles,
credentials, network permissions or authorization defaults.
