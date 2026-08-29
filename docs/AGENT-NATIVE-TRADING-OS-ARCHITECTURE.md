# HeptaTrader Agent-Native Trading OS Architecture

Status: canonical architecture.

This document is the source of truth for HeptaTrader's Agent-native product
shape. Older OpenClaw signal, JSONL bridge, desk-loop, and shadow-pipeline
documents describe historical compatibility paths and are not authoritative
for new runtime development.

Round37 workspace layering, compatibility retirement, source packaging, and
quality budgets are defined in `docs/archive/ROUND37-CONSOLIDATION.md`.

## Product Boundary

HeptaTrader is a local trading operating system. Codex, OpenClaw, and other AI
agents use OS-exposed tools; they do not own broker sessions, risk policy,
order IDs, reconciliation, or durable execution state.

```text
Codex / OpenClaw / local Agent
  -> heptactl / SDK / MCP adapter
  -> Unix Tool Gateway
  -> Session Supervisor and Policy
  -> Execution Service
  -> Simulator / IB / CTP / XT venue adapter
```

The Agent-facing adapters are thin translations over one versioned local tool
protocol. They must not introduce another order path.

## Native Agent Entry Point

`heptactl` is the canonical local command surface. It never talks to a broker
adapter directly: every command is encoded with the versioned typed protocol
and sent to the configured Unix Tool Gateway.

```text
heptactl --socket "$HEPTA_TOOL_SOCKET" --token-file /run/credentials/hepta-session tools list
heptactl --socket "$HEPTA_TOOL_SOCKET" --token-file /run/credentials/hepta-session tools describe market.get_quote
heptactl --socket "$HEPTA_TOOL_SOCKET" --token-file /run/credentials/hepta-session call market.get_quote instrument=EUR.USD
heptactl --socket "$HEPTA_TOOL_SOCKET" --token-file /run/credentials/hepta-session wait after_sequence=42 timeout_ms=30000
heptactl --socket "$HEPTA_TOOL_SOCKET" --token-file /run/credentials/hepta-session cancel heptactl-previous-call
```

`system.tools.list` and `system.tools.describe` return `hepta.agent-tools`
protocol version `1`, discovery schema version `2`, and only descriptors visible to the
server-bound session. Thin Codex, OpenClaw, SDK, and MCP adapters must invoke
this same gateway contract rather than introduce another execution path.

Every new request advertises `protocol_min_version` and
`protocol_max_version`; the server rejects a range that does not include v1.
Legacy `HTT1` requests without those fields remain v1-compatible. Discovery
returns a SHA-256 catalog hash and one SHA-256 hash per visible tool schema.
MCP and the native C++ client parse every descriptor field, reject duplicate
names/keys, and recompute every descriptor hash plus the catalog hash. A native
`describe` is accepted only after `list` established the same catalog in that
client session, and its requested target plus descriptor hash must exactly
match the corresponding entry in that list snapshot; `heptactl tools describe`
performs the list automatically.
Both fail closed before exposing a discovered tool. Clients send the bound schema hash
on calls; a mismatch is rejected before handler or execution dispatch with
`SCHEMA_HASH_MISMATCH`.

The stable `heptactl` exit contract is `0` success, `2` usage/credential,
`3` permission, `4` transport/protocol, `5` invalid tool, `6` rejected,
`7` duplicate, `8` uncertain, `9` server error, and `10` invalid response.

`adapters/mcp/hepta_mcp_server.py` is the shared MCP stdio adapter for Codex,
OpenClaw, and other MCP clients. It is a persistent native Unix-socket client,
not a per-call `heptactl` subprocess. The installed launcher resolves the
root-owned trust-domain record for its effective UID at
`/etc/heptatrader/trust-domains/uid-<uid>.json`, then fixes the domain-specific
tool socket, token path and expected UID before `execve`. Missing, unsafe or
mismatched records fail closed. The legacy UID/GID 2004 path is available only
when an operator explicitly enables single-domain compatibility; the packaged
plugin does not enable it. Session secrets are never placed on argv.

## Trust and Process Model

1. The Agent process is unprivileged and has no broker credential.
2. The Session Supervisor provisions short-lived, capability-scoped sessions.
3. The Tool Gateway authenticates every request and re-authenticates queued
   mutations immediately before execution.
4. The Execution Service is the sole order authority. It journals before send,
   owns idempotency and fencing, and reconciles against authoritative venue
   state.
5. Venue adapters translate commands and events; they do not make policy.

The historical `HeptaTrader` composition remains a legacy integration/research
component. The canonical Agent OS instead runs the independent
`hepta-tool-gatewayd` and has no local Execution fallback. Whenever
`HEPTA_TOOL_ALLOW_TRADE=1`, startup requires a valid remote Simulator or PAPER
Execution Service configuration. The fixed four-identity layout is retained
only for an explicitly selected single-domain compatibility deployment.
Every normal trust domain instead owns distinct
`hepta-gw-<domain>`, `hepta-agent-<domain>`, and
`hepta-exec-<domain>` UID/GID pairs. Its Gateway uses only the private
`hepta-gw-<domain>` primary group to connect to that domain's Execution
socket. The fixed `hepta-gateway` identity is not shared with templated
domains.

The Tool Gateway depends only on the narrow `ExecutionAuthority` interface.
The concrete `ExecutionCoordinator`, OMS journal, idempotency and order-owner
projections, and venue callbacks live behind a separate local Unix execution
service. Its typed protocol is versioned, length-bounded, timeout-bounded, and
authenticated with `SO_PEERCRED`; it carries server-derived Agent/session/
account/request context. `HEX v6` deliberately omits Agent-side decision-lease
credentials. After peer, daemon-identity, and readiness checks, the Execution
Service acquires or renews its own per-domain/account/instrument lease and
injects that credential only into the internal coordinator call. Broker
credentials never cross this boundary. New mutations additionally require a
maximum-five-second, single-use preview permit issued by that same Execution
Service from current authoritative quote/risk state. The opaque permit is
bound to the full normalized owner/order intent and daemon incarnation; the
Gateway can relay it but cannot issue, validate, or replay it. The preview
request ID is deliberately excluded. Instead, preview also returns an
Execution-issued future mutation `command_id`; the permit record requires that
exact value as place `toolCallId`, and an uncertain retry reuses it unchanged.
That place ID remains the durable mutation idempotency key. Outstanding permits are globally and per
Agent/session owner bounded; re-previewing the exact owner/intent revokes its
prior permit instead of growing the store.

If a place already has an exact durable `Accepted` or `Uncertain` record, the
same owner/intent/command ID may bypass an already consumed permit solely to
resolve or replay that durable mutation. Accepted replay returns a successful
duplicate; uncertain replay follows the idempotent send ledger and cannot
create a second venue send. A rejected record, changed fingerprint, or new
command ID never receives this exception and must obtain a fresh preview.

The first certified process split is Simulator-only. A real `fork+exec`
service owns `ExecutionCoordinator + OmsJournal + DeterministicExecutionVenue`
while the Tool Gateway uses `UnixExecutionServiceClient`. Crash/restart tests
must prove durable replay, duplicate suppression without a second venue send,
stale fencing rejection, authoritative owner reconciliation, and monotonic
Simulator order IDs before PAPER certification. IPC transport failure is
always `UNCERTAIN`; an Agent must not retry with a new command ID.

## Round38 Product and Trust-Domain Boundary

The Agent source policy treats `systemd/` as an exact file allowlist. Legacy
`hepta-trader.service`, every `hepta-openclaw-*` unit, and future ibgateway or
scalping units are not inherited merely because they are committed below that
directory. The tracked legacy units are additionally forbidden fixtures.
`hepta-trader.service` remains legacy lineage and is not an Agent OS source or
runtime component.

The default UID-2004 layout is an explicitly selected compatibility trust
domain: it may host one Agent or a set of mutually trusting processes only.
Untrusted Agents use root-owned
`/etc/heptatrader/trust-domains/<domain>.json` configuration plus the
`hepta-tool-gateway@.service`/socket templates, and must receive distinct UIDs,
GIDs, token directories, supervisor sockets, and Tool Gateway socket paths.
The launcher and root-owned WATCH custodian (including its private low-level
bootstrap invocation) load the same strict runtime record; neither may silently
fall back to UID 2004. The versioned policy and two-domain fixture
validate the Agent, Gateway, and Execution identity triples, isolated Tool,
supervisor and Execution sockets, Gateway state, and lease-credential paths
without granting PAPER, LIVE, or broker authority. Templated Gateways run as
`hepta-gw-<domain>` with only their domain-specific primary group and no
supplementary groups. The domain Execution socket uses that private Gateway
group as its connect boundary. Execution must bind its configured Gateway UID
and Agent ID to that same domain record. One Execution authority serves
exactly one domain; multiple untrusted domains require separate authority
processes and sockets. A future central multi-domain authority would require
an Execution-verified MAC binding `{domain, account, Gateway UID}` and is not
represented by the current contract. The fixed `hepta-gateway` group remains
only for the explicitly selected single-domain compatibility layout.

