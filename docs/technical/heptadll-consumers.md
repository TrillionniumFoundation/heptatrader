# HeptaDLL consumer register and support decisions

Status: CURRENT; bounded source-consumer register, not an external deployment census
Canonical development: `heptatrader/main`; #113-#117 merged; new development remains on main
Accepted implementation baseline: `2443722f47ffc8e880786783ed98509dd1c3f17c`, tree `632d1299eaa0a0b753dcd5d21b93cb5715bf9ac0`
Original retained source: `HeptaDLL-main@f69de179b4d41fe1813d317673abe8116cee76e5`

## Scope and ownership

There is one new-development line. New Data, Analytics, Replay, Strategy and
client features extend their existing targets in heptatrader. The offline SDK,
forward-only StrategyClientSDK and authoritative Execution runtime remain
separate installation/link boundaries. Alternate #106/#108/#109 implementations
are retained compatibility references, not a second active core to merge wholesale.

The owner authorized this consolidation and retention policy. No named external
operator, deployed host or binary-only customer was identified by the repository
reads. The responsible role below means the repository module's maintenance
role, not an invented individual assignment or that person's sign-off. An
external owner marked unconfirmed has NOT accepted a migration. A row may close
for this engineering delivery by an explicit RETAIN decision; it need not be
falsely called migrated to allow main development to proceed.

## Named source consumers and dispositions

| ID / actual consumer | Responsible role / owner evidence | Original version / platform | API or binary; data / persisted format | Decision and acceptance boundary |
|---|---|---|---|---|
| C01: `tests/research/sdk_package_behavior.py` external C++ consumer; `research/examples/replay_main.cpp` | Research SDK maintainers; checked-in consumer, no deployed owner claim | #107 line through merged #113 a982b4c4; C++11 offline SDK, native platform scope in PLATFORMS.md | Data/Analytics/Replay/Strategy archives; named Tick/session/bar CSV, selected BIN/XML profiles; no live state | MAINTAIN canonical offline SDK and actual install/relocation tests; no old-DLL ABI assertion |
| C02: `research/model_manifest.py` order-flow/next-open/portfolio/single consumers | Research SDK maintainers; checked-in commands | #106 acffe4ae reference -> #107/#110 and #113 single; POSIX Python + native C++11 | Digest-bound JSON/JSONL/normalized CSV; explicit native model/portfolio reports; no execution records | SOURCE-ADAPT selected bounded inputs to existing native models; retain old return-dictionary/Decimal callers separately under C06 |
| C03: `tests/research/native_client_tests.cpp` and installed external C++ copy | Agent-entry/client maintainers | #107/#111/#112 through merged #113 a982b4c4; Linux/POSIX C++11 | NativeStrategyClient and existing transport; HSR1 immutable requests | MAINTAIN original prepare/persist/submit/inspect and explicit same-ID retry semantics; original binding required |
| C04: #106 `research/python/hepta_research/gateway.py` application-key LimitIntent/StrategyGateway policy | Agent-entry/client maintainers for new source; deployed Python application owners unconfirmed | acffe4ae; POSIX Python | STK/CASH LMT/DAY; NEW HSA1 application-key bookkeeping plus original-bound native HSR1 requests; OLD JSON requests remain distinct | SOURCE-ADAPT new requests to `research/strategy_gateway.py` + existing NativeStrategyClient. Actual callers: `tests/research/strategy_gateway_behavior.py`, `application_execution_driver.py` and the installed SDK consumer; they test conservative possibly-sent recovery. Old JSON records RETAIN, no automatic conversion or deployed-migration claim |
| C05: #108 ResearchIntentClient and HRO1 record consumers | Original application owners unconfirmed; canonical client maintainers provide destination | 56fd92bc94fd36e064d18c383ffeef9994d85fea; native/POSIX | Old native API and `.hro` HRO1 records | RETAIN old records/callers; canonical opaque lifecycle is an explicit source-adaptation destination, not ABI or binding reconstruction |
| C06: #106 custom Python, wide Decimal, fractional-domain and historical-report consumers | Original application owners unconfirmed | acffe4ae; Python; actual deployments unknown | Old model/gateway APIs, JSON/string-Decimal/null report semantics and unrestricted numeric domains | RETAIN pinned source for unsupported domains. Accept only declared common-domain source adaptations; never silently narrow or relabel output |
| C07: original `heptaBasicStrategy`, `heptaBasicCTAStrategy`, `heptaBasicKindleStrategy`, `heptaBasicAgent`, AgentManager and SimMdSpi consumers | Original program/package owners unconfirmed; old repository maintainers retain source | HeptaDLL-main f69de179 (code retained from 5f370325); VS/Windows and CMake Linux/macOS source entry points, actual installed platforms unconfirmed | Original `heptaHeptaDLL` interfaces/binaries, SPI callbacks, legacy CSV/BIN/XML and strategy-specific state | RETAIN original code/builds/history. New strategy work targets canonical SDK/client; no restoration of direct SPI trading in main. Each real deployed strategy needs owner/version/input/intent golden comparison before replacement |
| C08: upstream Pegasus HeptaTrader, private/outside-organization or binary-only linked applications | Unconfirmed; bounded organization search is not deployment evidence | Version, actual platform and deployed artifact unknown | May depend on original class layout, vtable/ABI or proprietary state | RETAIN original compatible releases/source; outside current replacement support. Do not represent as unused or migrated; this row does not block new canonical features |
| C09: retired main monolith/HeptaStrategy/Pegasus/watchdog and root Interface/Tools consumers | Historical main source record; canonical runtime maintainers | Last pre-removal asset source 6cdae64e04a92d234852aa14670a54538e9e5f9c | Retired build flags and direct-trading composition | RETIRED IN MAIN already; retain recoverable history and explicit legacy-build failure. No empty replacement targets, old SPI bypass or second OMS |
| C10: original vendor SDKs, recorded datasets and original Word manual | Applicable rights holder/publication decision not established here | Original private repository; platform/vendor versions remain at source | Vendor headers/binaries, account/config/data assets, original manual | RETAIN PRIVATE; this port imports none. Organization ownership, green CI or source adaptation is not publication/redistribution clearance |

