# Unprivileged research strategy client

Status: QUALIFICATION_REQUIRED
Applies to: integration/heptadll-modular-20260919
Implementation: `research/python/hepta_research/gateway.py`
Tests: `tests/research/test_gateway.py`, `tests/research/process_smoke.py`

## Interface and ownership

[The research client contract](../../research/README.md) defines supported
LimitIntent fields, private outbox layout, two-phase invocation and recovery.
The client calls the existing installed heptactl/NativeToolClient. It neither
loads vendor SDKs nor connects to broker fronts. Execution alone approves,
journals, correlates and sends venue mutations. Existing session, peer UID,
capability, risk, quote and persistence checks remain in that path.

## Failure semantics

Prepare has no order side effect. A successful preview must contain an
Execution-issued command ID, single-use permit and service identity. Immutable
fields and expiry are durably stored before sending. Persistence failure prevents
a new send; response loss or restart causes only a query of the original command.
An accepted RPC is not an authoritative fill. No new command ID, replacement
expiry or independent position/risk claim is synthesized. Unsupported contract
identity or old CTP open/close semantics are rejected rather than dropped.

## Tests and observability

Fault injection tests cover pre/post-send disk failure, process interruption,
concurrent submissions, session rebinding, corruption and secure file access.
The separate digest-admitted multi-UID installed process test checks the actual
Gateway/Execution route, rejection boundaries and persisted recovery. Only its
filtered report and public process observations may be uploaded, never token,
permit or outbox bytes. No additional daemon, deployment identity, approval
service or production authorization is introduced.