`scripts/check_hepta_agent_trust_domains.py` is read-only by default. Its
explicit `--write-staging-root` mode accepts only an operator-selected,
absolute, empty, non-symlink directory and emits declarative runtime JSON,
Gateway/Execution environment fragments, sysusers input, and a non-authorizing
manifest. It never applies sysusers, creates a credential, enables a unit, or
starts a service. `--check-staging-root` verifies the exact staged content and
modes before any separately reviewed rootful provisioning step; unexpected
directories, symlinks, FIFOs, sockets, devices, or other non-regular artifacts
fail closed.

Each domain is materialized twice as independent configuration files:
`<domain>.json` is the root-bootstrap profile and must be a
`root:root`, mode-`0600`, single-link regular file.
`uid-<agent-uid>.json` is the Agent MCP-launcher profile and must be a
separate `root:<domain-agent-group>`, mode-`0640`, single-link regular file.
Both contain the same canonical record, but their ownership profiles and
paths are intentionally different; symlink and hard-link aliases fail closed.
Staging also emits `<domain>.agent-host.conf`, an inert per-domain Agent-host
drop-in fragment naming the exact Agent UID-backed account and UID config
path. The same fragment requires and orders after
`hepta-broker-egress-policy.service`, removes all capabilities, forbids
namespace creation, and retains only `AF_UNIX`, `AF_INET`, and `AF_INET6` for
typed tools and reviewed model egress. The operator must attach the complete
fragment—not only its identity lines—to the intended external Agent host unit
explicitly.

Production session construction recognizes only WATCH and PAPER. The retained
`LIVE_CAPPED` and `LIVE_REDUCE_ONLY` core labels exist for legacy
characterization, but the static product gate rejects either label in the
Gateway runtime/session policy or shipped profiles. The root-owned WATCH
custodian accepts no PAPER or LIVE lifecycle and records both authorities as
false in every transaction and receipt. Its low-level bootstrap remains an
internal implementation detail rather than a campaign API.

`hepta-execution-gateway-paper.env.example` is now a complete routing and
session-scope staging profile for the exact
`/etc/heptatrader/hepta-tool-gateway.env` path read by the Gateway unit. As
shipped it fixes `HEPTA_TOOL_ALLOW_TRADE=0` and
`HEPTA_TOOL_SESSION_TEMPLATES=watch`; therefore it cannot provision a PAPER
session. Enabling mutations or the PAPER template remains a separate,
externally certified deployment action and still requires the Execution
daemon authorization credential, engaged kill-switch review, and all PAPER
gates. The profile contains no credential or authorization receipt.

## Simulator Execution Daemon

`hepta-executiond` is the first formal out-of-process runtime. It is disabled
by default, accepts no command-line configuration or credentials, and rejects
every mode except `SIMULATOR`. Its runtime composition owns the Simulator
venue, execution coordinator, OMS journal, mutation fence, state-directory
lock, Unix execution/control server, and owner-scoped execution event hub.

The canonical deployment is
`hepta-execution-simulator.socket`,
`hepta-execution-events-simulator.socket`, and their shared `.service`. It
requires exactly two systemd-activated descriptors named `execution` and
`events`, an exact Tool Gateway UID, private `0700` state, a `0600` journal,
exact `0400` single-link credential files, and distinct
`hepta-gateway`/`hepta-exec` identities. The service is restricted to
`AF_UNIX`, has no network namespace access, contains no broker adapter or
broker credential, and has no `[Install]` section. Deployment must therefore
remain an explicit certification action rather than an automatically enabled
service.

Both execution IPC peers validate `SO_PEERCRED`. Frames and responses are
bounded, all connect/read/write work shares one absolute deadline, response
command IDs must match the request, and the client never retries a mutation.
The activated descriptor is owned and closed exactly once and its pathname is
never unlinked by the daemon. The non-activated test path uses a lifetime
lockfile plus inode-checked cleanup so a second daemon cannot replace a live
socket.

The versioned control operations expose owner-scoped command status,
session-owner fence/release, and service-owned authoritative reconciliation.
The reconcile request contains no active-order list or completeness flag: the
Execution Service reads its own venue state, so the Gateway cannot upload a
forged complete snapshot. Command-status lookups are keyed by the original
Agent/session/command tuple and continue to report durable `UNCERTAIN` after a
restart.

Long-poll event reads use the separate `events` descriptor and a bounded fixed
worker pool. `HEV2` has one read-only identity query; every wait carries the
complete `{serviceEpoch, serviceFencingGeneration}` pair also used by `HEX v6`.
Responses carry that pair plus the execution stream epoch, monotonic sequence,
bounded replay cursor, dropped-through watermark, and explicit
`EXECUTION_EVENT_GAP`/identity-mismatch states. Both peers validate
`SO_PEERCRED`, and response events must match the requested owner. The server
compares the pair before checking readiness or calling the event source, so a
stale activated-socket backlog cannot consume an event. The Gateway-side
`ExecutionEventRelay` republishes into its local owner-scoped hub while
preserving upstream identity/epoch/sequence provenance. An identity change or
gap emits one local resync-required control event; it never fabricates
authoritative venue state.

The OMS v4 journal persists `execution_domain`, operation-specific canonical
SHA-256 request hashes, a service-owned venue correlation, and stable unique
lifecycle event IDs. The correlation is a second SHA-256 binding
`agent/session/command/request_hash`; identical payloads under distinct command
IDs therefore remain distinct venue mutations. Reusing one
command ID with a changed operation or payload is rejected with
`IDEMPOTENCY_KEY_CONFLICT`; an exact retry returns the original terminal or
`UNCERTAIN` result. A complete service-owned correlation snapshot may resolve
a replayed place intent as confirmed or not found. That decision is written as
the synchronous critical `execution_command_resolved` event before the
recovery mutation block is cleared, and replay restores the same result and
owner. Incomplete snapshots never resolve uncertainty. Projection failure and
authoritative projection resolution remain separate synchronous critical
events, so a restart preserves every fail-closed block until durable
resolution.

The journal uses one pinned `O_APPEND|O_NOFOLLOW` descriptor, write-all
semantics, and `fdatasync` for synchronous critical records. Every append
validates the private mode-0600 regular-file pathname against the pinned
descriptor before and after the write. A missing, replaced, renamed, or
symlinked path, or any write/sync failure, poisons the writer; a malformed or
truncated record makes replay fail closed. The synchronous critical
`place_send_attempt` record is written after durable intent and before venue
I/O. Its timestamps are replayed into the rolling PAPER rate budget, so a
rejected dispatch or a send-before-receipt crash is conservatively charged and
an exact duplicate is not charged twice.

The Simulator process suite kills the service at four deterministic windows:
before authority dispatch, after durable intent but before venue send, after
venue send but before its receipt, and after the receipt but before the IPC
response. A separate fsync-backed venue ledger proves the expected send count.
The same client object and command ID are reused across restart; the suite also
covers unavailable transport, response-ID mismatch, stale fencing, changed
payload/operation conflicts, recovered-owner cancellation, monotonic order
IDs, and refusal by a competing daemon to take the active socket.

`scripts/run_execution_gateway_soak.py` repeats 9 suites. Four exercise
process or runtime-composition boundaries: the Simulator fork/exec crash suite,
Execution service runtime composition, Gateway relay/reconnect E2E, and the
fake-IB PAPER fork/exec crash suite. Three are in-process fault matrices for the
event feed, IB authoritative queue, and PAPER reconciliation. The remaining two
cover Tool Gateway runtime composition and the fake-broker Agent-tool path.
Each round verifies one exact machine-evidence line per suite, including the
final authoritative quote-to-broker-contract fail-closed binding, observes the
required process trees in `/proc`, proves the independent fsync venue/broker
ledgers, and finishes without orphan descendants or excess runner/process-tree
FD, thread, or RSS growth.

The mode-0600 Version 11 report (`hepta.execution-gateway-soak.v11`) hashes a
bounded complete output capture rather than trusting only its tail, rejects
missing, duplicate, mismatched, or unexpected evidence fields, uses a fixed
minimal environment, and executes each previously hashed binary through a
pinned descriptor. It records source-relative paths plus a redacted diagnostic
tail. Before and after all rounds it requires the same repository or no-Git
source identity, runner, CMake cache/configuration, compile-command snapshot
when enabled, security-relevant path/size/mode/SHA-256 manifest, and binary
snapshots. This distinguishes dirty or replaced inputs. It deliberately does
not claim that a binary cryptographically embeds that source manifest; the
release gate therefore still requires a fresh build immediately before the
soak and retains the build/configuration evidence. The CTest entry point is
parameterized by `HEPTA_SOAK_PROFILE`: PRs use the two-round `pr-smoke`
profile, while release and nightly lanes use the same entry point with the
full eight-round `release`/`nightly` profile. Every lane writes the same JSON
schema and path, so the phase gate consumes one existing receipt rather than
launching a second soak. Longer offline runs use the same pinned input set and
report schema.

## Production Cutover Boundary

