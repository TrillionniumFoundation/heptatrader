# HeptaStrategyClient developer SDK

This package makes the existing forward-only `NativeStrategyClient` consumable
outside the source tree. It is separate from the four-library offline
`HeptaResearch` package. It exports the **same three canonical root-build
archives**, not a copied client implementation, second runtime or broker bridge.
It does not grant access to a session, account, network or trading environment.

## Build, install and consume

Build from a complete checkout (Linux/local Unix transport):

```sh
cmake -S . -B build/client-sdk -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DHEPTA_ENABLE_IBAPI=OFF
cmake --build build/client-sdk --target hepta_research_native_client --parallel 2
cmake --install build/client-sdk --prefix "$PWD/stage/client-sdk" --component StrategyClientSDK
ctest --test-dir build/client-sdk -R '^hepta_research_native_sdk_install$' --no-tests=error --output-on-failure
```

In a different project:

```cmake
cmake_minimum_required(VERSION 3.16)
project(MyStrategy LANGUAGES CXX)
find_package(HeptaStrategyClient CONFIG REQUIRED COMPONENTS Client)
add_executable(my_strategy main.cpp)
set_target_properties(my_strategy PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_link_libraries(my_strategy PRIVATE HeptaStrategyClient::Client)
```

Set `CMAKE_PREFIX_PATH` to the installed prefix, which can be relocated.
`#include <hepta/research/native_strategy_client.h>` is the entry header.
`Client` links `NativeToolClient`, `ToolProtocol` and the platform threading
contract transitively. There is no Gateway, Execution, OMS, OpenSSL or vendor
SDK library dependency. Eight explicit public headers contain request/result
values, normalized instrument/order data, discovery and client APIs; the
privileged host, session binding, registry and execution-authority declarations
are not in this header closure. Existing wire values/layouts are unchanged.

## Execution boundary

Construct a longer-lived `NativeToolClient` with the existing session token-file
and local socket configuration, then borrow it with `NativeStrategyClient`.
The source client lifetime must exceed the wrapper lifetime. Construction from
temporaries is rejected. Never place tokens in source or package metadata.

`PreparedOrder` supports the existing LMT/DAY profile; unsupported contract
fields and historical auto-open/close semantics are rejected, not translated
into a different trade. Preview through the real Gateway. Use `NativeStrategyClient::Persist` to save the proposal,
Execution-issued command ID and permit before `SubmitStored`; an uncertain reply
requires status inspection or the exact same identity, not a new mutation ID.
A true transport result is not an accepted order. Cancellation and authoritative
flatten retain their existing ownership and preview rules. No package API makes
research positions, quotes or fills authoritative. The wrapper does not retry
mutations automatically or connect to a broker.

LIVE remains unavailable; CTP remains deferred, and XT retains its priority.
This SDK is not a historical HeptaDLL source/ABI replacement, strategy migration
certificate, host deployment or broker qualification.

## Borrowed inputs and output reuse

Input strings may refer to a field in the previous result/request or to the
previous diagnostic string. The SDK captures an owned request (or directory,
command ID, binding and token-file path) before clearing outputs. This applies
to direct preview/submit/cancel/flatten/status calls and durable persistence,
load and submission. For example, loading with `request.toolCallId` as the ID
and `request` as the output preserves that original ID rather than erasing it.
The `NativeToolClient` bound-call and token-file reader follow the same rule.

The output arguments themselves must be distinct: do not use a field inside
`result`/`request` as the output `reason`, or the same string as both token and
reason outputs. Callers must not concurrently mutate referenced inputs/outputs.
Local validation and transport failures still clear a previous success result;
invalid input is not repaired. This capture changes neither HTT1/HSR1 bytes nor
the original command, permit, expiry, session binding or execution authority.
There is still no automatic retry or regenerated preview permit.

## Durable client requests and restart recovery

The same SDK now offers three `Persist` overloads for `PreparedOrder`,
`PreparedCancellation` and `PreparedFlatten`, plus `LoadStored` and `SubmitStored`.
They add client request bookkeeping to the existing implementation, not an OMS,
portfolio, local fill database, risk approver or second trading service. The
older forward-only calls remain available to consumers that already own an
appropriate durable request store; these calls do not silently opt into storage.

A typical order flow, after decoding the actual matching successful preview:

```cpp
// directory was explicitly provisioned as an absolute, owned 0700 directory.
// commandId and previewPermit are the ORIGINAL service-issued preview values.
if (!client.Persist(directory, proposal, commandId, previewPermit, reason)) {
    // Stop. A failed or ambiguous persistence result does not authorize sending.
    return;
}
if (!client.SubmitStored(directory, commandId, result, reason)) {
    // Retain the record. Inspect status or explicitly repeat this exact call;
    // never generate a replacement command ID or refresh the old proposal.
    return;
}
// Transport success is not execution success: inspect result.envelope.status.
```

