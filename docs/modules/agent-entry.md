# Agent entry and MCP bridge

Status: CURRENT
Applies to: repository HEAD
Implementation: `.agents/plugins`, `adapters/mcp/hepta_mcp_server.py`, `HeptaTrade/cli`, `HeptaTrade/client`, `plugins/heptatrader-agent-os`, `scripts/hepta_agent_mcp_launcher.py`, `scripts/hepta_agent_trust_domain.py`, `research/include/hepta/research/native_strategy_client.h`, `research/src/native_strategy_client.cpp`
Tests: `tests/native_tool_client_tests.cpp`, `tests/unix_tool_server_tests.cpp`, `tests/python/test_mcp_bridge.py`, `tests/python/test_installed_runtime_processes.py`, `tests/research/native_client_tests.cpp`, `tests/research/native_gateway_tests.cpp`, `tests/research/native_execution_tests.cpp`

## Responsibilities

The Agent entry layer exposes the bounded HeptaTrader tool catalog to an Agent, CLI caller, or native client. It discovers tool descriptors, validates the advertised protocol and schema hashes, encodes typed requests, reads bounded responses, and preserves the required execution command identity across uncertain retries.

It is not an execution authority. It does not own broker credentials, broker sockets, order state, final risk decisions, or reconciliation truth.

## Public contracts

- MCP process: `adapters/mcp/hepta_mcp_server.py`.
- CLI: `heptactl` for ordinary tool calls and `hepta-sessionctl` for operator-owned session lifecycle operations.
- Native library: `hepta_native_tool_client`.
- Transport: authenticated local Unix stream socket with bounded length-prefixed messages.
- Discovery: protocol `hepta.agent-tools`, current protocol version 1 and discovery schema version 2.
- Mutations require a stable canonical `command_id`; an uncertain retry reuses the exact same value.

The entry layer rejects unknown fields, unsupported protocol ranges, duplicate tool descriptors, invalid schema hashes, oversized messages, malformed result envelopes, and result/tool mismatches.

## Identity and secret handling

The MCP bridge reads its session token from a regular, non-symlink file. It checks owner, mode, link count, size, stable metadata before and after open, UTF-8 validity, and the configured Agent UID. The token is sent only over the local tool socket and is never logged.

Deployment must assign each mutually untrusted Agent a separate OS identity, token, socket, and trust-domain configuration. Sharing one token or Unix identity across Agents collapses the owner and fencing model.

## State and concurrency

The Python MCP bridge keeps only a discovered descriptor catalog and catalog digest in memory. Each native call uses a new Unix socket connection and a bounded timeout. A catalog digest change during one MCP process lifetime is rejected rather than silently accepted.

The native client performs the equivalent wire and discovery checks in C++. Neither client persists execution state.

## Failure semantics

- Missing or unsafe token: fail before connecting.
- Missing, relative, or oversized socket path: fail before connecting.
- Timeout or early close: return an error/uncertain outcome; never manufacture success.
- Unknown result status or malformed JSON: fail closed.
- Lost mutation response: query command status or retry with the same command ID; never generate a new mutation identity for the same intent.

## Observability

Caller-visible output must include tool name, typed status, reason code, detail, order ID when available, and payload. Logs must omit session-token contents. Connection, protocol, validation, timeout, and response-envelope failures should be counted separately by the hosting environment.

## Test expectations

Tests cover discovery, framing, schema-hash validation, result parsing, command-ID requirements, Unix server interaction, timeout bounds, and malformed responses. Changes to field IDs, protocol versions, result statuses, or command-ID rules require cross-language compatibility vectors.

## Known limitations

The entry layer currently supports a local Unix transport only. It does not provide remote TLS, multi-host routing, or LIVE authority. Those capabilities must be introduced as separate trust domains rather than by exposing the broker API directly to an Agent.

## Developer reference and executable vectors