Simulator process certification does not authorize PAPER or LIVE and does not
yet authorize replacing the real-venue in-process coordinator. The Tool
Gateway now has one default-hard-off `ExecutionGatewayRuntimeComposition`.
When explicitly configured for `SIMULATOR` or `PAPER`, Agent place/cancel calls,
session revoke fence/release, command controls, and `events.wait` all reuse the
same remote mutation/control socket plus the independent event feed. Flatten is
published only when a concrete Execution-owned atomic reduce-only handler is
installed; the canonical Simulator and IB compositions currently hide it. Local
session/health events remain in the owner-scoped hub and are merged with relayed
execution events. Before any mutation, control, or event wait, the Gateway
requires the mutation and event sockets to report the same complete daemon
identity pair. A mismatch rejects the call without authority dispatch, event
source read, cursor advance, local publish, or automatic same-call retry.
Incomplete, contradictory, relative-path, same-socket, or
missing-UID configuration fails startup; disabled mode rejects stray remote
settings instead of silently ignoring them. The Gateway enforces a fixed
context boundary before transport: Simulator requires `SIMULATOR` plus a
`SIM:` execution domain, while PAPER requires `IB` plus either the retained
single-domain compatibility value `PAPER` or a templated
`PAPER:<agent-id>` value whose suffix exactly matches the configured Agent ID.
A context mismatch is rejected locally and never reaches either Execution
Service.

The current real-venue session context is intentionally not rewritten to
`SIMULATOR/SIM`. Therefore enabling the Simulator client in an IB/CTP/XT
process still fails the daemon's fixed-context policy rather than redirecting
a real strategy into simulated execution. This wiring is a process-boundary
integration seam, not a production venue cutover.

The Simulator daemon now provides stable venue correlation and durable
resolution for replayed uncertain place intents. The IB adapter has the first
offline-certified real-venue correlation seam: the service-owned canonical
`hepta-v1-sha256:` identifier is reversibly encoded into a 45-character `H1`
`Order.orderRef`, transmitted by the production IB wrapper, and reconstructed
from `openOrder` callbacks without process-local lookup state. A
`reqOpenOrders` generation becomes complete only at `openOrderEnd`; disconnect,
queue overflow, malformed `H1` data, duplicate correlation-to-order mappings,
or a rejected refresh invalidates the entire snapshot. Non-correlated callers
cannot supply `orderRef`, so Agent input cannot impersonate this namespace.
These contracts are tested against a fake IB wrapper only and do not authorize
an IB connection.

The independent IB PAPER profile is default-hard-off and is not a new mode of
the Simulator daemon. Its offline-certified policy core accepts only the
literal `PAPER` mode, a `DU` account, loopback TWS/IB Gateway, paper ports 7497
or 4002, and an exact mode-0400 `PAPER-V3:sha256:<64hex>` authorization
credential. The canonical credential hash binds the account, loopback endpoint,
client ID, fixed `/run/hepta/ib-paper-control` directory, `CASH,STK` and `MKT`
allowlists, and every configured risk cap; legacy PAPER and V2 credentials are
rejected. The code-level maxima are 25,000 units per order, 250,000 notional,
30 send attempts per rolling minute, 50 active orders, 100,000 gross absolute
position, and client ID 65,535. The DU account has a bounded numeric suffix,
and the first PAPER profile accepts only `STK`/`CASH` market orders; derivatives
and unknown security/order types fail closed. Any incomplete authoritative risk
snapshot rejects risk-increasing mutations. The kill switch blocks place but
intentionally preserves owner-fenced cancel as a risk exit. Production now pins
the root-owned, `root:hepta-ib-exec` mode-0750 control directory before adapter
initialization, requires a root-owned mode-0440 single-link marker, revalidates
the configured pathname and pinned device/inode on every check, and latches
directory replacement as uncertain until restart. Missing is disarmed only
after stable directory identity checks; malformed metadata and filesystem
errors fail closed. Durable reconcile likewise refuses incomplete IB evidence
and never accepts a snapshot uploaded by the Gateway.

The first offline `hepta-ib-executiond` composition is now a separate binary
closure. It owns activated mutation/event descriptors, a private state lock,
authorization and fencing credentials, synchronous-critical OMS, the IB
adapter/event pump, coordinator, PAPER policy, event hub, and both Unix
servers. The process refuses inherited adapter tuning/trace/log environment,
uses a state-directory observability path, and waits for four complete,
nonzero-generation snapshots tied to one connection epoch before exposing
mutation: target account, positions, account-wide active/open-order
correlations, and terminal completed/execution correlations. The account-wide
open-order request uses `reqAllOpenOrders`. Its complete active projection is
then incrementally updated by correlated sends, exact-account `openOrder`
callbacks, and terminal status, so the active-order cap cannot use a stale
view and a restarted adapter can restore broker lifecycle for cancel. A
connection close or authoritative event-queue overflow is fatal and makes the
daemon exit nonzero rather than remaining indefinitely inert.

IB evidence is positive-only: an order can be sent and immediately become
terminal before restart, and absence from active or terminal snapshots is
never a negative delivery oracle. Durable resolution merges positively present
active-open-order correlations with positively present completed-order and
execution correlations; a missing correlation remains `UNCERTAIN`. Because
`completedOrdersEnd` has no request ID, only one terminal request is permitted
per connection epoch and failure or timeout requires reconnect before retry.
Execution items and their End must match the active request ID. Exact account,
canonical `H1`, bidirectional correlation/order-ID uniqueness, connection
epoch, and queue capacity all fail closed. Simulator retains its stronger
deterministic not-found resolution. Dynamic risk and kill-switch checks occur
only after coordinator idempotency precheck, preserving
Duplicate/Uncertain/conflict results across degraded state. A second check runs
after the durable send-attempt marker and immediately before venue I/O, closing
the policy-to-broker race without bypassing the journal sequence.

The offline fake-IB boundary now proves a real fork/exec composition child with
an injected fake wrapper, state-lock competition, restart replay, four exact
place-order SIGKILL windows, and an independent fsync broker ledger showing
same-command retry never sends twice. Cancel now has its own synchronous
`cancel_send_attempt` and `cancel_command_resolved` records. Recovery uses only
positive broker evidence: Open remains uncertain, Cancelled/ApiCancelled
confirms success, and Filled/execution or another terminal state rejects the
cancel without resending it. Absence from active and terminal snapshots is not
negative evidence. Five process-level SIGKILL windows cover pre-dispatch,
intent/send-attempt before venue I/O, broker Filled, broker Cancelled before
receipt, and receipt before IPC response; exact replay never emits a second
broker cancel. This does not exercise a real IBAPI
connection or production systemd activation. The combined soak runner includes
that boundary.

`HEX v6` binds every mutation and control request to one immutable daemon
identity pair: a random 128-bit service epoch plus the fencing generation read
from the pinned HFC credential. The latter is deliberately distinct from an
Agent decision-lease generation. Agent lease token/generation fields are no
longer serialized on this IPC boundary. The service maintains independent
per-instrument leases, renews them for the same Agent/session owner, rejects a
competing owner, and revokes all matching leases when the session owner is
fenced. A client first performs the only pair-less,
read-only identity query, then pins both values into each request. The daemon
checks the pair before authority dispatch and returns an explicit mismatch
without executing stale work. The client compare-and-invalidates only the pair
observed by that call, so a delayed invalidation cannot erase a newer cache; it
never automatically resends a mutation. An explicit retry reuses the same
command ID and negotiates the new pair. Process tests preserve a manager-owned
activated socket, queue a request carrying the old identity while no service is
running, start a new server on a duplicated manager FD, and prove the queued
mutation is rejected with zero authority calls. Server shutdown now uses
bounded polling and closes only its owned descriptor, rather than calling
`shutdown()` on the shared socket object and damaging the systemd manager FD.

This still does not authorize PAPER cutover. The real IBAPI build closure is
explicit and verified, and the repository now carries mutually exclusive,
no-`[Install]` Simulator/IB PAPER socket-activation units plus a default-engaged
tmpfiles control-plane artifact. The six-suite fault matrix now certifies
reconnect, backpressure, event-gap provenance, old mutation and event identity
backlog rejection, cross-socket identity agreement, identity-refresh behavior,
concurrent pair pinning, same-owner cursor serialization, and authoritative
resync latching through exact machine evidence on every soak round. The
Gateway dispatches mutation/control with the exact pair already validated on
both sockets; it never re-reads a mutation-only cache between validation and
dispatch. An event identity mismatch has no source-read, cursor, or local-publish
side effect. Once a new identity or gap engages the resync latch, timeouts and
ordinary remote events cannot clear it; only an explicit service-owned
authoritative reconcile that is accepted, reports mutation unblocked, and
matches the current identity may acknowledge it. The same matrix retains IB
correlation conflicts and fail-closed reconcile coverage. Version 5 adds the
exact Gateway markers `old_event_identity_backlog_rejected`,
`dual_socket_identity_mismatch_rejected`, and
`event_restart_identity_refresh`, `validated_pair_dispatch_pinned`,
`owner_wait_identity_serialized`, and `resync_control_exact_match`, plus the event-fault markers
`stale_event_identity_no_read` and `identity_reject_no_cursor_publish`.
PAPER additionally performs a final kill-switch
observation under the adapter send mutex after preflight/risk evaluation and
immediately before the IB API call; this removes the policy-to-adapter and
adapter-lock-wait windows while retaining the earlier durable coordinator
check. Broker-capable builds also reject root service execution and reject a
numeric Gateway UID equal to the daemon UID. A per-domain provisioned-host gate
must prove the number resolves to its exact `hepta-gw-<domain>` account and
that the configured Gateway Agent ID comes from the same domain contract. The
legacy `hepta-gateway` account is accepted only by the explicit single-domain
compatibility gate.
Authorization and fence credential files are opened with `O_NOFOLLOW` and must
be single-link regular files with exact mode `0400`; their directory must be a
non-symlink, non-group/world-writable path disjoint from state and control.
The provisioned-host gate must additionally prove the systemd credential mount
is read-only to the service identity.
Remaining gates are real systemd activation,
credential, inode-permission, stop-cleanup, and loopback-network integration on
a provisioned host. Only after those gates and explicit operator authorization
may minimal, capped PAPER-only certification begin.

