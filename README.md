# HeptaTrader

HeptaTrader is an owner-operated trading runtime focused on explicit execution authority, deterministic risk checks, durable mutation identity, and authoritative recovery.

## Current capability

- **Deterministic simulator — CURRENT.** Canonical local development and fault-injection venue.
- **IB PAPER — QUALIFICATION_REQUIRED.** Implemented as a bounded PAPER-only candidate; real Broker mutation remains disabled until an external qualification campaign succeeds for the exact immutable candidate artifact.
- **CTP / XT-QMT — EXPERIMENTAL.** Interface scaffolds only; no real transport or mutation authority.
- **LIVE — UNAVAILABLE.**

`paper_authorized=false` and `live_authorized=false` are deliberate defaults. Source, CI, review, release packaging, and preflight do not grant Broker authority.
The machine-readable matrix uses separate transport, advertising, and authorization decisions; see [`docs/technical/capability-advertising.md`](docs/technical/capability-advertising.md).

## Architecture

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

The Execution Service is the sole external-order authority. Risk-increasing mutations follow the durable path:

```text
bounded intent
 -> peer/session/capability validation
 -> authoritative quote/state + deterministic risk
 -> durable intent + send-attempt journal
 -> venue send
 -> ordered projection
 -> command status + authoritative reconciliation
```

A lost response after a possible send is `UNCERTAIN`; it is not permission to generate a new mutation identity.

## Develop

Canonical local validation:

```bash
./scripts/dev_core.sh
python3 scripts/check_documentation.py
python3 -m unittest discover -s tests/python -p 'test_*.py'
```

The canonical documentation entry point is [`docs/index.md`](docs/index.md). The cross-module implementation map is [`docs/technical/runtime-engineering-map.md`](docs/technical/runtime-engineering-map.md).

## Release and PAPER qualification

Release engineering builds one deterministic, content-addressed artifact and carries the same identity through preflight, simulator lifecycle smoke, deployment, rollback, and optional Broker qualification.

IB PAPER qualification admits only the owner-dispatched `main` revision selected at workflow dispatch. Once the exact candidate artifact has been built and verified, qualification is bound to that immutable source/artifact/harness/profile/account/host/scenario tuple; later movement of the `main` branch does not mutate or invalidate the artifact. Any change to a bound input requires a new campaign.

The controls that materially protect trading behavior remain non-negotiable: journal-before-send, stable command IDs, authoritative reconciliation, deterministic risk, kill switch, owner/session fencing, Broker credential and network isolation, exact artifact identity, and final terminal/flat reconciliation.

## Repository model

This is an owner-operated, self-use repository. Pull requests, reviews, CI checks, and branch settings are engineering aids rather than trading authorization. See [`docs/adr/0002-owner-operated-repository.md`](docs/adr/0002-owner-operated-repository.md) and [`docs/adr/0003-immutable-artifact-paper-qualification.md`](docs/adr/0003-immutable-artifact-paper-qualification.md).
