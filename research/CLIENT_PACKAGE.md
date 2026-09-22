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