## Passive Execution Deployment Component

Linux builds expose one `hepta-execution-runtime` install component. An
IB-disabled build installs only `hepta-executiond`, the three Simulator units,
the non-secret Simulator environment example, and this architecture document.
An IB-enabled build adds the real `hepta-ib-executiond`, the three IB PAPER
units, its non-secret environment example, and the root-controlled tmpfiles
declaration. The disconnected IB-disabled stub is never installable.
Because the reviewed units use fixed FHS paths, the component accepts only
`cmake --install <build> --prefix /usr --component hepta-execution-runtime`;
any other prefix fails before writing the staging or host tree.

The component is intentionally passive: it installs no `/etc` profile, secret,
credential, user identity, `/run` inode, `/var` state, symlink, or enabled unit.
CI executes the component under a temporary `DESTDIR` and enforces an exact
file and mode allowlist, executable closure, no `[Install]` sections, and the
IB-off/IB-on boundary. This closes only the packaging precondition. It does not
provision `hepta-gateway`, `hepta-exec`, or `hepta-ib-exec`; select the deployed
gateway UID; install credentials; invoke tmpfiles; reload or start systemd; or
certify socket inode ownership, activation FD names, stop cleanup, loopback
isolation, and broker-unreachable behavior. Those checks still require a
rootful disposable provisioned host before any PAPER authorization.

`scripts/check_hepta_execution_provisioned_host.py` is the read-only first half
of that host gate. It uses descriptor-anchored no-follow reads, never reads
credential contents, and fails closed on identity aliases/supplementary groups,
unsafe ancestors, profile UID drift, credential/control inode metadata,
default-disengaged kill switch state, legacy units, or critical canonical-unit
directive drift. It also binds the Simulator environment/fence, the installed
tmpfiles declaration, and both root-owned single-link ELF entry points. Its
CTest coverage uses only a disposable synthetic root. It
does not start systemd or prove activated socket FDs, read-only credential
mounts, stop cleanup, loopback filtering, or broker-unreachable behavior; those
remain the separate disposable-rootful activation gate.

The current round105 line retains an explicit, default-off containerized
effective-systemd rehearsal.
Its outer runner requires a root-owned disposable-host sentinel, a digest-pinned
offline-ready gate base, cgroup v2 with the systemd driver, builtin seccomp, a
named non-unconfined AppArmor profile, networkless image builds and runtime,
read-only rootfs, no user host bind mounts, and exact object-ID/label cleanup.
The `real` variant runs only the production Simulator daemon. All three images
start with the IB-disabled stub at the canonical IB path; the sandbox variant
replaces it with a broker-free probe before any IB unit is started. The formal
IBAPI ELF is descriptor-hashed only by the outer runner and its bytes never
enter the Docker build context or an image. The inner contract binds that outer
formal hash, the real Simulator, the executed replacement, and read-only client-probe hashes; compares mounted credential
bytes to their protected sources without reporting content or hashes; proves
the Simulator consumed the expected HFC generation; and uses benign loopback
sentinels to distinguish working connectivity from systemd IP-policy denial and
to prove zero connections to the configured stub endpoint. No place/cancel API
is present in the client or sandbox probes.

This rehearsal is deliberately not a certification shortcut. Docker shares the
host kernel, and the Version 1 report records that AppArmor policy content is
not attested and that the final native disposable-VM gate is unsatisfied. A
normal workstation without the root-owned sentinel must fail before pulling,
building, or starting a container. `privileged`, `apparmor=unconfined`, host
cgroup/PID/network namespaces, Docker-socket mounts and host `/etc`, `/run` or
`/usr` binds are forbidden. Native disposable-host activation and an explicitly
reviewed platform policy remain required before any separate PAPER request.

## Canonical Tool Surface

The stable product surface is:

- `system.tools.list` and `system.tools.describe`
- `market.get_quote`
- `account.get_summary`
- `portfolio.list_positions`
- `orders.list`
- `risk.get_limits` and `risk.preview_order`
- `trade.place_order` and `trade.cancel_order`; `trade.flatten_position` only
  when an Execution-owned atomic reduce-only handler is installed
- `events.wait`
- `system.get_health` and `system.cancel_request`

Every descriptor has an input schema, result schema, effect classification,
required capability, and bounded timeout. Protocol version, schema hash,
request ID, session generation, deadline, and standard reason code are part of
the transport contract.

## Invariants

- No Agent or compatibility bridge may bypass `ExecutionCoordinator`.
- No risk-increasing mutation runs without a live session and decision lease.
  A lease-exempt cancel is permitted only as a risk exit with live-session,
  exact owner, and service-validated cancellation fencing.
- No broker call occurs before durable intent journaling.
- A command ID is bound to one canonical operation and payload; a changed
  request is an idempotency conflict, never a duplicate.
- Transport ambiguity remains `UNCERTAIN`. Recovery reuses the same command ID
  and must not synthesize a new mutation.
- Revocation and expiry fence queued work before execution.
- Reconciliation is authoritative; replay alone cannot resurrect terminal
  orders or ownership.
- `WATCH` is read-only. `PAPER` is explicit and capped. LIVE remains a separate
  future certification gate.
- Legacy JSONL/OpenClaw bridges are build-time opt-in compatibility code only.

## Repository Boundaries

- `HeptaTrade/tool_host`: local transport, sessions, supervisor, policy wiring.
- `HeptaTrade/tools`: canonical tool descriptors and invocation registry.
- `HeptaTrade/execution`: venue-neutral execution authority.
- `HeptaTrade/state`: authoritative state, recovery, and projection.
- `HeptaTrade/events`: owner-scoped events and health publication.
- `HeptaTrade/simulator`: deterministic offline venue.
- `HeptaTrade/agent`: decision-lease fencing.
- `tests`: deterministic unit, process-boundary, recovery, and Simulator E2E.
- `scripts`: maintained release/evidence tooling only; generated investigations
  belong in archived artifacts rather than the product source tree.

## Convergence Sequence

1. Preserve and classify the current incremental tree.
2. Extract runtime composition from `HeptaDemoStrategyTrader.cpp` without
   changing behavior.
3. Ship `heptactl` with discovery, describe, call, wait, and cancel commands.
4. Run those tools behind the standalone `hepta-tool-gatewayd`; use
   `hepta-sessionctl` over the OS-only supervisor socket for provision, renew,
   rotate, and revoke. This process split is implemented. The daemon links no
   broker adapter or strategy and has no local execution fallback. Its first
   standalone read model exposes discovery, bounded health, owner-scoped
   events, and Execution-Service-owned market/account/portfolio/order/risk
   reads over the authenticated Unix protocol.
5. Add thin Codex/OpenClaw/MCP adapters over the same protocol.
6. Split a Simulator-only execution daemon with systemd socket activation,
   private state and credentials, distinct OS identities, and deterministic
   in-flight crash certification. This stage is implemented.
