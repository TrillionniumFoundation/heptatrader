# Application-key Python consumer migration

Status: IMPLEMENTED IN THE #113 CONTINUATION; exact-head acceptance is recorded in PR #113
Baseline: `211166aed4ff6f6ae4b3661ec3a279b324bc7ce3` on `integration/heptadll-modular-20260920`
Reference caller: #106 `research/python/hepta_research/gateway.py` at `acffe4ae84a8e4377fd92b0ac536a865c6f6b6f5`

## One implementation and three delivery boundaries

The offline HeptaResearch package remains Data/Analytics/Replay/Strategy only.
The root-built opt-in StrategyClientSDK adds `hepta-strategy-native`, a thin
process adapter over NativeStrategyClient, and `hepta-strategy-gateway`, the
isolated-Python application-key caller. Production installation receives neither.
The existing eight client headers and three archives are unchanged as a delivery
boundary; one developer executable, one Python module, its launcher and this
contract are additional assets. No Execution implementation, new socket codec,
request serializer, account ledger or broker dependency enters the client.

The native adapter delegates Prepare/Persist/Restore/Submit/Inspect to the existing
SDK. `MatchesOrder` validates the exact durable opaque request against the expected
application limit intent and original recovery binding, using the existing HTT1
encoder. It does not contact a service or write records. Submit/Inspect still
reload the same opaque snapshot, so changing a request between validation and
forwarding cannot change the submitted intent. Generic native explicit same-ID
resend APIs remain supported; Python deliberately selects a different policy.

## Supported application profile

`LimitIntent` takes explicit instrument, symbol, sec_type, exchange, currency,
side, quantity, limit_price, reference_price and expires_at_ms. This migration
preserves the reference caller's STK/CASH LMT/DAY profile; it does not erase
futures/options/close-today fields to fit that profile. The service independently
validates contract identity, risk, permissions, expiry and preview authority.

Numeric inputs are positive decimal strings, at most 1e12, that round-trip through
the shortest binary64 decimal representation without changing their decimal
value. Canonical trailing-zero variants identify the same intent. This is a
bounded binary64 source-adaptation contract, NOT unrestricted Decimal, old Python
return-dictionary compatibility or exact real-number arithmetic. Wider inputs
reject. Expiry is a positive signed-64 integer in epoch milliseconds. No host
clock or account context is invented to fill missing intent fields.

## Application storage and failure policy

HSA1 is a dedicated owner-only application directory, not another request outbox.
It contains a format marker and a SHA-256-named directory per application key.
Each key has immutable normalized intent/scope metadata, an immutable command-ID
mapping, a `requests` directory written ONLY by NativeStrategyClient as HSR1,
and eventually one immutable `possibly-sent` marker. Python never stores session
token bytes, preview permits, orders, positions or fills. Native binding includes
the original credential, UID and socket, and is rechecked without rebinding.

Directory components are opened relative to descriptors without following
symlinks. Directories require owned mode 0700; files require owned regular mode
0600 and one hard link. Per-key flock serializes cooperating processes. Immutable
no-replace publication fsyncs the file and directory before sending. These are
local POSIX filesystem/cooperating-same-UID guarantees, not an adversarial-root,
malicious-same-UID, network-filesystem or physical power-loss qualification.

| Operation / boundary | Behavior |
|---|---|
| `prepare(key, intent)` | Durably records PREPARING intent before one native preview/persist; never places an order |
| Repeated identical preparation | Validates the original HSR1 request and returns its ID; no new preview |
| Same key, changed intent | Rejects; neither rewrites the original nor allocates another execution ID |
| Lost preview/persist output or incomplete mapping | Fails PREPARATION_INCOMPLETE, preserving original data; no implicit replacement preview |
| `adopt_preparation(key, original_id)` | Explicitly validates existing native HSR1, intent and original binding, then records that ID; never prepares or sends |
| First `submit(key)` | Validates HSR1 and intent, fsyncs `possibly-sent`, then attempts one native Submit |
| Marker write/sync fails | Does not call Submit; a visible marker after failure makes subsequent recovery status-only |
| Marker already exists | Calls only native Inspect with a separate fresh query ID |
| Timeout, lost response, process death, rejected/unknown/not-found/uncertain result | Never clears the marker, retries the mutation, changes its ID, renews its expiry or obtains another permit |
| `inspect(key)` | Validates the original request/binding and queries it; does not create a possibly-sent marker |
| Credential, UID, endpoint, request or intent changed | Rejects; no automatic credential migration or old-record conversion |

