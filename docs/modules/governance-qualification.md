# Governance and qualification

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `.github/`, `scripts/verify_github_governance.py`, `scripts/verify_ib_paper_qualification.py`  
Tests: `tests/python/test_github_governance.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`

## Governance contract and separation of claims

HeptaTrader distinguishes four independent claims:

1. **source correctness** — code, tests, documentation, and workflows at an exact commit;
2. **repository governance** — live rulesets, reviews, teams, status checks, merge queue, and no bypass;
3. **runner/environment trust** — protected environments, selected workflows/refs, dedicated runner identities, and secret boundaries;
4. **broker qualification** — immutable candidate artifact and bounded broker-observed PAPER evidence.

Passing one claim does not imply another. Every receipt binds its schema, exact source or artifact identity, verifier implementation, observed control-plane state and result. A source-controlled policy describes the required predicate; only live readback or broker evidence can satisfy an external predicate.

## Repository-controlled source

The repository defines desired governance policy, required check contexts, CODEOWNERS/team mappings, trusted verifiers, hostile tests, candidate builder, and PAPER qualification workflow. Privileged workflows run trusted code from the default branch and treat reviewed candidate source/artifacts as untrusted data.

Core and documentation workflows provide ordinary engineering checks. Qualification workflows must not replace those checks or make routine feature development depend on broker credentials.

## External controls

The following cannot be created or proved by a source commit alone:

- organization teams and membership/maintainer assignments;
- active no-bypass branch/ruleset enforcement;
- repository grants and CODEOWNERS service readback;
- protected environments and independent reviewers;
- runner-group selected-workflow and trusted-ref restrictions;
- dedicated live builder/PAPER host assignment;
- IB SDK, credentials, account, TWS/Gateway, and network state;
- genuine merge-group and broker-observed receipts.

Verifiers query live GitHub or broker evidence and leave the gate failed when any item is absent.

## Workflow trust boundary

A privileged dispatch must originate from trusted `main`, use pinned actions, checkout trusted harness code separately from candidate data, disable persisted checkout credentials, isolate candidate builds, avoid exposing secrets to candidate code, compare pre/post candidate admission, and emit immutable receipts with exact SHA provenance.

## State and evidence lifecycle

Repository policy, exact-source CI, live governance readback, builder attestation, PAPER campaign evidence and final qualification receipt are separate states. A failed or missing stage cannot be replaced by a manually edited status file. Receipts are immutable observations for one exact candidate; source changes invalidate prior admission and require the applicable stages to run again.

Source-level gap status is derived by running the registered validators against the checked-out SHA. External gaps remain open until their verifier consumes authenticated live data and writes a receipt whose digest and candidate identity validate. Closing an issue, changing a label or editing `production_authorized` is not evidence.

## Fail-closed behavior

Missing checks, skipped jobs, incomplete pagination, wrong workflow/job provenance, stale review, changed candidate, ruleset mismatch, runner mismatch, receipt mismatch, or missing broker evidence is failure. An administrator bypass is not equivalent to qualification.

## Qualification verification and observability

Verification output must expose the exact candidate SHA, workflow and job provenance, ruleset/environment/runner identities, receipt schema and digest, and every failed predicate without secrets. Qualification workflows retain immutable artifacts for incident reconstruction. Ordinary source CI must remain independently runnable without protected broker credentials.

Tests cover hostile ruleset scopes, missing or forged checks, changed candidates, archive traversal and symlink attacks, wrong runner/environment identity, mismatched profiles and receipts, and source-only attempts to authorize PAPER or LIVE.

## Current authorization state

`production_authorized` remains false in `docs/module-catalog.json` and `docs/capabilities.json`. This status may change only after both repository source and live external controls verify at the exact candidate/merge revision. LIVE remains unavailable even if PAPER qualifies.
