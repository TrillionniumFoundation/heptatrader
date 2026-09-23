# HeptaDLL consumer register and support decisions

Status: FINAL SUPPORT DISPOSITION; archive-ready policy, not an external deployment census
Canonical development: `heptatrader/main`; #113-#117 merged; new development remains on main
Accepted implementation baseline: `2443722f47ffc8e880786783ed98509dd1c3f17c`, tree `632d1299eaa0a0b753dcd5d21b93cb5715bf9ac0`
Original retained source: `HeptaDLL-main@f69de179b4d41fe1813d317673abe8116cee76e5`

## Scope and ownership

There is one new-development line. New Data, Analytics, Replay, Strategy and
client features extend their existing targets in heptatrader. The offline SDK,
forward-only StrategyClientSDK and authoritative Execution runtime remain
separate installation/link boundaries. Alternate #106/#108/#109 implementations are closed Draft PRs whose pinned
branches/commits remain compatibility references, not active cores to merge
wholesale. Their closure reduces parallel implementation lines; it does not
convert C05/C06 records or establish deployment-level migration.

The owner authorized this consolidation and retention policy. No named external
operator, deployed host or binary-only customer was identified by the repository
reads. The responsible role below means the repository module's maintenance
role, not an invented individual assignment or that person's sign-off. An
external owner marked unconfirmed has NOT accepted a migration. Final retirement
therefore does not relabel unknown deployments as migrated: active compatibility
support ends, while the final private repository remains available read-only as
source/history after archival.

## Named source consumers and dispositions

| ID / actual consumer | Responsible role / owner evidence | Original version / platform | API or binary; data / persisted format | Decision and acceptance boundary |
|---|---|---|---|---|
| C01: `tests/research/sdk_package_behavior.py` external C++ consumer; `research/examples/replay_main.cpp` | Research SDK maintainers; checked-in consumer, no deployed owner claim | #107 line through merged #113 a982b4c4; C++11 offline SDK, native platform scope in PLATFORMS.md | Data/Analytics/Replay/Strategy archives; named Tick/session/bar CSV, selected BIN/XML profiles; no live state | MAINTAIN canonical offline SDK and actual install/relocation tests; no old-DLL ABI assertion |
| C02: `research/model_manifest.py` order-flow/next-open/portfolio/single consumers | Research SDK maintainers; checked-in commands | #106 acffe4ae reference -> #107/#110 and #113 single; POSIX Python + native C++11 | Digest-bound JSON/JSONL/normalized CSV; explicit native model/portfolio reports; no execution records | SOURCE-ADAPT selected bounded inputs to existing native models; retain old return-dictionary/Decimal callers separately under C06 |
| C03: `tests/research/native_client_tests.cpp` and installed external C++ copy | Agent-entry/client maintainers | #107/#111/#112 through merged #113 a982b4c4; Linux/POSIX C++11 | NativeStrategyClient and existing transport; HSR1 immutable requests | MAINTAIN original prepare/persist/submit/inspect and explicit same-ID retry semantics; original binding required |
| C04: #106 `research/python/hepta_research/gateway.py` application-key LimitIntent/StrategyGateway policy | Agent-entry/client maintainers for new source; deployed Python application owners unconfirmed | acffe4ae; POSIX Python | STK/CASH LMT/DAY; NEW HSA1 application-key bookkeeping plus original-bound native HSR1 requests; OLD JSON requests remain distinct | SOURCE-ADAPT new requests to `research/strategy_gateway.py` + existing NativeStrategyClient. Actual callers: `tests/research/strategy_gateway_behavior.py`, `application_execution_driver.py` and the installed SDK consumer; they test conservative possibly-sent recovery. Old JSON records RETAIN, no automatic conversion or deployed-migration claim |
| C05: #108 ResearchIntentClient and HRO1 record consumers | Original application owners unconfirmed; canonical client maintainers provide destination | 56fd92bc94fd36e064d18c383ffeef9994d85fea; native/POSIX | Old native API and `.hro` HRO1 records | END mutation ABI/support. `NativeStrategyClient::InspectLegacyHro1` is the only canonical compatibility surface: strict read-only status reconciliation, no HSR1 conversion, preview, submit, permit refresh or invented binding. Old callers remain pinned to archived source. |
| C06: #106 custom Python, wide Decimal, fractional-domain and historical-report consumers | Original application owners unconfirmed | acffe4ae; Python; actual deployments unknown | Old model/gateway APIs, JSON/string-Decimal/null report semantics and unrestricted numeric domains | END active compatibility support; preserve frozen source for pinned consumers. Canonical code accepts only declared bounded domains and never claims arbitrary Decimal/report equivalence. |
| C07: original `heptaBasicStrategy`, `heptaBasicCTAStrategy`, `heptaBasicKindleStrategy`, `heptaBasicAgent`, AgentManager and SimMdSpi consumers | Original program/package owners unconfirmed; old repository maintainers retain source | HeptaDLL-main f69de179 (code retained from 5f370325); VS/Windows and CMake Linux/macOS source entry points, actual installed platforms unconfirmed | Original `heptaHeptaDLL` interfaces/binaries, SPI callbacks, legacy CSV/BIN/XML and strategy-specific state | END old ABI/runtime support and preserve source/history in the private archive. New strategy work uses canonical SDK/client only; direct SPI, AgentManager and old simulator composition are forbidden in main. No deployment-level migration claim is made. |
| C08: upstream Pegasus HeptaTrader, private/outside-organization or binary-only linked applications | Unconfirmed; bounded organization search is not deployment evidence | Version, actual platform and deployed artifact unknown | May depend on original class layout, vtable/ABI or proprietary state | END active support without claiming these consumers are absent or migrated. The archived private source remains available for pinned binaries and forensic comparison; canonical ABI compatibility is not promised. |
| C09: retired main monolith/HeptaStrategy/Pegasus/watchdog and root Interface/Tools consumers | Historical main source record; canonical runtime maintainers | Last pre-removal asset source 6cdae64e04a92d234852aa14670a54538e9e5f9c | Retired build flags and direct-trading composition | RETIRED IN MAIN already; retain recoverable history and explicit legacy-build failure. No empty replacement targets, old SPI bypass or second OMS |
| C10: original vendor SDKs, recorded datasets and original Word manual | Applicable rights holder/publication decision not established here | Original private repository; platform/vendor versions remain at source | Vendor headers/binaries, account/config/data assets, original manual | PRESERVE PRIVATE IN PLACE. Archival does not change visibility or add redistribution/publication rights; none of these assets enters canonical packages. |

