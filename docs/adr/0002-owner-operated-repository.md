# ADR 0002: Owner-operated repository model

Status: ACCEPTED
Date: 2026-09-09
Qualification identity update: ADR 0003 supersedes the moving-`main` invalidation rule below.

## Context

HeptaTrader is a self-use trading system operated by one owner. The former multi-team governance model made organization teams, CODEOWNERS, two approvals, branch rulesets, Merge Queue, protected governance environments, and a separate governance receipt prerequisites for ordinary development.

Those controls do not match the actual ownership model. They also do not mitigate the principal trading risks: wrong source identity, unpinned build inputs, credential exposure, unsafe Broker mutation, a missing kill switch, uncertain order outcomes, or unreconciled terminal state.

## Decision

The intended repository operating model is owner-operated. This decision is
policy intent, not a statement that hosted Rulesets have already been removed.
The permissions below are desired operating choices and remain subject to the
actual server-side rules until the owner changes them explicitly.

- The owner may commit, merge, revert, tag, or release directly.
- Pull requests, reviews, CI checks, and branch settings are optional engineering aids.
- There is no team, CODEOWNERS, approval-count, Merge Queue, protected-governance-environment, or governance-receipt requirement.
- Historical `G-TEAM-001` is retired as `NOT_APPLICABLE` and removed from the active gap register.
- No repository event by itself authorizes Broker mutation.
- A real IB PAPER campaign is admitted only when the workflow-dispatch actor and rerun triggering actor are `ProfHepta`, the immutable GitHub account ID is `102159240`, and the requested SHA is the exact `main` revision selected by that dispatch; these job-level gates are evaluated before either self-hosted runner is allocated.

IB PAPER remains independently qualification-gated. Qualification is an optional activation prerequisite rather than an unresolved project gap. The original version of this ADR also required `main` to remain unchanged throughout the campaign. ADR 0003 replaces only that identity rule: once the exact dispatch revision has been converted into a verified immutable candidate artifact, later movement of `main` does not change the candidate and does not invalidate its campaign. Both trusted and candidate checkouts remain exact-index verified, and qualification still binds immutable SDK/BID and builder inputs, the exact artifact, a separately pinned external harness, PAPER-only account mode, the complete bounded Broker scenario set, the operator kill switch, and authoritative terminal reconciliation.

LIVE remains unavailable.

## Consequences

The intended simplified model does not require organization-administration
APIs or artificial reviewer identities. Source changes cannot enact that server
configuration, and no review identity or successful merge may be fabricated. The owner accepts repository-change risk and may use CI or review when useful. Execution authority, journal-before-send, risk checks, credential isolation, artifact integrity, kill-switch behavior, uncertain-outcome recovery, PAPER-only scope, and final Broker reconciliation remain unchanged.

For the current qualification identity model, see [`0003-immutable-artifact-paper-qualification.md`](0003-immutable-artifact-paper-qualification.md).

## Observed implementation gap (2026-09-13)

The live Ruleset read during the audit, ID `22597364`, remained active with two
approvals, code-owner/last-push review requirements and Merge Queue. It retained
the real core, documentation and GCC/Clang required contexts. This conflicts
with the optional-review/direct-owner intent above. No server rule was changed
by the cleanup. Until an explicit owner decision and actual administrative
change, contributors must follow the enforced settings. This observation is a
dated fact, not a permanent new source gate. Record a later server decision here
rather than asserting that editing an ADR changed GitHub permissions.