## Source-level C07 inventory versus deployment evidence

The retained legacy default branch contains the strategy/agent framework components
listed in `heptadll-consumer-migrations.json`. The bounded organization/default-
branch search did not establish a checked-in concrete deployed strategy owner
derived from those bases. That narrows what can be migrated from repository
evidence: the canonical futures intent contract covers the observable limit/FAK/
FOK and explicit open/close vocabulary, while deployment identity, strategy state,
ABI expectations and operational owner acknowledgement remain unknown.

This is deliberately not a negative census. C07 therefore remains
`retained_unconfirmed`, and C08 remains the separate outside/private/binary-only
unknown. Neither may be changed to deployment-level `migrated` merely because
the source framework has a canonical adaptation path.

## Concrete acceptance versus unsupported assumptions

The source and installed consumers in C01-C04 are executable, not just examples
listed in prose. The application continuation adds a real Gateway/exec-child
Execution test with independent OMS send counts, preserving all previous tests.
A passing local or CI fixture qualifies that exact source/SDK pairing only.
Windows/macOS builds, arbitrary compiler ABI pairs, actual customer deployments,
real counterparty connectivity and LIVE permission are not inferred from Linux
C++11/Python tests. Existing runtime capability gates remain unchanged.

A new real-consumer migration records: owner acknowledgement, original commit or
release/artifact digest, actual OS/toolchain, required API/ABI, input dialect,
persistent record type, intended replacements and golden decision/output checks.
A migrated request keeps its original ID and original authority for reconciliation.
Never treat a missing status, a transport failure or copied directory as permission
to submit a replacement ID. Old host state is not migrated by a Git merge.

## Retirement decision

Decision for this delivery: KEEP HeptaDLL-main as the historical/compatibility
retention repository; canonical new development continues only in heptatrader.
No archive/delete/visibility change, release removal or force-push is required.
Do not merge #106/#108/#109 as additional research cores, and do not describe
retained incompatible records as fully superseded.

Retirement is a later scoped decision, not an attempt to prove that no unknown
consumer exists anywhere. Before archiving, resolve or explicitly preserve every
in-scope known deployment/artifact, retain the promised version/state support,
and confirm the publication scope. Unknown out-of-scope users can remain on the
retained version under an explicit support notice. Until then, the RETAIN rows
above are the support decision, not an unfinished prerequisite for every feature.

