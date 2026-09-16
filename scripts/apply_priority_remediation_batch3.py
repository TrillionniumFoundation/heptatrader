#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, value: str) -> None:
    (ROOT / path).write_text(value, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    value = read(path)
    count = value.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    write(path, value.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Remove macro/.inc source packaging.  The implementation remains in the same
# execution runtime target and translation-unit authority; this is source
# organization only, not a second persistence service or build product.
# ---------------------------------------------------------------------------
v1 = read("HeptaTrade/execution/execution_generation_support.inc")
capacity = read("HeptaTrade/execution/execution_generation_capacity_support.inc")
v2 = read("HeptaTrade/execution/execution_generation_v2_support.inc")
for old, new in (
    ("OmsGenerationStore::Prepare(", "OmsGenerationStore::PrepareGenerationV1("),
    ("OmsGenerationStore::Recover(", "OmsGenerationStore::RecoverGenerationV1("),
    ("OmsGenerationStore::RecoveryCapacity(", "OmsGenerationStore::RecoveryCapacityGenerationV1("),
):
    v1 = v1.replace(old, new)
    capacity = capacity.replace(old, new)
combined = (
    "// Canonical generation-backed OMS implementation. Compiled exactly once\n"
    "// in the existing execution runtime target; no macro method renaming or\n"
    "// textual .inc inclusion is required.\n"
    "#include \"execution_coordinator.h\"\n\n" + v1 + "\n" + capacity + "\n" + v2
)
write("HeptaTrade/execution/execution_generation_support.cpp", combined)

replace_once(
    "HeptaTrade/execution/execution_coordinator_terminal.cpp",
    '''// Preserve the already-accepted V1 implementation under private method names,
// then layer V2 dispatch on top. This avoids a second persistence authority:
// lookups, pinned indexes, terminal mutation enumeration and request caching are
// still the same code for both formats.
#define Prepare PrepareGenerationV1
#define Recover RecoverGenerationV1
#define RecoveryCapacity RecoveryCapacityGenerationV1
#include "execution_generation_support.inc"
#include "execution_generation_capacity_support.inc"
#undef RecoveryCapacity
#undef Recover
#undef Prepare
#include "execution_generation_v2_support.inc"''',
    '''// Generation storage/recovery is compiled from execution_generation_support.cpp
// in the same execution runtime target. This terminal unit owns only the public
// terminal-fence dispatch boundary.''',
)
for path in (
    ROOT / "HeptaTrade/execution/execution_generation_support.inc",
    ROOT / "HeptaTrade/execution/execution_generation_capacity_support.inc",
    ROOT / "HeptaTrade/execution/execution_generation_v2_support.inc",
):
    path.unlink()

replace_once(
    "HeptaTrade/CMakeLists.txt",
    '''    execution/execution_coordinator_reconnect.cpp
    execution/execution_coordinator_terminal.cpp)
''',
    '''    execution/execution_coordinator_reconnect.cpp
    execution/execution_coordinator_terminal.cpp
    execution/execution_generation_support.cpp)
''',
)

# Keep machine-readable build ownership truthful for both profiles/targets that
# compile the coordinator source list.
build_path = ROOT / "docs/build-targets.json"
build = json.loads(build_path.read_text(encoding="utf-8"))
insertions = 0
for profile in build.get("profiles", {}).values():
    for target in profile.get("targets", []):
        units = target.get("translation_units", [])
        if any(u.get("path") == "HeptaTrade/execution/execution_generation_support.cpp" for u in units):
            continue
        for idx, unit in enumerate(list(units)):
            if unit.get("path") == "HeptaTrade/execution/execution_coordinator_terminal.cpp":
                added = copy.deepcopy(unit)
                added["path"] = "HeptaTrade/execution/execution_generation_support.cpp"
                units.insert(idx + 1, added)
                insertions += 1
                break
if insertions == 0:
    raise RuntimeError("docs/build-targets.json: no coordinator terminal translation units found")
build_path.write_text(json.dumps(build, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

replace_once(
    "tests/python/test_recovery_projection.py",
    '''            "execution_coordinator_recovery.cpp", "execution_coordinator_reconnect.cpp",
            "execution_coordinator_terminal.cpp", "paper_terminal_mutation_manifest.cpp",
''',
    '''            "execution_coordinator_recovery.cpp", "execution_coordinator_reconnect.cpp",
            "execution_coordinator_terminal.cpp", "execution_generation_support.cpp",
            "paper_terminal_mutation_manifest.cpp",
''',
)

# ---------------------------------------------------------------------------
# Synchronize current docs with generation-backed implementation and HPM2.
# ---------------------------------------------------------------------------
replace_once(
    "docs/modules/execution-service.md",
    '''The coordinator consumes the journal's fully validated event sequence without
copying it into another full-history vector. Allocation/projection exceptions
clear partial projections and fence mutations; valid uncertain commands remain
available for reconciliation. See [recovery memory and exception semantics](../technical/coordinator-recovery-memory.md).
This does not implement checkpoints or bounded permanent identity storage.
''',
    '''The coordinator consumes the journal's fully validated event sequence without
copying it into another full-history vector. Allocation/projection exceptions
clear partial projections and fence mutations; valid uncertain commands remain
available for reconciliation. See [recovery memory and exception semantics](../technical/coordinator-recovery-memory.md).
When a verified generation store is selected, startup restores only the bounded hot
replay plus the lineage-bound active tail. Permanent command identity and request
hashes remain in pinned disk indexes and are loaded on demand through a bounded
historical cache. V1 remains readable; V2 seals delta history and has an explicit
legacy JSONL export for downgrade. A missing/corrupt selected generation fails
closed rather than falling back to an older or empty history.
''',
)
replace_once(
    "docs/modules/execution-service.md",
    '''This bounds neither permanent
historical identities nor full-history replay: those remain lifecycle work.
''',
    '''This pre-intent rule is independent of the generation lifecycle: sealed
terminal identities remain durable on disk, while hot coordinator state and active-tail
replay stay bounded by the generation/replay contracts.
''',
)

# Replace the stale recovery-capacity document with one current contract instead
# of appending another correction paragraph to historical prose.
write("docs/technical/oms-recovery-capacity.md", '''# OMS recovery capacity and bounded historical lookup

Status: CURRENT
Applies to: legacy replay, generation-backed startup, new-entry admission and offline diagnostics

## Two supported recovery modes

HeptaTrader has one OMS authority with two startup forms. **Legacy/no-generation**
startup validates and materializes the complete pinned JSONL snapshot before the
first projection callback. **Generation-backed** startup verifies `CURRENT`, the
selected immutable generation, parent lineage, cumulative indexes and the active
journal sentinel, then projects only bounded hot replay plus the active tail.
Neither form applies a valid prefix of later-corrupt evidence, expires a command
identity or resends a possible mutation.

The legacy decoded replay budgets remain:

| Deployment environment | Default | Accepted range | Unit |
|---|---:|---:|---|
| `HEPTA_OMS_REPLAY_MAX_BYTES` | 67108864 | 1–1073741824 | decoded recovery bytes |
| `HEPTA_OMS_REPLAY_MAX_RECORDS` | 65536 | 1–1000000 | replayed records |
| `HEPTA_OMS_REPLAY_MAX_RECORD_BYTES` | 262144 | 1–1048576 | one JSON record excluding newline |

Equality is accepted; one over is rejected. Empty, zero, signed, whitespace,
nondecimal and overflow settings fail initialization. Gzip storage never evades
decoded limits. These are engineering budgets, not measured target-host SLOs.

For generation-backed startup the same byte/record limits bound the selected hot
replay plus active tail. Immutable sealed history is verified through digest-bound
manifests and pinned indexes instead of being reconstructed into the coordinator.
Historical `(agent, session, command)` status/admission lookup is binary-search
based and materializes only a bounded cache entry.

## New-entry pause

`ExecutionCoordinator::PlaceOrder` evaluates trusted journal health after same-ID
replay/conflict and authority checks but before writing a new intent. New entry is
paused at the rounded-up 80 percent byte/record boundary. Unknown capacity also
rejects new entry. A capacity refusal writes no mutation record, caches no new ID,
sets no global mutation block and never removes history. Exact old-command replay,
cancel and separately guarded authoritative flatten remain available according to
their existing safety contracts.

The pause is headroom protection for the active writer, not a reservation for every
future callback. Exit/callback evidence can still grow the active tail after the
observation. Operators must seal/maintain history before a restart would exceed the
configured hot-recovery budget.

## V2 stopped-state sealing

`scripts/hepta_oms_lifecycle.py seal` runs only with the writer stopped. V2 writes an
immutable delta segment, cumulative permanent command/send indexes, bounded hot
replay and a parent-digest-bound runtime manifest. It then atomically replaces the
active ledger with a lineage sentinel and advances `CURRENT`/`CURRENT.runtime` in a
crash-tested order. A crash before publication leaves the previous complete ledger;
pointer/sentinel disagreement after publication fails closed.

New V2 send-attempt indexes are globally ordered by encoded account, execution
domain, timestamp, durable sequence and request identity. Rolling rate-window
lookup therefore uses a file lower bound plus the matching suffix rather than a
full permanent-history scan. Legacy V2 generations without the ordering declaration
remain readable through the conservative compatibility scan; the next successful
seal writes the current sorted format.

Permanent command IDs are never expired. Immutable generation segments remain until
an explicit external retention policy exists. `export` reconstructs a strict
create-only complete JSONL ledger for an older full-ledger reader; downgrade is an
operator action, never an automatic fallback.

## Terminal history binding

Terminal shutdown must prove all possible mutations are represented without making
lifetime history an in-memory vector. Generation-backed terminalization streams the
pinned permanent command index into fixed-size count/digest bindings and adds only
active-tail commands that are not already sealed. HPM2 persists those bindings,
finalization identity and counts in a small manifest; it does not serialize one
`command=`/`correlation=` line per historical mutation. The old HPM1 manifest remains
readable for already persisted evidence.

HPM2 removes the former 4,096-command/correlation terminal-manifest ceiling. The
sealed correlation digest is explicitly a versioned correlation-reference binding
in permanent-command order; active-tail unique correlations are bound as a separate
partition before the final HPM2 digest is produced. Receipts and latches bind the
resulting digest/count tuple and do not infer economic state from it. Final flatness,
zero unresolved commands, complete Broker barriers and drained callbacks remain
separate mandatory terminal evidence.

## Diagnostics and failure semantics

`OmsJournalHealthSnapshot` reports configured limits, validated bytes/records,
pending occupancy and replay reason. The offline command remains read-only:

```bash
python3 scripts/verify_oms_journal_replay.py \\
  --journal /private/offline-copy/oms.jsonl --capacity-json
```

Use a private consistent copy and the deployment's effective limits. Middle-file
corruption, torn records, unsafe paths, snapshot replacement, generation digest or
lineage drift, index corruption and allocation/I/O failure all fail closed. A larger
budget cannot validate corruption or resolve an uncertain send.

## Acceptance

Native and Python tests cover inclusive/over-limit replay budgets, oversized/torn
records, path substitution, capacity admission, old-ID duplicate/conflict, repeated
V2 generations, publication crash points, index corruption, V1→V2 transition,
explicit downgrade export, retrograde timestamps, sorted send-window lookup and
HPM2 terminal history above the former 4,096-record ceiling. The synthetic recovery
growth probe remains useful for measuring process RSS/recovery time, but it is a
measurement tool rather than the persistence design itself.

Target-host multiday stability, filesystem durability characteristics, operator
alert delivery and actual Broker qualification remain external evidence. They do
not reopen repository `OMS-LIFECYCLE-002`; they are tracked as host/operations and
qualification scope.
''')

replace_once(
    "docs/technical/runtime-cost-observations.md",
    '''For S scopes, N records in a scope and K matches, lookup costs approximately
O(log S + log N + K log K), with O(K) temporary pairs. Insertion allocates a
logarithmic index entry. Retained index memory is still O(all sends): removing
unused record copies reduces duplication, not asymptotic growth. Query scratch
now carries timestamps as well as ordinals; large windows still require memory
and sorting. This is not a bounded cache, checkpoint or measured production SLO.
''',
    '''For the active-tail in-memory index, with S scopes, N records in a scope and K
matches, lookup costs approximately O(log S + log N + K log K), with O(K)
temporary pairs. New V2 sealed-history indexes preserve a global
`account/domain/timestamp/sequence/request` order; the generation reader uses a
file lower bound and scans only the requested account/domain suffix, then combines
those rows with the active-tail index. Thus the normal rolling-window query no
longer scans all sealed sends. Pre-remediation V2 generations without the ordering
marker remain readable through a conservative full-scan compatibility path until
the next stopped-state seal rewrites current indexes.

Active-tail memory is O(active-tail sends), while sealed history remains on disk.
A deliberately huge cutoff window can still return O(K) timestamps and incur the
existing result sort. These bounds are implementation costs, not target-host SLOs.
''',
)

# OMS module: add the hot-path and compact-terminal facts next to generation details.
replace_once(
    "docs/modules/oms-journal.md",
    '''The v2 producer stream-merges parent command and send-attempt indexes with the
new tail instead of materializing the complete historical event stream in RAM.
Command identity is never expired by generation maintenance. Immutable parent
segments remain available until an explicit external retention policy exists;
the active writer path itself no longer grows with sealed terminal history.
''',
    '''The v2 producer stream-merges parent command and send-attempt indexes with the
new tail instead of materializing the complete historical event stream in RAM.
The cumulative send-attempt index is globally ordered for account/domain/time-window
lower-bound lookup, so PAPER rate checks do not rescan all sealed sends on each
preview/place call. Older V2 generations without the order declaration retain a
fail-safe compatibility scan until the next seal.

Command identity is never expired by generation maintenance. Immutable parent
segments remain available until an explicit external retention policy exists;
the active writer path itself no longer grows with sealed terminal history.
Terminal shutdown likewise streams sealed mutation history into HPM2 digest/count
bindings and adds only the active tail; it does not rebuild a lifetime command
vector or emit one manifest row per command. HPM1 remains readable compatibility.
''',
)

# XT/QMT: resolve the contradictory local-sidecar/Linux topology. Linux Execution
# remains authority; one mTLS HXQ1 channel to a Windows sidecar is an explicit new
# trust domain. It is still proposal-only until the pinned vendor runtime exists.
replace_once(
    "docs/technical/xtqmt-adapter-contract.md",
    '''The vendor package MUST NOT be imported or linked by Agent, Tool Gateway or the
shared Execution coordinator. The initial supported composition is:

```text
Agent -> Tool Gateway -> Execution Service
                           |
                           | authenticated local IPC
                           v
                    XT/QMT sidecar
                           |
                           | pinned xtquant/QMT API
                           v
                        QMT client
```

The sidecar runs under a dedicated OS identity. It receives no Agent session token
and cannot mint command IDs, decision leases or Execution owner generations. It
may receive only an Execution-issued venue command whose durable intent and send
attempt already exist. Its local credential/configuration directory is unreadable
by Agent/Gateway identities. The sidecar may reach only the reviewed local QMT
endpoint; it is not a generic Python plugin host.

The first implementation is Windows-only because QMT/xtquant runtime custody is a
Windows deployment concern. A remote TCP bridge is explicitly out of scope. If the
Execution process is not colocated on Windows, transport topology must be reviewed
as a new trust domain instead of silently exposing this local protocol over a LAN.
''',
    '''The vendor package MUST NOT be imported or linked by Agent, Tool Gateway or the
shared Linux Execution coordinator. The selected first topology is fixed rather
than left ambiguous:

```text
Agent -> Tool Gateway -> Linux Execution Service
                           |
                           | dedicated HXQ1 v1 over mutually authenticated TLS
                           | exact Windows host/service identity + pinned certs
                           v
                    Windows XT/QMT sidecar
                           |
                           | loopback/local pinned xtquant/QMT API
                           v
                        QMT client
```

Execution remains the sole durable order authority. The cross-host HXQ1 channel is
a new, narrow trust domain owned only by the Execution identity; it is not exposed
to Agent/Gateway processes and is not a generic TCP bridge. Both ends pin protocol,
peer certificate/public-key identity, allowed host, account/profile digest and
message bounds. The Windows firewall accepts the HXQ1 listener only from the exact
Execution host; the sidecar's QMT/API access remains local to Windows. A connection
without mutual identity/profile agreement is `DISABLED`, not degraded authority.

The sidecar runs under a dedicated Windows service identity. It receives no Agent
session token and cannot mint command IDs, decision leases or Execution owner
generations. It may receive only an Execution-issued venue command whose durable
intent and send attempt already exist. Its QMT credential/configuration directory
is unreadable by Agent/Gateway identities and by the Linux host. The sidecar is not
a generic Python plugin host.

This topology deliberately avoids porting the existing Linux/systemd/Unix-socket
Execution authority to Windows merely to obtain process-local IPC. Any future
colocated-Windows Execution design or additional network hop is a separate trust-
domain change and is outside HXQ1 v1. Until the pinned QMT runtime, certificates,
firewall policy and qualification fixture exist, the adapter remains fail-closed.
''',
)
replace_once(
    "docs/technical/xtqmt-adapter-contract.md",
    '''The sidecar protocol is length-prefixed, local-only and versioned independently of
Agent `HTT1`, Execution `HEX1` and supervisor `HSS1`. The initial protocol name is
`HXQ1`, version 1. Frames are bounded to 256 KiB and contain one canonical UTF-8
JSON object with duplicate keys and non-finite numbers rejected. Unknown fields are
rejected for mutation messages.
''',
    '''The sidecar protocol is length-prefixed and versioned independently of Agent
`HTT1`, Execution `HEX1` and supervisor `HSS1`. `HXQ1` version 1 runs only on the
pinned Linux-Execution↔Windows-sidecar mTLS connection above. TLS peer identity is
part of transport admission, not a substitute for message-level service/connection
epoch checks. Frames are bounded to 256 KiB and contain one canonical UTF-8 JSON
object with duplicate keys and non-finite numbers rejected. Unknown fields are
rejected for mutation messages.
''',
)
replace_once(
    "docs/modules/xt-adapter.md",
    '''The implementation order and exact trust/protocol/state requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md). The intended first integration uses a dedicated local Python/QMT sidecar so the vendor runtime does not enter Agent, Gateway or shared coordinator processes. Execution remains the sole durable order authority.
''',
    '''The implementation order and exact trust/protocol/state requirements are defined by the [XT/QMT execution-adapter contract](../technical/xtqmt-adapter-contract.md). The selected first topology keeps the canonical Execution authority on Linux and reaches a dedicated Windows Python/QMT sidecar through one pinned mutually authenticated HXQ1/TLS trust domain; Agent and Gateway cannot reach that listener. Execution remains the sole durable order authority.
''',
)

# Update the machine-readable closed OMS gap disposition without touching real
# external host/Ruleset/telemetry gaps.
gap_path = ROOT / "docs/gap-register.json"
gap = json.loads(gap_path.read_text(encoding="utf-8"))
for item in gap.get("gaps", []):
    if item.get("id") == "OMS-LIFECYCLE-002":
        item["summary"] = (
            "Generation-backed recovery, bounded historical lookup, sorted send-window indexing "
            "and compact terminal-history binding are implemented."
        )
        item["disposition"] = (
            "CLOSED. V2 startup verifies lineage and replays bounded hot state/tail while permanent "
            "command identities remain in pinned disk indexes. New V2 send indexes preserve global "
            "account/domain/time ordering for lower-bound rolling-window queries; legacy V2 indexes "
            "remain readable through the conservative compatibility scan. Terminalization streams "
            "sealed mutation history into fixed-size HPM2 digest/count bindings and includes only "
            "unsealed active-tail commands, removing the prior 4096-command/correlation manifest "
            "ceiling while retaining HPM1 read compatibility. No command identity is expired and "
            "PAPER/LIVE authorization remains false. Target-host multiday operations remain the "
            "separate HOST-OPERATIONS-003 external evidence scope."
        )
        break
else:
    raise RuntimeError("OMS-LIFECYCLE-002 missing")
gap_path.write_text(json.dumps(gap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# Developer map reflects normal TU ownership and the fixed XT trust topology.
replace_once(
    "docs/technical/reconciliation-engine.md",
    '''| `HeptaTrade/execution/execution_coordinator_terminal.cpp` | terminal recovery owner boundary | coordinator and session-supervisor tests |''',
    '''| `HeptaTrade/execution/execution_coordinator_terminal.cpp`, `execution_generation_support.cpp` | terminal recovery owner boundary and generation-backed historical projection | coordinator and session-supervisor tests |''',
)

print("priority remediation batch 3 applied")
