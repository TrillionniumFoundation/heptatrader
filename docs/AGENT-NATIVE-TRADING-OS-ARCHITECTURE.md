# HeptaTrader Agent-native runtime architecture

Status: CURRENT  
Applies to: repository HEAD  
Canonical index: [`index.md`](index.md)

## Runtime path

```text
Agent / MCP / heptactl / native client
                  |
                  v
             Tool Gateway
                  |
          typed local protocol
                  v
          Execution Service
                  |
        +---------+---------+
        |                   |
deterministic simulator   IB PAPER candidate
```

CTP remains a deferred experimental negative-capability scaffold. XT/QMT now has an executable HXQ1 v1 **read-only protocol client boundary**, but no qualified Windows/QMT mTLS transport and no mutation authority. Neither is a qualified trading venue. LIVE is unavailable. The authoritative machine-readable state is [`capabilities.json`](capabilities.json).

## Authority boundaries

The Agent entry performs discovery, schema validation, request encoding, and result decoding. The Tool Gateway authenticates peer/session/capability, applies a coarse bounded policy, audits decisions, and forwards requests. The Execution Service alone may journal and send a venue mutation. Venue adapters translate protocol and callbacks; they do not decide strategy or portfolio allocation.

Agent and Gateway identities do not receive broker credentials or broker-port network access. A strategy emits forecasts, target exposure, or bounded intent; it cannot assert authoritative position, quote, fill, risk approval, or broker success.

The XT read-only HXQ1 boundary does not change that rule: a future peer-pinned mTLS channel is supplied only to the Execution-owned adapter, and the present adapter has no place/cancel transport path.

## Mutation invariant

```text
bounded intent
 -> peer/session/capability validation
 -> normalized schema and command identity
 -> authoritative quote/state and deterministic risk
 -> durable intent and send-attempt journal
 -> venue send
 -> ordered event projection
 -> command-status and authoritative reconciliation
```

The same command ID must be reused after an uncertain or lost response. A new command ID is a new mutation and must not be generated merely to “retry.”

## Failure model

Unknown identity, session generation, capability, protocol, configuration, quote, position, active order, persistence, kill switch, broker callback, or qualification state fails closed for risk increase. Cancel, reduce-only, and authoritative flatten are explicit guarded exit operations, not blanket bypasses.

## Runtime modes

- **Simulator — CURRENT:** deterministic local venue for development and fault tests.
- **IB PAPER — QUALIFICATION_REQUIRED:** fixed broker-owning service, profile, credential, kill switch, network boundary, journal, and reconciliation; external host/runner/broker evidence is mandatory.
- **XT/QMT — EXPERIMENTAL:** HXQ1 v1 framing and read-only identity/account/position/quote request boundary are implemented; the real peer-pinned mTLS/QMT sidecar and all mutation authority remain unavailable pending qualification.
- **CTP — EXPERIMENTAL / DEFERRED:** fail-closed scaffold only; no parallel transport expansion while XT is the selected next venue.
- **LIVE — UNAVAILABLE:** no authorization or supported path.

## Development

There is one repository-owned development entry instead of multiple overlapping “full suite” recipes:

```bash
./scripts/dev_core.sh
python3 scripts/run_python_tests.py --lane core
python3 scripts/run_python_tests.py --lane source
```

Install/process acceptance has stronger prerequisites and is intentionally invoked by the release acceptance driver rather than by an unscoped `unittest discover`. For targeted debugging, individual test modules may still be run directly, but a targeted invocation is not a substitute for its canonical lane.

Module-level contracts are under [`modules/`](modules/). Operations are under [`operations/`](operations/). Repository source correctness, live GitHub governance, runner/environment trust, target-host monitoring/rollback evidence, and broker qualification are separate claims.
