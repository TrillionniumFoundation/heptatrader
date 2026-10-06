# Repository development and verification

The only maintained integration branch is `main`. Source integration is separate
from installing a binary or enabling trading. See [main convergence](../technical/main-convergence-20261006.md).

## Ordinary feedback

`Core Runtime CI` runs one exact-source native build and the disjoint Python core
and source suites once. It does not build release archives, drive systemd package
acceptance, poll other workflows or manufacture approval receipts on every edit.
`./scripts/dev_core.sh` and `--storage` are the same local native entry points.

The standalone GCC/Clang sanitizer suite and offline/client SDK integration remain
available by workflow_dispatch. Tagged release still requires actual installed
artifact acceptance. Optional IB qualification retains its external account,
runner and network boundaries. None of these is inferred from a green code test.

## Code versus documentation

`check_documentation.py` checks local links only. Module header spelling, generated
navigation, status synchronization and prose length do not block development.
`module-catalog.json` retains directory ownership for actual Git/CMake reachability
checks; it is not a completion score or an instruction to repeat file lists in
several documents. The optional IB observer is absent from SDK-free builds by design.

The pure JSON reader, live CMake ownership check, systemd boundary checks and real
behavior tests remain. Source issue inventories may still be inspected manually;
release-profile blockers remain distinct from routine code integration.

## Main protection

The owner-requested configuration retains default-branch deletion and force-push
protection, but removes required status aggregation, mandatory approval counts
and Merge Queue. Actual CI results remain visible; no pending/skipped job is
reported as passing. Branch publication never changes Broker authority.

All Python tests remain in disjoint discovered partitions. Install/process tests
require a disposable isolated fixture, never the active trading host. The
release and IB proof parsers, fsynced journals, original-command recovery,
identity checks, risk limits, MKT/IOC constraints and reduce-only behavior are
unchanged by the removal of development paperwork.