A fresh process needs its normally provisioned `NativeToolClient` configuration,
the outbox directory and the original command ID. `SubmitStored` rereads and
validates the complete stored HTT1 request and makes one call through the normal
NativeToolClient/Gateway boundary. It does not refresh expiry/quantity/price,
obtain another permit, invent a fill, retry automatically or mark an uncertain
result as successful. Cancellation preserves the caller's one stable command ID
and server order ID; flatten preserves its server-issued ID and permit. Neither
operation bypasses the existing service's ownership and risk rules.

Each immutable `<commandId>.hsr` record contains `HSR1` framing, a recovery
binding, a SHA-256 content checksum and the canonical request. Session-token
bytes are replaced with a non-credential marker before encoding. The preview
permit **is** retained and is sensitive: never upload records into Git, CI
artifacts, logs or support bundles. `LoadStored` returns a diagnostic copy with
an empty session token; modifying it cannot change subsequent stored submission.
The checksum reuses the existing native discovery SHA-256 implementation, with
no OpenSSL/vendor library added to the client package. It detects corruption;
it is not a MAC or protection against the same UID/root editing files.

Recovery binds the effective UID, exact configured Gateway socket string and
credential value. Timeout changes and use of a token file versus the same token
value do not change the binding. Token rotation, a different UID or endpoint
fail closed rather than accidentally replaying a command into another session.
`CallBound` resolves a token file once and pins that in-memory value for both
fresh discovery and the one forwarded call, closing a token-rotation window.
This is configuration binding, **not** server attestation or a credential. The
Gateway/Execution service still verifies the real session, lease, preview and
command identity. Configuration changes require explicit operator reconciliation;
do not edit the stored binding to suppress a mismatch.

The storage implementation requires Linux filesystem support for descriptor-
relative no-symlink opens, directory `flock`, `renameat2(RENAME_NOREPLACE)` and
file/directory `fsync`. It walks every path component, requires root/self-owned
ancestors that are not group/world writable (except root-owned sticky temporary
directories), and requires the final directory to be self-owned mode 0700.
Records must be regular, self-owned, single-link mode 0600 files. Links, FIFOs,
unsafe modes, missing/truncated/oversized/noncanonical data, conflicting IDs and
checksum/binding failures are rejected. The API does not create the directory.

Publication writes and syncs a private temporary file, atomically publishes it
without replacing any existing name, verifies the final single-link file and
syncs the directory. Cooperating readers/writers hold one directory lock, so
identical concurrent stores succeed and differing requests cannot overwrite the
winner. No hard-link publication interval can leave a two-link final record
after a crash. Existing identical records are resynced; a visible record after
an interrupted publication is validated and synced before recovery uses it.
Errors never authorize a send. Unsupported filesystem operations are errors,
not a downgrade to an unsafe overwrite. No power-loss or network-filesystem
qualification is claimed by a successful local SIGKILL test.

The record limit is 65,536 request bytes plus 149 framing bytes. Total retained
disk usage remains caller-managed. There is no automatic garbage collection,
acknowledgement file or identity expiry: deleting an uncertain record can lose
its retry identity. A crash before publication can leave an ignored `.pending-`
file; it is never treated as an executable request. Cleanup must be an explicit
stopped-client maintenance decision, not recovery-time guessing.

## Packaging and source identity

`StrategyClientSDK` is an explicit developer install component. Every one of its
install rules is `EXCLUDE_FROM_ALL`: ordinary root installation and production
release contents remain unchanged. The standalone offline SDK still rejects a
required NativeClient component; callers opt into this **separate** package.
No service, account configuration, vendor binary, private history or market
sample is included. Public integration authorization does not establish rights
for a later literal import from the historical repository.

The root repository VERSION owns the release label. Numeric package versions
require an exact match. `strategy-client-build-info.txt` and CMake variables
record source SHA and clean/dirty/unavailable provenance; the build configuration
and toolchain must be retained. They do not promise cross-compiler or future ABI
compatibility. Rebuild consumers for the selected package and qualify it with the
matching Gateway/Execution deployment.

## Executable acceptance

The root research CTest lane installs only this component, relocates it, removes
the original prefix, copies the existing native-client behavioral test as an
external C++11 consumer, and compiles/runs it against the installed archives.
Compiler/sanitizer flags propagate to that consumer. The test checks the actual
archive symbols, exact header/asset closure, dependency targets, unavailable
components/version rejection and negative compilation of privileged classes.
It also executes the generated research install script **without** a component
and requires that no SDK file is installed. Canonical root install/package tests
independently retain their complete production-namespace checks.