[`Agent tool protocol`](../technical/agent-tool-protocol.md) documents the framing,
field IDs, discovery digest, exact envelope types, identity lifecycle and a
working JSON-RPC cancel example. `tests/python/test_mcp_bridge.py` executes the
real Python process against a fragmented local Unix responder, including an
uncertain mutation followed by the same-ID duplicate retry. It also tests token
file safety, catalog drift and strict JSON rejection. Native-client tests remain
independent cross-language evidence; neither suite grants Broker authority.

## Research strategy adapter

`hepta_research_native_client` is a forward-only wrapper around NativeToolClient.
It accepts an immutable LMT/DAY proposal, requests the existing preview tool and
submits only with the matching Execution-issued command ID and preview permit.
It never allocates mutation IDs or retries automatically, and transport success
is not execution success. Unsupported HTT1 contract identity fields are rejected
rather than discarded. A failed call clears stale output.

The wrapper borrows a named `NativeToolClient` that must outlive it. Construction
from mutable or const temporaries is deleted: otherwise the stored reference
would dangle as soon as the constructing expression ended. Mutable and const
lvalue clients remain supported. This compile-time guard does not extend an
lvalue's lifetime or make an externally destroyed client safe. The existing
native-client test checks all four construction cases and retains its real
wire-codec, request-identity and failed-transport assertions.

The two native adapter source paths are owned by this module, more specifically
than the surrounding LOCAL_ONLY [research SDK](research-sdk.md). Root-build wire
and real local Gateway tests cover proposal identity, uncertain outcomes, no
automatic retry and session revocation. The negative Gateway fixture has no
venue and does not establish risk approval or broker qualification.

## Real Execution lifecycle acceptance

`hepta_research_native_execution_tests` complements, rather than replaces, the
negative Gateway fixture. It runs the actual `NativeStrategyClient`,
`NativeToolClient`, `ToolGatewayRuntimeComposition`, session supervisor and
`ExecutionServiceRuntimeComposition` over local Unix sockets, using only the
canonical deterministic simulator. The test links privileged service libraries;
the production research native client still links only NativeToolClient and its
wire dependency. The four offline SDK libraries remain unchanged.

The fixture obtains real service-issued previews and opaque permits. It verifies
accepted placement and fills, a resting order and guarded cancellation, stable
command identity on exact retries, rejection of changed-payload ID reuse, and
WATCH/revoked-session denial. A transparent proxy drops exactly one *accepted*
placement reply: the client clears stale output and never retries automatically;
an explicit retry with the same ID is a duplicate. The test then stops and
restarts the Execution component with retained state, checks its changed epoch,
restored positions and durable command identities, and independently replays the
OMS to count exactly three place-send attempts and one cancel-send attempt. The
Gateway's chained decision audit must verify.

The current simulator does not implement authoritative flatten. Its real
preview must remain rejected; neither this test nor the strategy client replaces
that rejection with an opposite-side placement.

Run from a complete checkout as an unprivileged user (root fails, not skips):

```sh
cmake -S . -B build/research-native -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DHEPTA_ENABLE_IBAPI=OFF
cmake --build build/research-native --target hepta_research_test_binaries --parallel 2
ctest --test-dir build/research-native -R '^hepta_research_' --no-tests=error --output-on-failure
python3 scripts/verify_build_ownership.py --profile core
```

The existing `core;research` CTest selection and research/core build aggregates
include this target. Both reviewed build profiles record its real dependencies.
The integration workflow therefore executes it through the maintained root
build, with no separate acceptance harness or production source substitution.

This first scenario is simulator/component acceptance, not a process crash
campaign. The additional SIGKILL cases below still do not establish installed
systemd, multi-UID isolation, broker qualification, production latency, or
permission to trade. An existing C++ test-only custodian seam lets one
unprivileged fixture UID provision the PAPER-template session; it is not exposed
as a production configuration option. Keys/tokens and market data are synthetic
and scoped to a private temporary directory. CTP stays deferred, XT retains its
existing priority, and LIVE remains unavailable.

