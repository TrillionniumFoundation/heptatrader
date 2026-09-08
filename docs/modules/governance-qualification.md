# Governance and qualification

Status: QUALIFICATION_REQUIRED  
Applies to: repository HEAD  
Implementation: `.github/`, `scripts/verify_github_governance.py`, `scripts/verify_ib_paper_qualification.py`  
Tests: `tests/python/test_github_governance.py`, `tests/python/test_ib_paper_qualification.py`, `tests/python/test_qualification_trust_boundary.py`

## Separation of claims

HeptaTrader distinguishes four independent claims:

1. **source correctness** — code, tests, documentation, and workflows at an exact commit;
2. **repository governance** — live rulesets, reviews, teams, status checks, merge queue, and no bypass;
3. **runner/environment trust** — protected environments, selected workflows/refs, dedicated runner identities, and secret boundaries;
4. **broker qualification** — immutable candidate artifact and bounded broker-observed PAPER evidence.

Passing one claim does not imply another.

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

## Administrator scope boundary

Repository `admin` permission is not evidence of organization ownership, team membership, protected-environment control, runner-group control, secret administration, or Broker-host control. Likewise, an organization owner or repository administrator is not automatically an independent reviewer, qualification runner, Broker operator, or receipt verifier.

A connected automation may exercise only the scopes granted to its exact installation token. Increasing an automation product's local approval mode does not add GitHub App installation permissions. A `403`, missing API family, absent protected secret, offline runner, or unverified Broker control channel is an external blocker; source code must not convert it into a successful governance or PAPER claim.

## Live activation sequence

External activation is ordered. Later steps cannot substitute for earlier evidence:

1. Run all source checks on the exact pull-request head without privileged Broker credentials.
2. Materialize every mapped organization team, required member and maintainer role, and repository write-or-higher grant; require a clean CODEOWNERS service readback.
3. Protect the `repository-governance` environment with independent reviewers, self-review prevention, trusted-`main` deployment restrictions, and a separate read-only governance evidence token.
4. Install exactly one active, no-bypass default-branch ruleset implementing deletion and non-fast-forward protection, squash-only pull requests, two approvals, CODEOWNER and last-push approval, stale-review dismissal, resolved conversations, all registered PR/merge-group checks, and an `ALLGREEN` squash Merge Queue.
5. Admit a genuine source-change pull request through that queue. The resulting GitHub queue ref and successful `merge_group` checks must bind the exact reviewed head and current base.
6. Run the trusted-main governance verifier against the exact pull request and merge-group identities. Only its immutable verified receipt closes repository-governance qualification.
7. Independently protect the `ib-paper` environment and restrict distinct no-secret builder and PAPER runner identities to the exact trusted workflow/ref.
8. Build, independently verify, and execute the exact admitted artifact through the pinned external PAPER harness. Only a clean terminal Broker state and immutable verified qualification receipt may close PAPER qualification.

Direct pushes, administrator bypasses, manually authored receipts, screenshots, source-policy files, earlier-head evidence, port reachability, or a successful simulator run do not satisfy any missing step.

## Evidence lifecycle

Mutable issue bodies, comments, dashboards, and operator notes may describe progress but are not authorization evidence. Durable receipts must bind the exact Git SHA, relevant pull request and merge-group identities, workflow/job provenance, policy and configuration digests, runner or host identities, and every required terminal result. A newer source revision invalidates an earlier exact-source claim unless the applicable verifier explicitly proves compatibility.

Evidence collection must be fully paginated, provenance checked, secret free, and fail closed on missing or ambiguous data. Receipt creation is the last step of verification, not an input supplied by the candidate or operator.

## Workflow trust boundary

A privileged dispatch must originate from trusted `main`, use pinned actions, checkout trusted harness code separately from candidate data, disable persisted checkout credentials, isolate candidate builds, avoid exposing secrets to candidate code, compare pre/post candidate admission, and emit immutable receipts with exact SHA provenance.

## Fail-closed behavior

Missing checks, skipped jobs, incomplete pagination, wrong workflow/job provenance, stale review, changed candidate, ruleset mismatch, runner mismatch, receipt mismatch, or missing broker evidence is failure. An administrator bypass is not equivalent to qualification.

A gap state or authorization flag must not be changed merely to make a control-plane check pass. The external state changes only after the corresponding live objects and verifier receipt exist and are independently validated.

## Current authorization state

`production_authorized` remains false in `docs/module-catalog.json` and `docs/capabilities.json`. This status may change only after both repository source and live external controls verify at the exact candidate/merge revision. LIVE remains unavailable even if PAPER qualifies.
