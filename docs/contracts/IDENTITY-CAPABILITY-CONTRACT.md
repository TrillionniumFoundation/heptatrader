# Identity and Capability Contract

Status: current core contract
Applies to: session supervisor, Gateway, native/MCP clients and operator profiles
Verification: peer identity, lease, token, capability and negative tests
Authority: Agent-side authorization boundary

授权决策绑定 OS peer identity、session ID、lease generation、capability set、socket trust domain、expiry 和 audit sequence。token 只证明受控 session possession，不授予 Broker truth或绕过 Execution。

普通 Agent 只能获得 read、bounded intent、cancel/flatten 等明确 capability；raw place 属于独立 operator profile。未知 capability、过期 lease、UID/GID 不匹配、附加组不符、token path 不安全或 audit persistence 失败时拒绝。

Capability 名称是版本化有限集合。tool 名称不是 capability；一个 tool 的 capability requirement 必须由 tool catalog 声明并由 Gateway 与 Execution 双重约束。Management 不能通过配置把 unsupported/LIVE capability 变为可用。

## Session supervisor contract (`hepta.session-supervisor.v1`)

`hepta.session.runtime` is the sole durable authority for supervisor lease records, lease generations, predecessor fencing, recovery-only state and PAPER finalization tombstones. `hepta.client.runtime` encodes bounded supervisor requests; `hepta.gateway.runtime` validates peer identity and invokes the supervisor boundary. Neither consumer may manufacture an accepted lease or advance a durable generation locally.

### Request identity and operations

Every request is one of `Provision`, `Revoke`, `Renew`, `Rotate`, `RecoveryQuery`, `PaperFinalize`, `PaperFinalizeAck`, `PaperTerminalizeAck`, `PaperTerminalWitnessPrepare` or `PaperTerminalWitnessAck`. The operation is bound, as applicable, to template ID, current/replacement token, agent ID, session ID, peer UID, TTL, expected lease generation, target command ID, recovery/finalization IDs and content digests. Unknown operations, malformed canonical fields, zero/overflow TTL, stale expected generation, unsafe token replacement and incomplete PAPER evidence fail closed.

### Durable state and fencing

A lease record binds issuer, token, agent/session identity, peer UID, exact Execution account/domain owner scope, expiry, lease generation, predecessor token/generation and fence state. Mutating operations serialize through the single owner and commit the encrypted lease store atomically before success is returned. Rotation never makes both predecessor and replacement authoritative: the predecessor becomes fenced and the replacement is accepted only at the next durable generation. Restart must reload and authenticate the store before serving requests; metadata, key, decrypt, parse or persistence uncertainty closes admission.

Expiry is evaluated against the authority-owned clock. A consumer-provided timestamp never extends a lease. Heartbeat or network liveness is diagnostic unless it completes a valid `Renew` transition against the current token and generation. A stale token, stale owner, expired record, changed account/domain scope or generation mismatch cannot be repaired by retrying with a new identity.

### Recovery and PAPER finalization

Recovery state is non-authorizing except for the explicitly registered recovery operation. PAPER finalization is a one-way state machine: `None -> FencePending -> FenceComplete -> AuditSealed -> purged acknowledgement`. Finalized records are non-authorizing tombstones and cannot be provisioned, renewed or rotated back into a Tool session. Group sealing and purge require exact recovery/finalization IDs, owner-set digest/count, acknowledging owner identity/generation and terminal receipt digests. Missing or conflicting broker/owner evidence keeps the mutation gate closed.

### Result semantics

`accepted=true` means the requested transition was durably admitted under the returned `leaseGeneration`; it is not Broker truth and grants no Execution authority beyond the separately validated capability. Results carry a stable reason code and, for recovery/finalization, explicit owner fencing, Execution fencing generation, authoritative command status, broker generation/barrier fields and terminal evidence digests. Clients must treat transport failure after submission as uncertain and query authoritative supervisor state rather than replaying a mutation blindly.

### Compatibility and verification

The C++ wire DTO in `HeptaTrade/tool_host/session_supervisor_protocol.h`, the durable record and transition API in `session_supervisor_lease_store.h`, the contract registry, ModuleManifests and generated guides must change atomically. Removing or reinterpreting an operation or field is a major contract change. Additive fields require closed-world decoder tests, old/new golden vectors, malformed-input negatives, crash-before/after-persist tests, stale-generation/fencing tests and exact replay evidence. The current verification authority is `session-boundary` plus `protocol-contracts`; distributed consensus is not claimed by this same-host contract.
