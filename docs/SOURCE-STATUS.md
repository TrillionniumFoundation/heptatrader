# Derived source status

Status: CURRENT  
Applies to: exact Git revisions  
Implementation: `scripts/derive_source_status.py`, `.github/workflows/source-status.yml`  
Tests: `tests/python/test_source_status.py`

## Purpose

`docs/gap-register.json` declares the repository and external predicates that matter to authorization. It is not self-authenticating proof that those predicates pass at the current revision. Source status is derived by executing the registered validators in one clean checkout, binding their outputs to the exact Git SHA, and emitting a machine-generated `heptatrader.source-status.v1` receipt.

## Derivation contract

The derivation command records the exact `HEAD`, verifies that the worktree is clean, runs documentation identity and depth checks, fresh CMake ownership verification, gap-policy validation, the canonical IB PAPER profile validator, and the source-gap closure suite. It records each command, exit code, output digest and bounded diagnostic tail. It then verifies that neither the revision nor worktree changed during validation.

Repository-controlled gaps become `VERIFIED_CLOSED` only when every source predicate succeeds in that run. A failed predicate derives `OPEN_SOURCE`; editing the register cannot override it. External gaps always remain `OPEN_EXTERNAL` because source code cannot prove live organization teams, branch rules, protected environments, runner assignments, broker credentials, TWS/Gateway state or PAPER executions.

## Receipt and authorization boundary

The receipt contains no authority to trade. `paper_authorized` and `live_authorized` remain false, and a separate authenticated external qualification receipt is required. The source-status receipt is uploaded by CI for the exact event revision and must not be committed as a reusable claim for a later revision.

A receipt includes a canonical SHA-256 digest over its complete body. Consumers must verify its schema, exact source SHA, check results and receipt digest before using it as source-correctness evidence. A successful source receipt does not imply repository governance, trusted runner assignment or broker qualification.

## Local verification

Run from a clean checkout:

```bash
python3 scripts/derive_source_status.py --output /tmp/heptatrader-source-status.json
```

A nonzero exit means at least one repository-controlled predicate is open or the derivation environment was not clean and stable.

## Failure semantics

Missing Git identity, a dirty worktree, validator timeout, nonzero predicate result, malformed gap policy, revision change or validator-created files fail the derivation. The diagnostic receipt may describe predicate failures, but no failed run can mark a repository gap closed.

## Observability and tests

CI exposes the exact SHA, receipt digest and per-predicate output digest. Unit tests prove that successful predicates close only repository-controlled gaps, failed predicates reopen them, external gaps remain open, and dirty source is rejected.

## Known limitations

The receipt proves only the checked source predicates on the runner that executed them. Supply-chain provenance, runner trust and external service state require their own attestations. The current receipt is JSON rather than a signed in-toto statement; signing can be added when the protected build and verification identities are materialized.