## SIGKILL and exec recovery acceptance

The same test executable additionally starts the real Execution composition in
an **exec'd child process**. The Gateway remains alive in the parent. A private
read-only observation/control socket is passed as descriptor 3; spawn closes all
other inherited descriptors above standard IO. This Linux/glibc fixture uses
`posix_spawn`, not a forked copy of a multithreaded Gateway. Its child mode is a
test helper, not an installed daemon or a new production control surface.

The transparent proxy withholds each real accepted response while the fixture
injects three SIGKILL crashes. Every killed PID is reaped and verified to have
terminated by SIGKILL. Each restart execs a new process against the same private
state, journal and fence credential, and must obtain a different service epoch.
The fault windows are deliberately distinct:

- **Durable fill, lost placement reply.** Before killing, the fixture observes
  both the actual simulated fill and durable terminal-owner removal. Observing
  the in-memory position alone is not a persistence barrier: the event sink runs
  outside the venue lock. The client loses the accepted placement reply, clears
  stale success and makes no automatic retry. After exec/recovery, positions and
  command identity persist and an explicit exact retry is a duplicate.
- **Durable cancellation, lost cancel reply.** The same barrier requires the
  cancellation event and terminal-owner removal before SIGKILL. After recovery,
  the original cancel ID deduplicates and replaying the old placement cannot
  resurrect the cancelled order.
- **Accepted but unfilled active order.** A nonmarketable order is killed while
  active, with no fill barrier. The simulator's documented restart contract
  retires its in-memory active orders. The recovered admission count persists,
  position does not increase, and a same-ID retry neither revives the order nor
  invents a fill. This is not a claim that broker orders disappear on restart.

Additional assertions reject changed-payload ID reuse and an unused preview
permit issued by the old service epoch. A live Gateway cannot fabricate success
while Execution is dead. Independent final OMS replay must count exactly three
unique place-send attempts and one cancel-send attempt for this separate crash
fixture, and its chained Gateway decision audit must verify. No test-only mock
approves risk, manufactures a broker ID, supplies a fill, or restores a position.

The pre-existing component lifecycle, WATCH/revocation and unsupported-flatten
checks remain intact. No CMake target, translation unit, production dependency,
protocol, ownership inventory or installed artifact is added by these cases.
The existing nonempty CTest selection and 30-second timeout remain enforced.
Readiness, observation and wait/reap operations have bounded waits; RAII removes
private fixtures and terminates children on ordinary assertion failures. The
final child exits normally so sanitizer exit/leak checks can execute; SIGKILL'd
children cannot perform exit-time leak checks.

This extends evidence to actual process death/re-exec in the local simulator.
It does **not** establish host power-loss durability, an arbitrary instruction
crash campaign, different-UID/systemd deployment, broker recovery, production
latency, complete historical API/ABI migration or source-repository retirement.

## Exact-head sanitizer evidence

The existing `Canonical Full Suite` GCC and Clang sanitizer jobs build the real
core aggregate and execute its nonempty `core` CTest selection. They also require
five successful consecutive runs of `hepta_research_native_execution_tests`;
a missing test or any failed repetition fails the job. This reuses the existing
three SIGKILL/re-exec scenarios rather than adding a mock acceptance executable.
The test's per-invocation timeout and unprivileged-user requirement are unchanged.

Each job records the checked-out commit and tree, actual CTest inventory, JUnit
results, separate core/recovery CTest logs and temporary test diagnostics. The
artifact is identified by compiler build, source SHA, run and attempt. Source
identity and tracked-file cleanliness are rechecked after execution. Uploading
logs with `always()` preserves failures; neither an uploaded artifact nor a
queued workflow establishes test success. The existing installed-process,
SDK-package, ownership and broker-qualification checks remain separate claims.
