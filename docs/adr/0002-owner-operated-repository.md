# ADR 0002: Owner-operated repository model

Status: ACCEPTED
Date: 2026-09-09

## Context

HeptaTrader is a self-use trading system operated by one owner. The former multi-team governance model made organization teams, CODEOWNERS, two approvals, branch rulesets, Merge Queue, protected governance environments, and a separate governance receipt prerequisites for ordinary development.

Those controls do not match the actual ownership model. They also do not mitigate the principal trading risks: wrong source identity, unpinned build inputs, credential exposure, unsafe Broker mutation, a missing kill switch, uncertain order outcomes, or unreconciled terminal state.

## Decision

The repository is owner-operated.

- The owner may commit, merge, revert, tag, or release directly.
- Pull requests, reviews, CI checks, and branch settings are optional engineering aids.
- There is no team, CODEOWNERS, approval-count, Merge Queue, protected-governance-environment, or governance-receipt requirement.
- Historical `G-TEAM-001` is retired as `NOT_APPLICABLE` and removed from the active gap register.
- No repository event by itself authorizes Broker mutation.
- A real IB PAPER campaign is admitted only when the workflow-dispatch actor and rerun triggering actor are `ProfHepta`, the immutable GitHub account ID is `102159240`, and the requested SHA is exact current `main`; these job-level gates are evaluated before either self-hosted runner is allocated.

IB PAPER remains independently qualification-gated. Qualification is an optional activation prerequisite rather than an unresolved project gap. A valid qualification binds the exact current `main` SHA, immutable SDK/BID and builder inputs, the exact artifact, a separately pinned external harness, PAPER-only account mode, the complete bounded Broker scenario set, the operator kill switch, and authoritative terminal reconciliation. Movement of `main` invalidates an in-flight qualification. Both trusted and candidate checkouts are verified against their stage-zero Git index, tracked bytes, modes, links, untracked files, and ignored files before use; the checks are repeated after the build and Broker campaign.

LIVE remains unavailable.

## Consequences

Routine development and release no longer depend on organization-administration APIs or artificial reviewer identities. The owner accepts repository-change risk and may use CI or review when useful. Execution authority, journal-before-send, risk checks, credential isolation, artifact integrity, kill-switch behavior, uncertain-outcome recovery, PAPER-only scope, and final Broker reconciliation remain unchanged.
