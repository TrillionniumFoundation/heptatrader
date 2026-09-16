# Retired assets and retained compatibility boundaries

Status: CURRENT
Applies to: source retirement; no deployed-state or persistent-schema migration
Implementation: `CMakeLists.txt`, `HeptaTrade/CMakeLists.txt`, `cmake/HeptaLegacy.cmake`
Tests: `tests/python/test_legacy_retirement.py`, `tests/python/test_legacy_runtime_boundary.py`, `tests/python/test_cmake_install_integration.py`

## Retired products and consumers

The historical multi-venue monolith, HeptaStrategy composition, Pegasus
simulator, JSONL 0DTE bridge, strategy orchestration, order watchdog and CSV
reconciliation reporter have been removed with their exclusive build graph.
The remaining root `Interface/` and `Tools/` collections are now removed too.

| Retired asset | Known consumer / decision |
|---|---|
| `Interface/include/hepta*.h` | Old monolith, HeptaStrategy, Pegasus and watchdog; these applications were already retired. Canonical execution uses its own contract/state/risk types. |
| `Interface/include/tinyxml.h`, `tinystr.h` | Old XML composition in the retired monolith; not canonical service configuration. |
| `Interface/oneapi/` | Old vendored concurrency headers in the retired overlay; no canonical target includes or links this collection. |
| `Interface/CTPTradeApi32`, `CTPTradeApi64`, `CTPTradeApiLinux` | Identical legacy SDK placeholders, not functioning Broker transports. Current CTP capability stubs remain separate. |
| `Tools/` configuration, instrument XML and dated market samples | Retired simulator/monolith configuration and example data; not installed canonical input or authoritative live history. |
| `scripts/validate_sim_data.py` | Personal Windows-path XML/CSV sample-row checker; wrongly carried in the runtime helper list. Removed with its catalog and install entries, not replaced by a success-shaped validator. |

The complete last pre-removal asset tree is recoverable from source
`6cdae64e04a92d234852aa14670a54538e9e5f9c`. For example:

```sh
git show 6cdae64e04a92d234852aa14670a54538e9e5f9c:Interface/include/heptaBasicStrategy.h
```

Do not copy the retired data/SDK overlay into new packages. Embedded upstream
notices remain with their historical source; retirement does not assert new
redistribution rights, erase existing legal obligations or certify all external
consumers. No data, header archive or dummy replacement folder is added.
`HeptaTrade/tools/` is maintained Gateway code, not the removed root `Tools/`.

## Explicit old-build failure

Root and standalone HeptaTrade entry points reject enabled
`HEPTA_BUILD_LEGACY_MONOLITH`, `HEPTA_BUILD_LEGACY_SIMULATOR` and
`HEPTA_ENABLE_LEGACY_0DTE_BRIDGE` with `HEPTA_LEGACY_RUNTIME_RETIRED`.
Explicit OFF flags remain accepted for existing canonical automation.
`cmake/HeptaLegacy.cmake` remains only as an executable failure diagnostic for
old include callers. No success-shaped stub target or SDK fallback recreates
the retired trading products.

## Previously completed cleanup

Eight unused Visual Studio assets were retired previously; their provenance
remains at `d003f54c7c6c2bd19002627f2bcd9081228b01cd`. The twelve assignment-only
risk sinks and `PreTradeRiskLegacyWriteOnly` were removed with compiler tests
for the supported API. Documentation wrapper duplication, source-token closure
checks and spelling-only meta-tests were also retired. These are not newly
claimed deletions in the remaining-assets change.

## Acceptance and limits

The existing CMake behavior tests reject the old flags/include and inspect
fresh codemodel targets and include paths. The full canonical compile and tests,
GCC/Clang sanitizer lanes, Git/CMake ownership, fresh installation and installed
process/PID1 acceptance must pass on the exact resulting revision. Removing
files first and then weakening a failing build/test is not accepted cleanup.
The separately supplied IB SDK remains pinned outside the repository; core CI
is not a claim that the SDK-enabled Broker campaign ran.

OMS schema readers, encrypted HSL historical layouts, durable command identity,
owner fences, terminal witnesses and authoritative reconciliation remain
maintained behavior. Source age is not evidence that persisted state is unused.
See [persistence support](persistence-support-window.md). Deleting old source
never migrates a host, revokes an old artifact or authorizes trading.

## Final watchdog callback residue

The optional `ExecutionCoordinatorCallbacks::trackOrder` declaration and the
place/authoritative-flatten dispatch calls have no maintained binding after the
watchdog retirement and are removed together. The maintained producer/consumer
search, canonical compilation and existing dispatch/recovery tests are the
acceptance boundary; no source-token ban is introduced. Order owner creation,
`onIbOrderPlaced`, all persisted events and their readers remain in service.
External code using the retired private composition hook must migrate; no
replacement empty callback or secondary order path is provided.
