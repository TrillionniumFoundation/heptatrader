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

The register explicitly retains missing complete SHADOW integration evidence
and target-host/prior-version rollback evidence. These are not claims that the
core package cannot be built. Equally, a same-artifact pointer rollback smoke
cannot be represented as a prior-version journal/lease migration test.

Optional IB PAPER activation still requires the independently controlled SDK,
harness, account, host isolation and an actual campaign bound to the exact
immutable artifact. `--release-profile ib-paper` cannot replace or grant that
qualification. Advancing `main` does not mutate an already admitted artifact;
changing any bound campaign input does. See [the IB module](modules/ib-paper.md)
and [the harness contract](technical/ib-paper-harness-contract.md).
