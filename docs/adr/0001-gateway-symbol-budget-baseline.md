# ADR 0001: Gateway Release symbol budget baseline

- **Status:** Accepted
- **Date:** 2026-08-29
- **Scope:** `hepta-tool-gatewayd` Release builds

## Context

The Tool Gateway is deliberately separated from broker adapters and credentials. Its post-link verification therefore has two independent controls:

1. a deny-list that rejects broker, venue SDK, credential-environment and process-exec authority; and
2. a bounded count of defined Release symbols, which makes accidental authority growth visible even when no individual forbidden symbol is matched.

After the runtime was moved to C++17 and target-scoped CMake targets, exact-head GitHub Actions evidence measured **1420** defined symbols. The historical budget of 1200 no longer described the reviewed implementation. Disabling the budget would remove a useful structural control and is rejected.

## Decision

- The forbidden-symbol deny-list remains mandatory in every Linux Gateway build.
- Release builds enforce a maximum of **1500 defined symbols**.
- Debug builds enforce the deny-list but do not apply the Release cardinality budget because diagnostic builds retain dead helper code.
- Any future budget increase requires:
  - an exact-head Release measurement from the repository CI;
  - an explanation of the added Gateway responsibility;
  - confirmation that no broker or credential authority moved into the Gateway; and
  - an update to this ADR in the same change.

## Consequences

- The 1500 limit leaves 80 symbols, approximately 5.6 percent, above the measured 1420 baseline.
- Growth beyond the reviewed headroom fails the build instead of silently expanding the Gateway.
- The cardinality budget supplements the deny-list; it never replaces it.
- Broker, risk, OMS and venue responsibilities remain in the Execution Service.