The existing real Gateway/Execution and SIGKILL recovery tests remain separate
and are preserved. The native unit/installed-consumer test additionally checks
all three persisted operations, exact identity, corruption, unsafe modes/links,
credential/endpoint changes, SHA-256 known-answer vectors and concurrent writers.
The real-service test now execs a client that previews and persists, kills that
process before any send, and recovers through new client processes. Further
client kills after placement/cancellation and an Execution restart require exact
same-ID results and independently verified single venue-send journal entries.
A persisted forged flatten permit remains rejected by the service. Installed client linkage/behavior is not evidence of a broker
connection, host isolation, remote CI completion or LIVE approval.


## Typed preview approval, without caller JSON scraping

Both `NativeStrategyClient::PreviewAuthorized` overloads issue exactly one
ordinary preview call, for `PreparedOrder` or `PreparedFlatten`, then use the
existing shared result codec to populate `TypedPreviewAuthorization`. Unlike
raw `Preview` / `PreviewFlatten`, a true return means that an approved, matching,
structurally valid preview was received. It does **not** mean a mutation was
sent, an order succeeded, or a stored permit is still usable. False clears the
typed approval; a transported denial/uncertainty remains in the original result
for diagnosis. No fallback, automatic retry or mutation is performed.

```cpp
TypedPreviewAuthorization approval;
NativeToolClientResult result;
std::string reason;
if (!client.PreviewAuthorized(proposal, "strategy-preview-0001",
                              approval, result, reason)) {
    // Handle the diagnostic and original result; do not submit or invent an ID.
    return;
}
if (!client.Persist(privateOutbox, proposal, approval.commandId,
                    approval.previewPermit, reason)) {
    return; // Persistence failed; nothing has been sent.
}
// Persist and submit the SAME immutable proposal with the service-issued ID.
// A true transport result still requires inspecting result.envelope.status.
client.SubmitStored(privateOutbox, approval.commandId, result, reason);
```

The example assumes an existing longer-lived client, immutable proposal and
owned private outbox directory. It is not a complete trading program. Cancellation
has no preview operation and keeps its existing caller-retained command identity.

`TypedToolProtocol::DecodePreviewAuthorization(json, expectedTool, approval,
reason)` is also available for an already received full result envelope. It uses
the existing bounded, duplicate-key-rejecting JSON parser, not a second parser or
substring extraction. It requires the exact current eight-field preview schema,
`approved=true`, `single_use=true`, a canonical command ID, a lowercase SHA-256
permit, positive exact 64-bit expiry/generation and a nonempty bounded service
epoch. Unknown/missing/duplicate fields, cross-tool responses, non-ok statuses,
malformed nested preview data, invalid integer forms and overflow are rejected.
The nested authoritative preview is structurally checked, never converted into
client-supplied authoritative quote, position or risk state.

The codec reports the service's original expiry and identity without refreshing
or locally certifying them. It is a decoder, not an authenticator: parsing copied
or constructed JSON cannot create permission. Credentials, proposal binding,
expiration, owner/generation checks and final execution remain exclusively
service-owned. Use the result of the matching call with the same proposal and
client; never pair an approval from another preview with a changed proposal.

Input strings may borrow prior approval fields or diagnostics; they are captured
before outputs are cleared. Output-to-output aliases are unsupported. Existing
raw APIs, HSR1 records, wire requests, execution paths and eight-header/three-
archive package boundaries are unchanged. The installed/relocated consumer tests
both exported overloads and the shared codec. The real Gateway/Execution crash
fixture now obtains its service-issued values through the typed path and retains
all original durable retry, lost-reply, revocation and SIGKILL checks. Simulator
flatten rejection is retained, not promoted to a qualified flatten capability.


## Opaque prepared-command lifecycle and consumer migration

`PreparedStrategyCommand` provides an explicit `Prepare -> Persist -> Submit`
lifecycle inside the same `NativeStrategyClient`. It adds no transport, parser,
record schema, account ledger, executable or installed header. Existing direct
and durable APIs remain supported; this is an additive source API, not an old
HeptaDLL/ResearchIntentClient ABI alias. Rebuild consumers against the package.

For a supported order or flatten proposal, `Prepare` captures the current
recovery binding, makes exactly one credential-bound preview call and decodes
the matching approval with the existing typed codec. The opaque value retains
the original request, command ID, permit and binding, but no token bytes. It is
`Ready()` but not `Durable()`. Cancellation preparation instead validates its
caller-selected canonical command ID and captures the binding without a network
call; it cannot establish order ownership or cancel eligibility.

