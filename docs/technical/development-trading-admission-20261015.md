# Development admission is not trading authorization (2026-10-15)

Status: CURRENT. This policy distinguishes existing workflows; it adds no new
runtime gate, approval service or second control plane.

## Use the smallest gate that protects the thing being changed

| Scope | Source/PR acceptance | Separate operations qualification |
| --- | --- | --- |
| Docs and offline validators | Correct behavior, relevant tests, exact source identity | None: NAV, economic alpha or broker Witness are not needed to merge docs |
| Research and shadow candidates | Causal inputs, frozen failures retained, tests, no order ability | Genuine prospective plan before a new forward trial; no automatic model promotion |
| Read-only monitors and collectors | Source parity, bounded publication/failure tests, real restart and rollback where changed | Monitor-only acceptance; NOT full-account NAV or net-alpha qualification |
| Broker adapter or resident driver | Exact tests, recovery/unknown-send/position compatibility | Real risk/owner/OMS/broker reconciliation before a controlled cutover |
| New-risk authority or promoted model | Passing CI is not sufficient | Existing Kill Switch, native risk, original order identity and authorized operator decision |

A failed historical 4-window trial must never be backfilled or reclassified.
A new monitor implementation does not inherit the old experiment's source
readiness and efficacy verdict. It may be merged and evaluated separately while
the original observations stay immutable. Repeated cross-host copies of the
same already reconciled failure are not a blanket merge requirement: only
escalate for an identified independent-trust or fault-mode question.

## Existing safety boundaries remain nonnegotiable

Retain the native kill switch, account and owner fencing, lease/epoch generation,
idempotent possibly-sent command reconciliation, authoritative broker positions,
partial-fill/reduce-only behavior, current quote TTL, risk caps and real terminal
identity. Unknown or rejected full-account NAV must remain unqualified and may
not be relabeled from an algebraic exchange-rate inference.

Treat three independent statuses separately: (1) the Git source merged into
main; (2) an immutable release deployed and running; (3) permission for new
risk-increasing orders. Neither status (1) nor status (2) implies (3).
An active process or `mutation_ready` must not silently disarm a manually
engaged PAPER hold. Never flatten a position solely to enable logging updates.

## Existing workflows and PR backlog

`core-ci.yml` has two independently running native/Python checks and does not
run tagged artifact acceptance. `canonical-full-suite.yml` and
`heptadll-integration.yml` are deep, manually dispatched checks.
[Tagged artifact acceptance](core-release-acceptance.md) is separate and does
not itself authorize PAPER or LIVE deployment. Do not reintroduce the obsolete
cross-workflow polling, duplicate CI-policy checks or a status-reachability
control plane.

For Draft/stacked PRs, compare each effective changed file with current main:
close already absorbed or historical-only work with a disposition reference,
port valid source fixes to a small current-main commit, and keep genuine
research-only scripts unpromoted. Do not merge entire stale parent stacks just
to satisfy ancestry. Preserving the original private research evidence does not
require retaining dozens of unmerged operational branches.

## Concrete debts recorded in this round

* FX opening-limiter retirement is already merged into main, but the resident
  driver can still pin an older immutable release. Upgrade only after a real
  nonflat position/command continuation plan, not as a side effect of merging.
* The read-only source-health daemon's default **60 seconds after assessment**
  cadence conflicts with some forward experiments' **30-second as-of budget**.
  Fix monitor publication for new observations while preserving the original
  quote TTL, source HOLD and historical decisions. Its monitor improvement
  does not require model alpha or Flex evidence.
* Existing exact decision attribution can now be inspected without flat state,
  and byte-identical deployed code can be rechecked without a restart. A
  genuinely different deployed trading module still needs owner-safe upgrade
  qualification.
* Historical failed transient systemd units and layered drop-ins require a
  scoped maintenance inventory, not wholesale deletion of running research
  timers or any manual emergency hold.

This document approves neither live trading nor changes to a running
risk-increasing strategy.