7. Add remote session fencing, owner-scoped command status, service-owned
   reconciliation, and a separate bounded execution event feed with Gateway
   relay. The Simulator protocol, components, stable venue correlation, and
   durable uncertain-place resolution are implemented. Default-hard-off
   Gateway wiring and same-object reconnect/gap tests are implemented;
   repeatable resource-audited offline soak is implemented. The IB adapter's
   active and terminal correlation contracts, default-hard-off PAPER policy,
   cap-bound authorization, hard limits, isolated daemon composition, and
   place-order and cancel crash/replay boundaries are implemented and fake-IB
   certified. Explicit Gateway PAPER wiring and fixed-context isolation are
   implemented. Unified `HEX v6`/`HEV2` daemon identity-pair pinning,
   dual-socket agreement, compare-and-invalidate refresh, and old
   activated-socket mutation and event backlog rejection are implemented.
   `HEX v6` also removes Agent lease credentials from the wire, grants the
   canonical per-instrument lease inside the Execution Service, and forbids
   local authority fallback whenever Agent mutation tools are enabled.
   Root-owned pinned kill-switch isolation,
   real-IB build closure, the conditional passive install-tree contract,
   reviewed no-`[Install]` systemd units, and the six-suite
   reconnect/backpressure/event-gap/reconcile fault-matrix soak are implemented.
   Static provisioned-host closure and a default-off, broker-free containerized
   effective-systemd rehearsal harness are implemented. The default-off native
   disposable-VM per-variant runner and three-VM evidence aggregator are also
   implemented with exact image/provisioning/platform-policy binding and
   loopback-only network requirements. Deterministic broker-free rootfs bundle
   generation and independent archive verification now close file, directory,
   ownership, mode, variant-binary, and manifest provenance before an image
   builder is allowed to consume a payload. The bound image-manifest digest is
   intentionally the hash of the relevant immutable-file manifest rather than
   a self-referential whole-image hash. Reviewed base-image assembly, three
   independent disposable VM executions, platform-policy attestation, and
   PAPER certification still remain.
8. Run explicit, capped PAPER-only venue certification. LIVE is out of scope
   until a separate approval gate exists.
# Round31 Core Boundary

Agent-facing and Execution Service contracts use venue-neutral `InstrumentRef`,
`OrderIntent`, `PlaceOrderCommand`, and `CancelOrderCommand` types. The IB
adapter retains transitional source aliases, but IB SDK/native objects remain
below the adapter boundary. Simulator and IB are the only certified venues;
CTP and XT remain disabled experimental components.

The standalone product links three shared static cores instead of recompiling
the same implementation into every binary:

- `hepta_execution_core` owns execution fencing, protocol, event, OMS, and Unix
  service primitives;
- `hepta_trading_tool_core` owns the Agent-visible tool registry and contract
  binding layer;
- `hepta_agent_os_core` owns Gateway/session/tool server composition.

Compatibility names such as `IBContractLite`, `IBOrderLite`,
`IbPlaceOrderCommand`, and `PlaceIbOrder` are migration aliases only. New code
must use the venue-neutral names.

## Round32 Preview, Market Freshness, and Native Client

Risk-increasing `trade.place_order` calls in the canonical PAPER session must
carry a short-lived permit returned by `risk.preview_order`. The Execution
Service, not the Tool Gateway, binds each permit to the Agent/session/account/
domain, daemon incarnation, complete normalized order intent, and an
Execution-issued future mutation `command_id`. The Gateway only serializes
preview against owner fencing and relays the opaque permit plus future command
ID. A permit is consumed before dispatch, expires under both wall and monotonic
deadlines no later than five seconds after preview, and is invalidated by any
owner, order-field, daemon-identity, or command-ID drift. Owner fencing revokes
all outstanding permits for that owner. An exact durable Accepted or Uncertain
retry reuses the same command ID and may bypass permit re-consumption only to
resolve the pre-existing durable mutation; Accepted returns a successful
duplicate and neither path can cause a second venue send. The Execution Service
still performs its independent authoritative risk check; a permit never
delegates broker or risk authority to the Agent.

Authoritative quote results carry a venue-owned `subscription_id`, explicit
subscription state, observation time, staleness deadline, and stale flag.
Simulator order dispatch rejects stale quotes. The IB PAPER daemon owns its
reviewed quote contract set and `ReqMktData`/`CancelMktData` lifecycle; unknown,
incomplete, or stale subscriptions fail closed. The Agent cannot supply a raw
broker contract, request ID, or market mark.

`hepta_native_tool_client` is the shared C++ Unix client used by `heptactl` and
available as the `hepta-agent-os-sdk` install component. It owns bounded framed
I/O, response-envelope validation, and strict regular non-symlink session-token
loading. Its wire validation is authority-free and compiled into the archive;
the SDK does not hide a link dependency on Execution or trading-registry
libraries. The component installs a relocatable
`HeptaTrader::NativeToolClient` CMake package, and the release gate configures,
links, and runs a consumer using only the staged headers, archive, and package
metadata. MCP remains a thin adapter over the same versioned schema rather than
a second trading protocol.

## Round33 Repository and Operations Convergence

The default Agent OS build now sets `HEPTA_BUILD_LEGACY_MONOLITH=OFF`. The
historical `HeptaTrader` binary combines legacy strategies, CTP/XT/IB wiring,
and process-local authority, so it is not part of the Agent OS runtime or PAPER
certification closure. It remains available only through an explicit
`-DHEPTA_BUILD_LEGACY_MONOLITH=ON` research build. The deprecated 0DTE bridge
cannot be enabled unless that legacy target is also explicitly enabled.

Repository operations use the declarative `ops/hepta-ops-v1.json` registry and
`scripts/hepta_ops.py` CLI. Generated compatibility shims are deterministic,
use the reviewed absolute `/bin/sh` and `/usr/bin/python3` interpreters,
emit deprecation telemetry, and cannot authorize PAPER or LIVE activity.
Optional telemetry is restricted to the fixed
`compat-wrapper-usage.jsonl` name below a caller-owned mode-0700 directory; all
components and the mode-0600 file are opened through no-follow directory
descriptors. Active jobs are restricted to reviewed repository-local Python
entry points, scrub Python injection variables and `PATH`, close inherited
descriptors, and install an exec-persistent Linux seccomp filter that denies
network syscalls.
Legacy research wrappers are inventoried as `compat` or `archive`; Round33 does
not delete or overwrite them.

Large runtime evidence remains outside the source closure. The retention policy
in `policies/heptatrader-evidence-retention-v1.json` classifies certification,
forensic, latest, and ephemeral payloads with explicit priority and Git-index
eligibility. Evidence index v2 streams payload hashes, fixes output below the
dedicated `evidence-indexes/` tree, rejects symlinks and unsafe modes, and
requires local payload verification. Git may retain only the small
content-addressed index. Index creation anchors every output directory through
no-follow descriptors, requires caller ownership with no group/world write
bits, and publishes a private file by fsync plus same-directory atomic rename.
Its retention clock and upload status remain
`pending-external`; metadata-only verification and payload removal are disabled
until an independently verified ingestion receipt exists.

CTP remains disabled experimental. Its byte-identical headers have one
canonical copy under `third_party/ctp/6.7.7/include`; the historical 32-bit,
64-bit, and Linux include directories are forwarding compatibility paths.
Platform binaries remain separate and are pinned by
`third_party/ctp/6.7.7/manifest-v1.json`. The legacy import lacks a reviewed
origin URL and redistributable license file, so distribution authorization
stays false. The observed package version is 6.7.7, proven by all three platform
version files and pinned payload hashes. The proprietary headers and binaries
are excluded from the distributable Agent OS clean-source archive by a
vendor-root deny-by-default rule; only the exact README and manifest allowlist
remains. Vendor reads walk every path component with no-follow descriptors, and
header convergence performs a complete preflight before any compatibility file
is replaced. An explicit legacy-monolith build therefore requires a separately
reviewed local vendor overlay.

## Round34 Host Identity, Supply Chain, and Runtime Boundary

The Agent-facing socket is `/run/hepta-agent/tools.sock`. Its parent is
root-owned mode `0711`; the socket is `hepta-agent:hepta-agent` mode `0600`.
The supervisor socket remains below the Gateway-owned mode `0700` runtime
directory. The root-owned `hepta-shadow-watch-custodian` transaction is the
only production lifecycle owner for a bounded SHADOW WATCH campaign. It binds
the exact trust-domain configuration,
`/etc/heptatrader/trust-domains/<domain>.shadow-watch.env`, campaign ID,
reader PID/UID/GID/start time/boot ID, generation, expiry, lease-receipt digest,
and root-fence digest in a root-only durable record below
`/var/lib/hepta-shadow-watch-custodian/<domain>`. Campaign, strategy, observer,
collector, and exporter code never invokes `hepta-agent-session-bootstrap`,
`hepta-sessionctl`, or any provision/rotate/close operation.

Provision first publishes `PROVISION_PREPARING`; only then may the custodian
invoke the reviewed low-level WATCH bootstrap. That bootstrap still creates
same-bearer, different-inode Agent delivery and root-fence files, fsyncs them
before supervisor mutation, and publishes the canonical accepted lease
receipt. The custodian validates the exact generation, receipt chain, bearer
equality, independent inodes, ownership, modes, link counts, stable reads,
expiry, and the false PAPER/LIVE/mutation flags before atomically committing
`phase=ACTIVE`. An accepted supervisor result, a token pathname, a live socket,
or a successful read is not independently sufficient. The host bootstrap must
prove the canonical `hepta.shadow-watch-custodian-registration.v1` and the
matching durable `ACTIVE` transaction before starting the custodian
`supervise` unit or allowing a collector or SHADOW controller to consume the
session.

Rotation is part of the same transaction. The custodian durably records
`ROTATION_PREPARING`, invokes the low-level bootstrap itself, reconciles an
ambiguous candidate, and returns to `ACTIVE` only for the exact `N+1` receipt
and fence chain. The old root fence remains recovery authority until the new
generation is proven committed. Campaign code cannot retry or compensate a
low-level call. Any incomplete provision or rotation is resolved and either
committed exactly or authoritatively revoked by the custodian.

