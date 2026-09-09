# HeptaTrader

HeptaTrader is an AI-facing trading runtime that keeps model-generated intent separate from deterministic execution authority, broker credentials, risk checks, durable order state, and reconciliation.

> **Repository status:** `0.1.0-beta.1`. Simulator development is supported. IB PAPER is a qualification-gated candidate. CTP and XT/QMT adapters are experimental scaffolds. LIVE trading is not implemented or authorized.

## Canonical runtime

```text
Agent / MCP / heptactl / native client
                  |
                  v
             Tool Gateway
                  |
                  v
          Execution Service
                  |
          +-------+--------+
          |                |
 deterministic simulator  IB PAPER candidate
```

The Agent never owns a broker session or broker credential. Risk-increasing mutations must pass session/capability checks, deterministic risk, an Execution-issued command identity, durable journal-before-send, venue submission, authoritative projection, and reconciliation.

## Build and test

Linux development requires CMake 3.16+, a C++11 compiler, OpenSSL development headers, Python 3, and pthreads.

```bash
./scripts/dev_core.sh
python3 scripts/check_documentation.py
python3 -m unittest discover -s tests/python -p 'test_*.py'
```

The default build keeps the legacy monolith, legacy simulator, IB SDK integration, and deprecated 0DTE bridge disabled. Enabling IB compilation requires a separately supplied, pinned IB C++ API source tree.

## Capabilities

| Capability | Status | Mutation authority |
|---|---|---|
| Deterministic simulator | CURRENT | local deterministic venue only |
| Agent Tool Gateway | CURRENT | forwards bounded calls to Execution Service |
| IB PAPER | QUALIFICATION_REQUIRED | disabled unless the fixed profile, host controls, exact-current-main artifact, and Broker qualification all pass |
| CTP adapter | EXPERIMENTAL | fail-closed; no real transport |
| XT/QMT adapter | EXPERIMENTAL | fail-closed; no real transport |
| LIVE | UNAVAILABLE | none |

The machine-readable capability source of truth is [`docs/capabilities.json`](docs/capabilities.json). The module map is [`docs/module-catalog.json`](docs/module-catalog.json), and the exact CMake-target/translation-unit ownership inventory is [`docs/build-targets.json`](docs/build-targets.json).

## Documentation

Start with [`docs/index.md`](docs/index.md). Every maintained module has a module-level contract covering responsibilities, public interfaces, state, failure semantics, persistence, security boundaries, observability, tests, and known limitations. Documentation consistency is enforced by `scripts/check_documentation.py`. Build ownership is checked against fresh CMake File API replies by `scripts/verify_build_ownership.py`. Registered source and Broker-qualification gaps are recorded in [`docs/gap-register.json`](docs/gap-register.json).

## Security invariants

- Only the Execution Service may send orders to a venue.
- Agent-facing processes do not hold broker credentials or broker-port network access.
- Risk-increasing mutations are journaled before external send and are idempotent by command ID.
- Unknown session, identity, quote, state, configuration, persistence, or kill-switch conditions fail closed.
- Cancel, reduce-only, and authoritative flatten remain available only through their explicit guarded paths.
- IB PAPER qualification binds the exact current `main` SHA, immutable build inputs, the resulting artifact, the pinned external harness, PAPER account mode, and the final reconciled Broker state.
- LIVE remains unavailable regardless of repository workflow or PAPER qualification state.

## Owner-operated repository

This is a self-use, single-operator system. The owner may commit, merge, revert, tag, and release directly. Pull requests, reviews, CI checks, and branch settings are optional engineering aids rather than authorization prerequisites. There is no team-distribution, CODEOWNERS, dual-approval, Merge Queue, protected-governance-environment, or governance-receipt requirement.

A real IB PAPER campaign still requires the exact current `main` revision, isolated build and PAPER execution identities, pinned SDK/BID and harness inputs, the operator kill switch, authoritative reconciliation, and a verifier-issued Broker receipt. Repository convenience never grants LIVE authority.