```cpp
hepta::research::PreparedStrategyCommand prepared;
NativeToolClientResult result;
std::string reason;
if (!client.Prepare(proposal, "strategy-preview-0001", prepared, result, reason)) {
    return; // No mutation; preserve/inspect any transported rejection.
}
// Keep this original identity. Failure here must not trigger another preview.
const std::string originalId = prepared.CommandId();
if (!client.Persist(privateOutbox, prepared, reason)) {
    return; // No send; a synced/publication attempt may already have left a record.
}
if (!client.Submit(prepared, result, reason)) {
    return; // Uncertainty is not permission to create a new identity.
}
// Examine result.envelope.status; transport success is not execution success.
```

A new process can use `Restore(privateOutbox, originalId, prepared, reason)`.
Restore validates and resyncs only the existing canonical HSR1 record, checks
its credential/UID/socket binding, and clears its output on failure. Successful
restoration sets Ready and Durable, not accepted/sent/filled. `Submit(prepared)`
requires durability, rereads and validates the file on every call, compares its
canonical request bytes to the prepared snapshot, then sends that same loaded
request once through `CallBound`. A cached durable flag cannot bypass missing
files, permissions, a changed binding or replaced request bytes. Copying an
opaque object does not bypass this reread or confer additional permission.

Persist failure keeps the original prepared request and ID for explicit
recovery, but clears Durable and the cached directory. A failure after atomic
publication can leave a valid file: retry persistence with the same object or
explicitly restore the original ID after resolving the error. Never obtain a
new preview merely to repair storage. Token rotation between preparation and
persistence fails rather than rebinding the old permit to a new credential.
The preparation preview itself uses `CallBound`, not separate before/after token
checks that could miss an A/B/A rotation. All existing filesystem, quota,
same-UID trust, power-loss and uncertain-result limitations still apply.

The service remains the authorization authority. Preparation does not promise
that a permit will remain unexpired or survive an Execution restart. An unused
old-epoch permit may be rejected; an already accepted command is reconciled by
its original ID. An application retaining #106's status-only-after-uncertainty
policy must continue to call `Status` in that state, rather than replacing it
with unconditional Submit. There is no new automatic retry/state machine here.

### Explicit legacy-client disposition

| Caller / record | Migration decision | Remaining requirement |
|---|---|---|
| Existing #107 direct and HSR1 callers | Supported unchanged; existing HSR1 place/cancel/flatten records can be restored by the opaque API | Same original credential/endpoint and normal service reconciliation |
| Installed C++11 SDK consumer | Exercises the opaque lifecycle in its existing behavioral executable | Exact-head actual installation/relocation and compiler checks |
| Actual Execution recovery test consumer | Runs both original and opaque lifecycle, including fresh client processes, SIGKILL before first send and after accepted place/cancel, and service restart | Keep the original assertions and inspect the real send-attempt journal |
| #108 Prepare/Persist/Load/Submit application workflow | Source-migration destination is PreparedOrder/PreparedStrategyCommand and NativeStrategyClient Prepare/Persist/Restore/Submit | Adapt proposal limits and application types explicitly; this is not an automatic migration of a deployed application |
| #108 HRO1 `.hro` records | Retain original records and original client at `56fd92bc94fd36e064d18c383ffeef9994d85fea` | Reconcile original IDs using the old caller/service; no HRO1-to-HSR1 conversion or regenerated permit |
| #106 Python StrategyGateway / JSON outbox | Retain at `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5` | Preserve application-key mapping, Decimal validation and status-only uncertain-state policy; no automatic Python API or record conversion |
| Historical HeptaDLL or unknown private/binary users | Retain original repository, releases and notices | Named deployment/consumer and publication-scope disposition before archival |

Do not run old and new applications as independent mutation owners for the same
unreconciled intent. Preserve the old record, stop its automatic processing,
inspect its original command status through the appropriate original session,
and record the application's disposition before moving new work to the new
client. An unknown result, revoked credential or unavailable service remains
unresolved; it is not evidence that the old command never executed. No public
API or green test can certify the absence of unknown external installations.

The existing client behavioral test adds invalid/undurable preparation, token
rotation, record replacement with a valid checksum, old-format rejection,
borrowed inputs and old/new HSR1 identity checks. The same source runs as the
actual installed/relocated external C++11 consumer. The existing real Execution
fixture executes both lifecycle variants and independently verifies one send
per original command. No historical assertion, timeout, sample count, default
production installation, permission or trading capability is relaxed. Exact
source and observed local/remote outcomes belong in the PR evidence, not an
unconditional success statement in this contract.