### Custodian-supervised SHADOW WATCH collection

After `ACTIVE` is proven, `hepta-shadow-watch-custodian@<domain>.service` is
the primary lifecycle monitor. It continuously binds the reader process
identity and lease expiry; owner death, configuration drift, service stop, or
expiry starts close. The separately reviewed
`hepta-shadow-watch-custodian-reconcile@<domain>.timer` is
`Persistent=true` and exists only as a crash/reboot backstop for an already
durable transaction. It neither provisions nor rotates authority and is not a
collection scheduler. The normal `supervise` and collection path cannot start
before the `ACTIVE` handoff. The reconcile timer is the only pre-`ACTIVE`
exception: after a crash or reboot it may resolve or close an already durable
preparing transaction, but it can never make that transaction usable.

The static, `Persistent=false`
`hepta-shadow-watch-collector@<domain>.timer` may be started only by the
reviewed root host bootstrap for that bounded campaign. Its oneshot service
runs as `hepta-agent-<domain>` with no supplementary groups and with
`PrivateNetwork=yes`, consumes only the custodian-owned WATCH token, and calls
the fixed discovery plus health, account, positions, orders, limits, and quote
read surface. Its catalog rejects every visible preview or `trade.*` tool. The
collector writes a private mode-`0600` normalized snapshot; systemd
`OnSuccess` invokes `hepta-shadow-watch-export@<domain>.service`, which
validates schema, digest, generation receipt, and all no-mutation flags before
publishing a root-to-reader mode-`0440` snapshot and receipts under `/run`.
The reader cannot chmod, replace, or rewrite the export. Campaign code consumes
the validated export only; it does not execute the collector or exporter.

Close is also owned by the durable custodian transaction. It first commits
`CLOSING`, quarantines Agent access, and uses only the root fence to revoke the
transaction's exact generation. A missing fence cannot be treated as success
before the exact receipt expiry; an ambiguous outcome remains pending. Only an
authoritative exact-generation `ACCEPTED`, `ALREADY_ABSENT`, or `EXPIRED`
outcome permits cleanup. The custodian must then prove the Agent token, root
fence, lease receipt, and the entire domain export directory absent, publish
exactly one root-only mode-`0600`
`hepta.shadow-watch-custodian-closure.v1` receipt binding the campaign,
`lease_generation`, `lease_receipt_body_sha256`, `fence_token_sha256`, and
`authoritative_revoke_outcome`, set `local_authority_removed=true` and
`export_evidence_removed=true`, record `paper_authorized=false`,
`live_authorized=false`, `mutation_authorized=false`, and
`direct_broker_access=false`, and remove the active transaction. Service
inactivity, token absence, command success, or elapsed TTL alone is not
closure. The reconcile backstop remains active until the closure receipt and
zero token/fence/export residue are all verified.

Session retirement is a durable two-phase fence. Revoke or expiry first marks
the encrypted supervisor lease `fence_pending` and disables the local session;
the record and contract catalog are removed only after Execution accepts the
owner fence. A failed fence therefore rejects the supervisor operation, keeps a
retryable disabled session, and blocks provision, renew, or rotate for the same
Agent/session owner. The standalone daemon retries pending and expired records
once per second. On restart it fences every pending or expired durable record
before accepting supervisor traffic; an unavailable Execution service prevents
Gateway activation rather than restoring mutation authority. Active WATCH
records are also converted to a durable `session_revoked` fence and removed on
every Gateway process restart, because their runtime bearer/fence may have been
lost across a host reboot. A copied WATCH bearer therefore never regains an
enabled session after restart. Active PAPER records retain their separately
reviewed durable-restart behavior.

### Passive SHADOW host projection

The distributable Agent runtime remains a `/usr`-only component. Its exact
Agent closure contains 94 files, and the combined Agent plus Simulator
Execution runtime contains 102 files; both continue to declare
`host_state_paths_included=false`. The dedicated
`build_hepta_shadow_runtime_archive.py` projection is a narrower host-install
artifact with 95 files: the 94 Agent files plus exactly one host-state member,
`etc/heptatrader/hepta-agent-trust-domain-paper-identities-v1.json`. That member
is copied only from the already verified packaged documentation example and is
bound to mode `0600`, 257 bytes, and SHA-256
`4a94d555cad61a9de67b809cfae301eadd6ebf2511714c93343f10decb34e435`.
No other `/etc`, `/run`, or `/var` archive member is accepted.

The projection install manifest v2 orders that default-deny identity first,
binds every file and the source baseline, and cross-binds the manifest's
installer digest to both the packaged installer record and the executing
installer bytes. The root installer holds the persistent, single-link,
root-owned mode-`0600` lock
`/var/lib/hepta/.shadow-runtime-install.lock` from before its first safety
preflight through receipt publication or failure handling. Existing-target
writes use descriptor-anchored `renameat2(RENAME_EXCHANGE)` compare-and-swap;
create-only publication and conditional cleanup use `RENAME_NOREPLACE` so a
racer is not blindly overwritten or deleted.

The preflight and postflight samples must be exactly equal after canonical
validation, including all nine PAPER unit states and the inactive
Gateway/activation/reconcile/WATCH custodian, collector, and exporter units.
An install is rejected while any of those SHADOW authority units is active. A successful v4 receipt
records both point-in-time samples and the held lock inode evidence but sets
`preflight_continuity_claimed=false`. Normal failures while the named lock
still matches the held inode perform an exact rollback while leaving the
canonical identity at deny-all. If that global binding is lost, the installer
enters terminal compromise handling: it withdraws only its provable receipt
into retained quarantine, reasserts the canonical deny-all identity, performs
no unlocked `/usr` restore or cleanup, and fails. Residue then requires a new
stable-lock recovery transaction and cannot support activation.

Every successful transaction also atomically replaces the fixed root-owned
mode-`0600` current-generation marker
`/var/lib/hepta/shadow-runtime-install-state/current-install-v1.json` while
holding that lock. The marker monotonically binds the current manifest and
receipt file digests, lineage, 95-file path digest, backup root, and all-false
authority boundary. Every invocation must also carry the caller-frozen exact
predecessor generation and pointer-file digest; genesis is the explicit pair
`0`/`absent`. The lock-held observation must match that pair before any
payload, backup, or receipt mutation, and every later generation must retain
the exact same 95-path set. A stale frozen writer therefore cannot rebase
itself over a newer generation, and a later supported install invalidates every
old generation even when old files remain byte-identical. Receipt v4 retains
the exact predecessor generation and predecessor pointer-file digest observed
under that lock; current pointer v1 binds the new receipt digest, so the edge is
gap-free and independently consumable. This still proves only one supported
installation edge, not long-running service or trading continuity.

All supported privileged installers must honor this one persistent lock and
the strict root-owned, non-group/world-writable ancestor contract. A root or
`CAP_DAC_OVERRIDE` actor that bypasses the lock can alter process memory or the
host tree directly and is outside this installer threat model; the userspace
code does not claim an inode-conditional unlink primitive against such a rogue
host-root writer. The integrated profile, activation, and admission consumers
first bind the installer payload to an externally pinned canonical manifest,
then hold the named install lock while checking receipt v4, the fixed current
generation, the complete 95-file closure, and the deny-all identity. They emit
strict consumption-evidence v3, including the exact predecessor edge; profile
and activation nest that exact object
in their receipts, and admission reconsumes it at live and final-publication
windows. This is never continuity, PAPER, or LIVE evidence by itself.

The passive profile transaction treats a WATCH boundary unit as fail-closed
only when its exact systemd state is either `loaded/inactive/dead/no-job` or
`loaded/failed/failed/no-job`. Both states carry no active unit authority; the
transaction records the exact choice before and after and rejects mixed,
active, activating, or queued-job states. It never calls `reset-failed` or
claims that this point-in-time state is activation continuity.

The installed plugin launches only through the non-setuid UID-2004 gate.
Gateway health now distinguishes remote configuration from a live, matching
mutation/event daemon identity pair. Tool descriptors budget the complete
multi-RPC path; a single Execution IPC wait remains bounded to 2.5 seconds.
`trade.flatten_position` stays undiscoverable until a venue composition owns an
atomic reduce-only implementation.

The provisioned-host runtime preflight is a real end-to-end WATCH probe, not a
socket metadata claim. From the real host root it drops a child to UID/GID 2004
with no supplementary groups, launches the installed MCP identity gate, and
requires MCP initialize, tool discovery, and `system.get_health` to succeed in
one stdio session. The health payload must prove the Gateway and the matching
remote Simulator Execution identity are live. A dead or unlistened socket,
unknown token/session, or unavailable Execution service fails the preflight.
The current native v6 gate first verifies the explicitly static
`--installation-only` state, then executes the reviewed compatibility
four-UID lifecycle plus two-domain identity/socket isolation inner gate on
each of three distinct real VMs. Only a strictly parsed inner result may set
`runtime_preflight_executed=true`; build-time manifests, container evidence, or
an asserted boolean cannot claim this runtime proof.

