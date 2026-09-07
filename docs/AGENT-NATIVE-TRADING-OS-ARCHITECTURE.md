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

CTP and XT/QMT are experimental interface scaffolds with no transport. They are not real venues. LIVE is unavailable. The authoritative machine-readable state is [`capabilities.json`](capabilities.json).

## Authority boundaries

The Agent entry performs discovery, schema validation, request encoding, and result decoding. The Tool Gateway authenticates peer/session/capability, applies a coarse bounded policy, audits decisions, and forwards requests. The Execution Service alone may journal and send a venue mutation. Venue adapters translate protocol and callbacks; they do not decide strategy or portfolio allocation.

Agent and Gateway identities do not receive broker credentials or broker-port network access. A strategy emits forecasts, target exposure, or bounded intent; it cannot assert authoritative position, quote, fill, risk approval, or broker success.

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
- **CTP / XT-QMT — EXPERIMENTAL:** no real transport, connection, query, or mutation authority.
- **LIVE — UNAVAILABLE:** no authorization or supported path.

## Development

```bash
./scripts/dev_core.sh
python3 scripts/check_documentation.py
python3 -m unittest discover -s tests/python -p 'test_*.py'
```

Module-level contracts are under [`modules/`](modules/). Operations are under [`operations/`](operations/). Repository source correctness, live GitHub governance, runner/environment trust, and broker qualification are separate claims.
