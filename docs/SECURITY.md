# Security contract

Status: current runtime contract; external PAPER host controls remain separately validated
Applies to: `HeptaTrade/`, `adapters/mcp/`, `systemd/`, `scripts/hepta_agent_*`
Verification: `canonical-full-suite` on the exact revision; target-host checks are separate

## 1. Threat model

The Agent, model output, MCP input, local client arguments, market-data transport and broker connection can all be wrong, stale, duplicated, delayed or malicious. HeptaTrader therefore treats every external value as untrusted until it is normalized and checked by the authority that owns the corresponding state.

The primary failures to prevent are:

- an Agent bypassing deterministic risk or broker ownership;
- a replay or retry producing a second order;
- stale quote/account/position state authorizing new exposure;
- a revoked or expired session continuing to mutate;
- a reconnect splicing state from different execution epochs;
- a scaffold venue reporting synthetic success;
- inability to cancel, reduce or flatten during a degraded state;
- secret or token disclosure through source, logs, process arguments or broad file permissions.

## 2. Trust zones

```text
Untrusted: model output, MCP JSON-RPC, CLI input, research artifacts
Boundary: Agent adapter and Tool Gateway
Trusted control: Execution Service, deterministic risk, OMS journal
External authority: broker/venue
Authoritative projection: Execution reconciliation state
```

Tool Gateway authenticates peer identity, token, session, capability, tool schema and bounded framing. It never links broker symbols or reads broker credentials. Execution Service owns venue connections, order IDs, lifecycle, durable commands and authoritative projections.

## 3. Credentials and files

- Broker credentials are loaded only by the broker-owning execution service.
- Session token files must be regular, non-symlink files owned by root or the expected runtime UID and inaccessible to group/world.
- Token/config readers use `O_NOFOLLOW`, bounded reads and before/open/after metadata checks where supported.
- Real credentials and rendered private config never enter Git.
- Example files contain no usable account secret.
- Secrets must not be accepted through Agent-visible tool arguments.

## 4. Identity and capability

Each mutually untrusted Agent uses a distinct OS identity, socket/session token and server-bound instrument/account scope. Capability names are explicit. Ordinary Agent profiles do not include raw order placement authority; operator-only authority is provisioned separately.

A session record binds at least:

```text
principal, environment, capabilities, account, instruments,
issued_at, expires_at, generation, decision-lease fence
```

Expiry, revoke, generation mismatch or unknown environment fails closed.

## 5. Mutation safety

A new mutation is accepted only after:

1. peer/session/capability validation;
2. schema and semantic validation;
3. authoritative snapshot/freshness validation;
4. deterministic risk decision;
5. stable command ID and request fingerprint binding;
6. durable journal append;
7. venue send through the sole broker authority.

The same command ID with the same fingerprint returns the durable result. The same ID with a different fingerprint is an idempotency conflict. An uncertain result is queried/retried; it is never replaced by a new ID for the same intended mutation.

## 6. Safe exit

Global risk blocks may stop new exposure, but a proven cancel, strict reduce-only or authoritative flatten must remain available unless the runtime cannot establish the state required to make that action safe. Reduce-only never crosses through zero.

## 7. Venue and LIVE policy

CTP and XT/QMT are unsupported until real transport, connection lifecycle, order correlation, correction, reconnect and reconciliation tests exist. They return a typed unsupported result rather than a synthetic success.

LIVE is unsupported. A future activation must be an explicit reviewed change that reuses the same Execution authority, deterministic risk, journal, fencing, kill switch and reconciliation; no Agent or legacy bypass is permitted.

## 8. Security tests

The bounded core gate covers protocol bounds, session/token semantics, authority
separation, idempotency, journal durability, kill switch, snapshot and
execution lifecycle. The permanent `canonical-full-suite` gate adds
ASAN/UBSAN, malformed-protocol, crash/replay and bounded performance fixtures;
the separately scheduled reliability workflow remains useful for repeated
diagnostics. CI has read-only repository permissions and no merge authority.