The separate compatibility Agent OS rootful-container rehearsal requires two
independent external GO documents: one for the exact digest-pinned base image
and one for the loaded `hepta-systemd-gate` AppArmor policy. The latter binds
the reviewed policy-source digest to the kernel policy filesystem's exact
profile digest, raw-policy digest and raw ABI. The runner uniquely selects the
profile by its `name` field only after proving the fixed parent is securityfs,
the root-owned `policy` node is an `apparmorfs:[id]` magic link, its open
descriptor is on the kernel AAFS filesystem, and the current namespace is the
unstacked root namespace. It then requires enforcing mode, exact attachment
and zero learning count, validates the three raw-data symlink targets against
the same raw-data ID, and requires the complete policy record to remain
unchanged through cleanup. A third independently hashed external GO binds the
Docker Engine ID and exact root-owned daemon PID/start-time/boot identity to
that same AppArmor namespace; the local socket alone is not accepted as proof.
The PASS report persists the post-cleanup AppArmor and daemon-namespace
records, equality booleans and completed revalidation checks. A development
base candidate does not relax either gate. The runner never authors or loads
policy, and this shared-kernel rehearsal remains ineligible to replace the
three native VMs.

The P1 campaign has a separate source-run liveness gate. Its input manifest
contains the original dual-domain BASE runner, the review-closure consumer,
all four production safety-soak unit files, and the dedicated fixture; none of
those source-only gate runners is installed as a runtime CLI. The production
units instead close over the fixed installed hyphen executables for the
coordinator and both workers, while the static target has no `[Install]` path.
`HEPTA_ENABLE_P1_CAMPAIGN_ROOTFUL_LIVENESS_REHEARSAL` is off by default, and
its opt-in CTest omits `--certify`; consequently its
`hepta.p1-safety-soak-campaign-rootful-liveness-gate.v1` report is always
`REHEARSAL_ROOTFUL_NON_CERTIFYING` and grants no PAPER/LIVE authority.

The P1 launcher treats `formal_start_ms` as the fresh formal-history warmup
anchor.  Its disposable 91-sample load probe has a separate fixed dispatch at
`formal_start_ms - 1200000`; the launcher process must start within 60 seconds
on either side of that dispatch and must continuously constrain wall versus
monotonic drift to at most 1000 ms.  After the probe closes, the launcher waits
without formal WATCH authority, prepares a fresh admission binding immediately
before the anchor, and starts no formal reader, WATCH generation, or history
segment before the anchor.  Missing the bounded formal start fails closed.

The formal decision window begins at
`valid_after_ms = formal_start_ms + 12600000`, exactly the materializer's
210-minute rolling window.  That fresh same-campaign segment is sufficient for
the production minima of a 5,400-second quote span and forty consecutive closed
five-minute bars even at the allowed ten-second collector cadence and start
tolerance; probe or prior-campaign records are never preloaded.  Pre-valid
formal observations remain legal `WARMUP` records rather than decision
iterations.  This timing contract remains SHADOW-only and is not PAPER
approval.

### Local deployment evidence and active-v4 quarantine

The default product is a single-host `LOCAL_TRADING_OS`, but deployment
identity is not P1 admission. The v4 `admission_mode=local-only` shape omitted
the independently finalized P1 graph and is therefore quarantined. The
operator rejects every active v4 document with
`CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED` before consulting an
admission/deployment provider or calling the one-shot disarm boundary. The
prepare path rejects v4 promotion with `REPAIR_P1_ADMISSION_REQUIRED` before
reading strategy bytes, deployment evidence, creating a WAL, or mutating
systemd/configuration state.

The disabled v4 example and deployment-evidence recorder remain deliberately
non-authorizing. A separately certified install transaction publishes the
fixed canonical install closure as a root:root, regular, single-link mode-0600
file whose SHA-256 is supplied externally. The recorder verifies the complete
ordered 63-file installed path/hash/mode closure—including the external-P1
root finalizer, same-boot authority guardian, and finalizer socket/service—and
emits root-owned mode-0600
evidence with `paper_authorized=false`, `live_authorized=false`, and
`mutation_authorized=false`. Those artifacts can prove deployed bytes and
source/install identity, but can never disarm, preview, or submit an order.

Crash recovery also treats every legacy in-flight active-v4 prepare target as
non-committable. Even if the target policy was already published, recovery
fences it, re-proves the deny-all/no-session/inactive boundary, restores the
prior disabled policy and environment, and removes the WAL only after that
rollback succeeds.

The v5 successor has two exact, fail-closed admission shapes. The
`external-p1-finalized` shape exact-pins the P1 audit, WATCH-to-PAPER handoff,
terminal admission/finalization graph, source, strategy, domain, campaign,
time window, and deployment identity. Its short-lived candidate is consumed
once, while fresh, into an operator-owned root-only receipt under the
host-authority lease; later cycles revalidate that immutable receipt and every
source/P1/handoff/finalization/deployment pin. The `local-only` shape is the
bounded single-host successor to the retired v4 chain: it is eligible only
after legacy authority is disabled, the account is end-flat, broker policy is
DENY_ALL, and a certified Round105-or-later source/install closure has been
recorded. It carries no P1 fields and cannot be promoted from a caller-supplied
digest or an active v4 policy. Prepare accepts only one of these canonical
disabled v5 shapes, persists the complete binding in WAL v2, and any
interrupted prepare always fences and rolls back rather than restoring
authority.

The local controller still enforces the boundaries that affect trading risk:
the account must be an IB PAPER `DU` account, the endpoint must be loopback on
port 4002 or 7497, and LIVE remains false. The external-P1 shape accepts only
EUR.USD LMT/DAY intents with BUY fixed to observed ask and SELL fixed to
observed bid. The local-only shape accepts only EUR.USD MKT/DAY intents using
the same authoritative quote as the risk and slippage reference immediately
before broker send. One active order is allowed, quantity/cadence/holding
limits are finite, v5 is bounded to 24 hours and 720 cycles, and every cycle
is protected by the existing kill-switch watchdog and forced end-flat path.
`hepta-local-paper-control disable` atomically restores the deny-all identity
manifest and broker policy.

IB PAPER market data is Execution-owned. A reviewed contract allowlist drives
subscription request IDs and broker callbacks; complete bid/ask state,
observation time, and maximum age are required for quote reads, preview, and
place. The offline full-process fixture exercises MCP-shaped Tool Gateway
discovery, health, quote, single-use preview, exactly-once place/accepted
replay, and cancel against a fake broker. An accepted replay is a successful
duplicate at the MCP surface; rejection and uncertain outcomes remain tool
errors. It does not connect to IB or authorize PAPER.

Simulator authoritative quotes are also Execution-owned. A periodic worker
refreshes the configured contract set independently of Agent reads, and both
read and preview remain valid after the original quote TTL has elapsed only
when that worker has produced a fresh observation. Stop and partial-start
failure paths join the worker before releasing runtime state.

The current combined offline soak is
`hepta.execution-gateway-soak.v11`. Each round executes 9 binaries, each
independently hashed, and requires machine-readable proof for Execution-issued
mutation IDs, single-use permits, owner-fence permit revocation, exactly-once
same-command replay, the periodic Simulator feed, Gateway liveness, and the IB
fake-broker Agent-tool path. The v11 contract also binds the authoritative
quote instrument to every field of the actual broker contract and requires
fail-closed, zero-send behavior for any drift. It remains an offline code and
process-boundary certificate, not a provisioned-host, VM, PAPER, or LIVE
authorization.

The distributable archive is a full-repository strict-source bundle with
compiled and nonredistributable proprietary/prebuilt overlays removed. It is
not an Agent-OS-only archive: legacy and experimental source remains present
for compatibility, while default build flags keep those targets disabled.
Exact metadata manifests pin the legacy prebuilt set, CTP 6.5.1 Tools overlay,
and CTP 6.7.7 overlay without granting distribution authority. The native
three-VM v6 schema binds the complete Agent OS installation payload, staged
runtime-input manifest, exact ten-tool WATCH surface, per-VM runtime result and
lifecycle digests, the Execution evidence, and three independently signed
provisioner/hypervisor instance receipts with distinct UUIDs and challenges.
PASS requires real UID-2004
plugin loading, socket traversal, a custodian-owned WATCH `ACTIVE` handoff,
service/socket restart, exact-generation close, zero token/fence/export
residue, and a closure receipt on all three independent disposable VMs. External
object-store receipts and any PAPER venue certification remain separate gates.

The twelve checked-in CTP compatibility headers are forwarding source only.
Their canonical `third_party/ctp/6.7.7/include` target is an ignored,
operator-controlled overlay because the retained import has no reviewed
redistribution grant. Source CI verifies the exact forwarders and metadata and
requires the canonical overlay to be absent. A local full-payload gate may
verify and compile only after that overlay has been provisioned independently
with the manifest-pinned bytes; it never downloads, stubs, or publishes those
headers. CTP therefore remains unavailable from a clean source checkout and
disabled in the Agent OS runtime.