A crash after marking but BEFORE the native send deliberately sacrifices automatic
liveness. An unknown status is not permission to send again. An operator must
reconcile through the original authority; deleting application state is not an
approved recovery operation. Application errors can occur after an actual send;
process failure never proves that no venue effect occurred. Successful process
exit/transport likewise does not prove an order or fill: inspect the returned
service envelope. Failed preparations may expose the original command ID in the
native error response; retain it for explicit adoption rather than generate one.

Output and error streams are bounded to 1 MiB each, and process time is bounded.
The native child gets a minimal environment without inherited credential/loader
hooks; credentials are supplied only by an explicit private token-file path.
The installed launcher re-execs isolated Python and resolves its module/native
executable relative to its relocated install prefix. Supply an explicit `--native`
only to select another reviewed SDK binary. No source-tree fallback is installed.

## Build and use

From the repository root, build the normal canonical core with tests. The existing
`hepta_research_test_binaries` aggregate also builds the native adapter. Install
only the selected developer component:

```sh
cmake --build build/core --target hepta_research_test_binaries --parallel 2
cmake --install build/core --prefix /opt/hepta-client-sdk --component StrategyClientSDK
/opt/hepta-client-sdk/bin/hepta-strategy-gateway --help
```

A caller supplies its already authorized local service and token-file paths:

```sh
/opt/hepta-client-sdk/bin/hepta-strategy-gateway prepare \
  --socket /run/hepta/client.sock --token-file /private/session.token \
  --store /private/strategy-application --key strategy-decision-0001 \
  --instrument EXAMPLE --symbol EXAMPLE --sec-type STK --exchange EXCHANGE \
  --currency USD --side BUY --quantity 1 --limit-price 100 \
  --reference-price 100 --expires-at-ms 1900000000000
```

These are placeholders, not real account configuration, a currently valid
preview, or trading authorization. `submit`, `inspect` and `adopt-preparation`
reuse the same common paths and key; they do not accept new intent fields.
`adopt-preparation` additionally requires `--command-id` with the original ID.
The installed `strategy_gateway.py` also exposes the Python API directly; its
store and transport classes intentionally differ from the old Python API.

## Registered acceptance

`tests/research/strategy_gateway_behavior.py` exercises application conflicts,
number bounds, duplicate preparation, pre/post-publication errors, actual fsync
failure injection, process concurrency, SIGKILL before send, lost replies,
unknown/error outcomes, corrupt/unsafe files, legacy-directory refusal and actual
native adapter binding/argument behavior. No empty/skipped suite is acceptance.

The existing `native_client_tests.cpp` additionally tests MatchesOrder, changed
intent, foreign binding, rotated credentials and replacement of a valid opaque
request. Its complete existing behavioral suite still runs against installed
headers/archives after relocation.

The existing `native_execution_tests.cpp` runs the actual Python/native consumer
through a real Gateway and a separate exec-child Execution simulator. Four
concurrent clients, one dropped accepted reply, an Execution SIGKILL/restart,
credential rotation and a client SIGKILL after marker/before send leave exactly
one actual `place_send_attempt` for two prepared intents. The pre-send-crash
intent remains unsent. The existing simulator deliberately retires unfilled
in-memory orders at restart; no restored active order or broker fill is invented.
Every original lifecycle, retry, latency-sample and journal assertion remains.

`native_sdk_package_behavior.py` repeats the policy and real-service scenario
using ONLY the relocated installed application module/native executable. It keeps
original source/build prefixes absent from the external C++ consumer, checks
actual archive/executable symbols and confirms that default installation is empty
for this component. Runtime/host/broker qualification remains separate.

## Supported migration and retained records

This is a runnable source-migration destination for the selected #106 application
policy, not a conversion of #106 JSON records, #108 HRO1, old return dictionaries,
arbitrary Decimal calls or external binary applications. An existing unmarked
legacy directory is rejected without rewriting its records. The consumer-by-
consumer decisions and support boundary are in
[the consumer register](../docs/technical/heptadll-consumers.md).
The original HeptaDLL repository, releases/history and alternate references are
retained. Source integration proceeds without claiming that unknown deployments
were migrated; original-repository archival is not part of this delivery.