## Source-level C07 inventory versus deployment evidence

The retained legacy default branch contains the strategy/agent framework components
listed in `heptadll-consumer-migrations.json`. The bounded organization/default-
branch search did not establish a checked-in concrete deployed strategy owner
derived from those bases. That narrows what can be migrated from repository
evidence: the canonical futures intent contract covers the observable limit/FAK/
FOK and explicit open/close vocabulary, while deployment identity, strategy state,
ABI expectations and operational owner acknowledgement remain unknown.

This is deliberately not a negative census. A follow-up GitHub global code search
on 2026-09-23 for `heptaHeptaDLL`, `heptaBasicStrategy`,
`heptaBasicCTAStrategy`, and `heptaAgentManager` returned only the retained
HeptaDLL source plus heptatrader migration records; it found no additional public
source consumer. That still cannot observe private repositories, unindexed
branches, installed libraries or binary-only applications. C07 therefore ends
active support with its source preserved, and C08 remains an explicitly unverified
outside/private/binary-only category with support ended. Neither becomes a
deployment-level `migrated` claim merely because the source framework has a
canonical adaptation path or a public search is empty.

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

Decision for this delivery: END HeptaDLL ABI/runtime compatibility support and
archive `HeptaDLL-main` after the final compatibility notice is merged. Canonical
new development continues only in `heptatrader`. The archive preserves the
private source, history and historical integration branch read-only; it does not
import them into the canonical build, publish private assets, convert host state,
or revoke an already deployed binary.

This is a support-policy closure, not a fabricated deployment census. C05-C08 do
not become deployment-level `migrated`: HRO1 is read-only reconciliation only;
wide Decimal/custom Python, direct-SPI Strategy/Agent ABI and unknown binary-only
consumers remain pinned to frozen source if they still exist. Their active support
ends and therefore they no longer block repository archival. C10 remains private
in place. `heptadll-consumer-migrations.json` and
`heptadll-lifecycle-status.json` are the machine-enforced truth for this boundary.

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
Their final disposition is support-ended/source-preserved, not migrated. A real
replacement entry, if one is later required for an archived consumer, should still
record owner acknowledgement, original artifact digest/toolchain, required
API/ABI/data/record formats, golden input/intent/output evidence and rollback
version. Archival is the source-preservation action; it does not delete history,
change visibility, convert records or claim those deployments migrated.

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
original bytes/versions only as frozen archived compatibility provenance; active
support ends. C09 remains retired in main and C10 remains private. A matching-
source synthetic package pair does not turn unknown deployments into migrated
ones or establish publication clearance.


## Machine-enforced migration and retirement state

The human-readable table above is paired with
[`heptadll-consumer-migrations.json`](heptadll-consumer-migrations.json) and
[`heptadll-lifecycle-status.json`](heptadll-lifecycle-status.json).
`tests/python/test_heptadll_migration_state.py` rejects a deployment-level
`migrated` or `retired` claim unless it carries the named owner acknowledgement,
original artifact SHA-256, platform/toolchain, API/ABI, data and persisted-record
formats, golden comparison evidence and rollback version. The final support
statuses for C05-C08 intentionally do not use deployment-level `migrated` or
`retired`; their unknown deployment facts are not manufactured to make archival
look like migration completion.

Canonical feature integration and legacy repository archival are independent
state machines. The lifecycle receipt marks archive readiness only when every
consumer category has a final support disposition, old record mutation paths are
closed, private publication scope is preserved and source/history retention is
fixed. This check does not discover an external binary or turn a missing owner
into migration sign-off.