## Round35 Passive Distribution Layers

Round35 separates the public, locally reproducible delivery into three trust
layers instead of relabeling a native-VM certification image as a product
package:

1. The existing deterministic `hepta.clean-source-bundle.v2` remains the
   strict-source authority.
2. `hepta.vendor-overlay-set.v1` reads only the three provenance manifests
   inside that verified source bundle. It is metadata-only, lists every
   proprietary overlay as not included and not distribution-authorized, and
   binds its exact source lineage.
3. `hepta.runtime-package.v1` performs a fresh Release build from the verified
   source bundle with IBAPI and all legacy targets disabled. It merges only the
   passive `hepta-agent-os-runtime` and Simulator execution components, requires
   their two overlapping files to be byte-identical, and currently packages the
   exact 102-file `/usr` product surface. It does not contain `/etc`, `/run`, `/var`,
   credentials, broker adapters, PAPER units, SDK content, or vendor/prebuilt
   payloads, and it never provisions users, enables units, or starts services.

Passivity is an archive/installer behavior, not a requirement to erase valid
unit metadata. Gateway unit files retain their reviewed `[Install]` sections;
the package builder still creates no enablement symlink and invokes no
`systemctl enable` or `start`. Execution Simulator units remain deliberately
without `[Install]`. The verifier checks both distinctions.

`hepta.distribution-artifact-set.v1` then binds the source tar and manifest,
vendor descriptor, runtime tar and manifest as five exact roles. Its scope is
`local-offline-passive-simulator-runtime`; all version, source, vendor, target,
and digest lineage must agree. The runtime verifier independently checks safe
tar metadata, the internal/external manifest identity, the 102-file allowlist,
ELF architecture/dependencies/interpreters/no-RPATH, Python interpreters,
systemd `ExecStart` closure, and the passive safety boundary.

These artifacts improve reproducibility but do not certify a clean Git
checkout while the source baseline remains bound to uncommitted work. They
also do not replace the compatibility four-UID plus two-domain-identity
provisioned-host gate, real loader/MCP socket
traversal, three independent native VMs, external object-store receipt, or
separately authorized PAPER certification.

## Round35 Marketplace, WATCH Runtime Probe, and Receipt Contract

The runtime component installs a Codex team marketplace at
`/usr/share/heptatrader/.agents/plugins/marketplace.json` and its local plugin
source at `/usr/share/heptatrader/plugins/heptatrader-agent-os`. The marketplace
entry is versioned, has explicit installation/authentication policy, and points
only to that reviewed package. Codex registers `/usr/share/heptatrader` and
installs `heptatrader-agent-os@heptatrader`; OpenClaw installs the same bundle.
Neither operation provisions a session or grants trading authority. The
non-setuid launcher still requires the host process to already be UID/GID 2004.

The provisioned-host runtime preflight now requires the exact ten-tool WATCH
surface and calls health, quote, account, positions, orders, and risk reads in
one MCP session. It rejects every `trade.*` tool and `risk.preview_order`, and
checks equality between the text and structured result envelopes. This is a
source and fixture closure until it runs on a genuinely provisioned host with
the compatibility four identities plus at least two isolated domain
Gateway/Agent/Execution identity triples, activated systemd sockets, a WATCH
token, and live Simulator Execution services. Static native-VM installation
evidence cannot be promoted to this runtime claim.

Evidence ingestion is deliberately split across trust domains. A local,
offline, content-addressed v2 request builder can only emit `pending-external`
requests after verifying a manifest-defined evidence set. The set binds the
exact inventory and delivery-closure roles, release identity, source-baseline
lineage, evidence index, and raw manifest bytes. The manifest is itself added
to the upload/readback object closure. Membership in a manifest-defined
certification set elevates every member object's requested retention to
indefinite legal hold, including roles whose standalone index classification
would otherwise be finite. A legacy v1 request can still support local
compatibility tests, but the production verifier rejects it and requires the
manifest-defined v2 chain. Neither request format can assert upload, readback,
retention, source deletion, PAPER, or LIVE.
An independently operated ingestion service must upload each object, read it
back completely, enforce an immutable version and the strongest required
retention, then sign a domain-separated canonical statement with Ed25519.

The offline verifier accepts only the canonical RFC 8410 Ed25519 SPKI shape,
pins its digest, validity, revocation state, store allowlist, and retention
policy, and requires canonical Base64 plus exact
request/set/object/readback/retention closure. It re-verifies the local
request, evidence set, index, payloads, policy, and trust snapshot after the
signature operation and again after capturing the verification clock and
evaluating current retention. The request builder likewise compares the
verifier-reported manifest and index digests with its initially captured raw
bytes and confirms both files again before returning. A concurrent trust
revocation or evidence mutation therefore fails instead of returning a stale
current-policy result.

Receipt and request times use a strict ASCII RFC 3339 profile with at most six
fractional-second digits, matching the verifier's exact timestamp precision,
rather than the broader ISO-8601 forms accepted by language runtimes. A
readback attestation or receipt signature later than the captured verification
time is not current, even within a nominal clock-skew window. The signing key
must be valid both at the claimed signing time and at verification time.
Verification reports bind the captured trust policy, request, index, and
evidence-set manifest digests; they are signed-snapshot evaluations, not a
replay ledger or a trusted timestamp service.

The repository production trust policy intentionally contains no keys and is
`pending-external`. No uploader, object-store credential, receipt private key,
or source-removal implementation is shipped. CI may publish a pending request
as a convenience artifact, but that artifact is not a production ingestion
receipt or retention anchor. A future `configured-external` policy and each
public key are accepted by the canonical verifier only from the fixed
`/etc/heptatrader/heptatrader-evidence-receipt-trust-v1.json` location and a
root-owned path whose parent chain is not group/world writable. The repository
policy is only the pending template used while that system file is absent.
Finite retention must extend for the full policy interval after the latest of
ingestion, readback verification, and receipt signing, so delayed signing
cannot certify an already expired remote lock.

## Round36 Final Certification

Round36 adds a final, fail-closed certification aggregator rather than a new
way to manufacture external evidence. Every report first re-verifies the fixed
Round36 delivery closure
`heptatrader-round36-semantic-v1-delivery-closure-v1.json` against the exact
`heptatrader-round36-semantic-delivery-artifacts-v1` root. It binds the raw
closure and all seven artifact records, Git/source-manifest lineage, strict
source tar/manifest/files digests, and rechecks their inode, size, mode and
SHA-256 after all external verification work.

The two optional external evidence classes are independent:

1. The native input must pass the imported v6 file-backed aggregate verifier
   and its
   `native-disposable-vm-agent-os-watch-runtime-rootful-systemd` level. The
   aggregate binds the absolute path, SHA-256, size and `0600` mode of the
   exact root-owned `real`, `sandbox`, and `stub` variant reports. The verifier
   reopens all three through a protected no-follow parent chain, parses each
   full variant contract, reruns `aggregate_reports`, requires byte-for-byte
   semantic equality with the supplied aggregate, and rechecks all three
   inputs. It therefore enforces common VM/kernel identity, exact executed
   kinds and binary closure, runtime/binary digests, three distinct native VMs,
   fixed UIDs 2001–2004, real four-UID Agent OS runtime preflights, one-session
   WATCH discovery and reads, revocation/cleanup evidence, physical
   non-loopback isolation, and the offline IB/PAPER/LIVE boundary.
   Installation-only v4 evidence, container rehearsal reports, and a
   hand-edited aggregate that merely passes its shallow shape parser are not
   eligible. Its clean-source tar/manifest/files digests must equal the bound
   Round36 lineage.
2. The receipt input must pass
   `verify_heptatrader_evidence_ingestion_receipt.verify_receipt` with
   `require_system_trust=True` against the exact request, index,
   evidence-set manifest, evidence root, retention policy, and fixed system
   trust policy. The returned contract must be system-production,
   signature-verified, manifest-defined, and current-policy-satisfied. Its
   round-closure and every delivery role must bind the same Round36 bytes.
   Test-local keys or verifier results are never promotable.

The builder accepts no certification booleans. If either evidence class is
absent, the only publishable result is `status=pending-external`,
`passed=false`; production, systemd, receipt and retention flags all remain
false even when the other class verifies independently. Only both verifier
contracts together produce `status=certified` and `passed=true`. Real IB,
broker connection, order placement, PAPER, LIVE, source deletion and source
removal remain false in both states.

The report is deterministic canonical JSON, private `0600`, and published
without overwrite through a protected parent and atomic same-directory link.
Every raw file input is bound by canonical path, SHA-256, size and mode and is
re-read after verification; the verifier reconstructs the complete report
from those paths and rejects noncanonical bytes, cross-lineage substitution,
or mutation during verification. The checked-in trust template has no keys,
and no genuine native aggregate exists locally, so repository/CI use of this
contract remains `pending-external`; the scripts do not create trust keys,
connect a broker, or authorize PAPER/LIVE.
