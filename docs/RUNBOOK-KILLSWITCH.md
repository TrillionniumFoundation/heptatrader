# PAPER kill-switch runbook

Status: current for IB PAPER target-host operation
Applies to: `HeptaTrade/execution/ib_paper_kill_switch.*`, PAPER service/control directory
Verification: same-revision core tests; target-host validation required

## Semantics

IB PAPER uses a deployment-owned, read-only-to-Execution file-system kill switch. A valid marker means engaged. A stably absent marker means disarmed. Any directory/marker identity, owner, mode, link, symlink or I/O uncertainty is treated as engaged/uncertain and blocks risk increase.

Execution reads but cannot disarm the switch. Agent, MCP adapter, Tool Gateway and venue adapter cannot modify the control directory.

## Engage

A controlled root/operator path atomically creates the valid marker, confirms Execution observed the blocked state, then performs owner-scoped cancellation, strict reduction or authoritative flatten as appropriate.

## Disarm

Disarm only after:

1. current broker/Execution identity is known;
2. active orders, terminal correlations and positions are reconciled;
3. risk limits, account and instrument bindings are reviewed;
4. control-directory operation is atomic and permissions remain canonical;
5. any uncertain observation returns to engaged.

The repository does not provide Agent-callable disarm or automatic campaign scripts.

## Safe exit

The switch blocks risk increase. Owner-scoped cancel and authoritative no-cross-zero flatten remain available only when current state proves them safe.

## Tests

```bash
cmake --build build/core-release --target hepta_ib_paper_kill_switch_tests
ctest --test-dir build/core-release --output-on-failure \
  -R hepta_ib_paper_kill_switch_tests
```