See [consolidation history](heptadll-consolidation.md),
[application client contract](../../research/STRATEGY-GATEWAY.md),
[client SDK contract](../../research/CLIENT_PACKAGE.md) and
[previous main retirement](legacy-retirement.md).

## Post-#113 independent platform acceptance

#113 is merged as `a982b4c40bf93a6de02c5516320a7bb3a2a34997`; its application-key
implementation and C01-C04 source adaptations are the baseline, not missing work
to reimplement. Continue that same native core and existing integration branch.
The added offline platform acceptance is defined in
[PLATFORMS.md](../../research/PLATFORMS.md). Accepted Windows/macOS research
profiles qualify only their exact source/compiler/architecture and relocated
offline package. They do not qualify the native client/server on those systems,
a legacy DLL/vtable, a Universal binary or an unidentified installed strategy.

C07/C08 actual owners, deployed platforms and artifact identities remain
unconfirmed, even when the corresponding canonical offline SDK passes CI.
Their disposition stays RETAIN. A real replacement entry must supplement this
register with owner acknowledgement, original artifact digest/toolchain, required
API/ABI/data/record formats, golden input/intent/output evidence and rollback
version. No archive, deletion, visibility change or record conversion occurs.

## Request-state handoff for C04

The new application directory is explicitly HSA1, not an HSR1 serializer or a
second OMS. Its `intent.json` binds the application key, normalized fields and
credential scope; `command.id` names the original canonical HSR1 request. The
`possibly-sent` marker is durable before the first submit. Repeated submission
after that marker performs only inspection of that same command, including an
unknown/not-found status. Absence of a receipt is not permission to resend.

A lost preparation response is handled only by explicit `adopt-preparation`
with the original command ID and validation against the already persisted HSR1
request, intent and binding. It does not fabricate a preview, renew a permit or
convert an old JSON/HRO1 record. These are source-consumer support decisions,
not confirmation that an unidentified application's state directory was migrated.

The C01/C03 external consumers are rebuilt with the declared language standard
and strict compiler diagnostics. In particular, the corrected C03 fixture in
4eb299bb rejects silent C++14 extensions in an advertised C++11 consumer. Exact
source, package and real Gateway/Execution acceptance belongs in the associated
PR/run receipts; this register does not turn a job definition into a passed run.

## C04 independent packaged-server acceptance

The selected application migration now has a mandatory separately packaged
client/server scenario in the shared core release driver. It consumes the actual
relocated StrategyClientSDK launcher/native binary and the admitted core server
package, rather than pairing that SDK only with a source-tree service test
executable. The existing multi-UID InstalledRuntime fixture and single Execution
Service remain the implementation. See [paired-package acceptance and evidence
scope](core-release-acceptance.md#independently-packaged-application-client-and-core-server).

This advances delivery acceptance for C03/C04 without inventing a new deployed
consumer or owner acknowledgement. C01/C02 remain independently consumable
through the existing offline SDK and native platform tests. C05-C08 retain their
original requests/versions, C09 remains retired in main and C10 remains private.
A matching-source synthetic package pair does not replace those support decisions
or establish publication clearance; HeptaDLL-main stays the compatibility source.


## Machine-enforced migration and retirement state

The human-readable table above is paired with
[`heptadll-consumer-migrations.json`](heptadll-consumer-migrations.json) and
[`heptadll-lifecycle-status.json`](heptadll-lifecycle-status.json).
`tests/python/test_heptadll_migration_state.py` rejects a deployment-level
`migrated` or `retired` claim unless it carries the named owner acknowledgement,
original artifact SHA-256, platform/toolchain, API/ABI, data and persisted-record
formats, golden comparison evidence and rollback version. C07/C08 therefore
remain explicit retained/unconfirmed consumers; their unknown deployment facts
are not manufactured to make repository retirement appear complete.

Canonical feature integration and legacy repository retirement are independent
state machines. New Data/Analytics/Replay/Strategy/client work may remain
`integrated` while the legacy repository remains `retained`. Archival may be
claimed only when every blocking retirement gate in the lifecycle receipt is
true. This check is source evidence only; it does not discover an external
binary or turn a missing deployment owner into a migration sign-off.
