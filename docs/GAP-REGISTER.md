# Gap register and release scope

Status: CURRENT
Applies to: repository HEAD
Machine source: [`gap-register.json`](gap-register.json)
Verification: `python3 scripts/check_gap_register.py`

## Honest issue state

Schema v2 permits new IDs and the states `OPEN`, `ACCEPTED`, `DEFERRED` and
`CLOSED`, in either the REPOSITORY or EXTERNAL domain. Accepted, deferred and
closed entries require a disposition. Closed entries require existing evidence
paths. An issue number may remain attached after closure; reopening a real
problem does not require rewriting a checker to keep CI green.

Evidence paths are navigation to implementation, tests or receipts. Checking
that a path exists does not execute its tests or certify its contents. The
checker does not search C++ source for function names, operators or test prose.
Historical closure records describe bounded past work, not permanent guarantees
about all future commits. The former unconditional `source_state=READY` claim
has been removed. PAPER and LIVE authorization remain false.

## Release admission is a separate question

An ordinary integrity check succeeds with valid open work. A release caller can
request a particular supported profile:

```bash
python3 scripts/check_gap_register.py
python3 scripts/check_gap_register.py --release-profile core
python3 scripts/check_gap_register.py --release-profile ib-paper
```

The second/third commands reject unresolved entries whose `blocking_releases`
contains that profile. `ACCEPTED` and `DEFERRED` are not automatic waivers: their
listed release blockers still apply. Scope or disposition changes require an
honest rationale and ordinary code review, not a synthetic all-closed invariant.
The tagged core release workflow invokes the core-profile check before building.

Example of a newly discovered core release blocker:

```json
{
  "id": "EXAMPLE-001",
  "domain": "REPOSITORY",
  "state": "OPEN",
  "summary": "A regression affects the core install path",
  "evidence": [],
  "issue": null,
  "blocking_releases": ["core"],
  "disposition": ""
}
```

## External and experimental work

The register separates completed repository capabilities from still-open
external target-host rollback, actual server-side governance and target-host
operations, and from deferred product expansion. `OMS-LIFECYCLE-002` is closed
only for repository recovery/storage correctness and `RUNTIME-TELEMETRY-003`
only for the bounded source producer/report/collection/alert contract. Neither
closure is evidence of a real host deployment, provider availability, multi-day
operation or arbitrary future-schema compatibility. Broader multi-asset
portfolio/per-reason observability is tracked separately as
`RUNTIME-PORTFOLIO-004` instead of keeping a completed correctness gap open.
Refer to the machine register for current states rather than maintaining a
second handwritten all-closed checklist.

Optional IB PAPER activation still requires the independently controlled SDK,
harness, account, host isolation and an actual campaign bound to the exact
immutable artifact. `--release-profile ib-paper` cannot replace or grant that
qualification. Advancing `main` does not mutate an already admitted artifact;
changing any bound campaign input does. See [the IB module](modules/ib-paper.md)
and [the harness contract](technical/ib-paper-harness-contract.md).

## Historical closure records

The current machine register lists actionable work plus bounded repository
closures that still matter to current implementation navigation. The 18 older
CLOSED records are retained in the [exact pre-cleanup register](https://github.com/TrillionniumFoundation/heptatrader/blob/5409e0911afa405f2413d69e33d0c0df3cf1fcad/docs/gap-register.json),
not copied into another archive or revalidated as permanent HEAD certification.
No implementation, regression test or persistent-format reader is removed by
this inventory cleanup. Repository telemetry correctness and actual-host operations remain separate
scopes: the former can be CLOSED while the latter stays OPEN, and future
portfolio observability can remain DEFERRED. Closing repository OMS or telemetry
work does not claim target-host or Broker evidence. Future regression reports
receive their own issue and behavior test; successful CI remains evidence for
its own source/artifact rather than a recurring paperwork gap. There is no
requirement for every issue to be CLOSED.
