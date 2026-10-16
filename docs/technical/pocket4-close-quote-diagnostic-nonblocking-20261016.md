# Pocket4 FX CLOSE provenance: optional quote evidence must not block exits

**2026-10-16, source-only repair.** This is not a production rollout.

## Actual defect and smallest repair

The already-merged CLOSE provenance code called `freeze_exit_trigger(..., quote)`
**before** `prepare-close`. A malformed, missing, inverted or time-skewed
diagnostic quote raised `DriverError("close trigger quote evidence invalid")`.
Thus an *optional attribution field* could suppress a previously valid
`MAX_ADVERSE_MOVE`, `MODEL_REVERSAL` or `HALT_REDUCE_ONLY` reduce-only close.

`scripts/heptatrader_paper_driver.py` now preserves the original order,
risk, session and durable `POSSIBLY_SENT` command path. For valid quote
evidence it records `quote_evidence_status=VALIDATED` and original bid/ask.
For invalid quote evidence, it records
`quote_evidence_status=UNVERIFIED_DIAGNOSTIC_OMITTED` with **no bid/ask or
observed-at claim** and proceeds with the exact original CLOSE. The
`exit_reason` is still the original driver-evaluated reason, never inferred
from a later price or mutable `last_exit_reason`.

The existing main release additionally persists bounded create-only
`close-command-receipts` for real original broker-correlated terminal closes.
That feature and its fail-open diagnostic error isolation are not modified.

## Real Pocket4 tests and candidate

- `python3 -B -m unittest tests.python.test_paper_close_diagnostic_nonblocking tests.python.test_paper_close_receipt_durability tests.python.test_paper_driver -q`: **69/69 passed** on Pocket4.
- The prior Pocket4 close-reason OMS research has 33 independent tests passed,
  but is a distinct branch; do not claim those tests were imported into this
  branch or included in the 69-test result.
- An earlier attempted *whole core* test with system Python failed because
  `torch` was absent. The ROCm venv supplied torch but lacked `cryptography`
  unless the system `/usr/lib/python3/dist-packages` is included. Neither
  import failure is an assertion about business logic. Report the corrected
  full-core result separately when it finishes.
- An entirely **inactive** pinned candidate was staged locally:
  `~/.local/share/heptatrader/decision-attribution/candidates/fx-close-reason-nonblocking-20261016-v2/EUR.USD/`.
  Manifest SHA256:
  `2ab32cf820c5127b23619fb03c61b8aa4e059c41818b47f7da1368c4a27b0b08`.
  The candidate's three source hashes and accounting-period pin passed the
  original wrapper's `load_manifest` validation. Candidate driver SHA256:
  `fa8d4d2e8a0255af62734b54285c8c90890499c25819d53e8cda9fe7d5eeead3`.
  Its manifest truthfully retains `trading_behavior_changed=true` and
  `STAGED_NOT_ACTIVE_OR_AUTHORIZED_FOR_TRADING`.
- The active EUR/USD attributed driver still pins older base SHA256
  `c1e4339f13a466759186bcf63ece5bff37b10c51157fdbd7bfe53402e0f2943d`.
  **No unit, process, trading/quote/risk policy, broker order or model changed
  during staging.** Pocket4's original main checkout retained its existing
  uncommitted three-file changes.

## Actual maintenance blocker — not bureaucratic review

The user-owned `maintenance-status` returned
`native_kill_switch_engaged=true`, `native_marker_origin=INDEPENDENT_EMPTY_NATIVE`,
`persistent_hold=false`, `may_stop_driver=false`.
The FX driver was observed flat with `pending=null`, and IB active-order
projection was empty/authoritative. These are *snapshots*, not atomic
no-new-entry or driver-stop authority. The active
`heptatrader-paper-driver.service` has `RefuseManualStop=yes` because an
earlier driver downtime actually exposed a PAPER EUR/USD position to larger
losses (see `docs/technical/risk-stop-continuity-20261015.md`).

**Do not remove `RefuseManualStop`, signal-kill the service, alter the target
dependencies, or claim a user-local maintenance hold is a broker-owner stop
permit.** Complete independent execution-owner atomic admission fencing,
authoritative flat/pending/active-order reconciliation and a continuity/rollback
mechanism before live source switching. The first new original CLOSE after
that release is the proof point for future reason coverage; all prior 361
closed episodes remain unassigned.

Scientific limit: no ex-post duration bucket or historical bar backfill
becomes proof of cost-positive live alpha. No model promotion authorized.
