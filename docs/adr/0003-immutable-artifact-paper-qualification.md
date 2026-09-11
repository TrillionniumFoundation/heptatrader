# ADR 0003: Qualify immutable PAPER artifacts, not a moving branch pointer

Status: ACCEPTED  
Date: 2026-09-11  
Supersedes: the `main`-movement invalidation portion of ADR 0002

## Context

IB PAPER qualification previously required the requested SHA to equal current `main`, then repeatedly queried remote `refs/heads/main` before and after the Broker campaign. Any unrelated commit landing on `main` invalidated an in-flight qualification even though the already-built candidate artifact, executable digest, external harness, PAPER profile, Broker account, host identity and observed Broker effects had not changed.

That coupling serialized ordinary source development behind an external PAPER campaign and duplicated source-identity checks without protecting a new trading invariant. Branch names and moving refs are repository navigation state; they are not the bytes that reach the Broker.

## Decision

The workflow still admits a candidate only through an owner-authorized `workflow_dispatch` on `refs/heads/main`. The caller-supplied `candidate_sha` must equal the dispatch event's immutable `github.sha` before either self-hosted runner is allocated.

The no-secret builder then creates a content-addressed candidate from that exact revision. The qualification subject is the resulting immutable tuple:

- source commit SHA;
- builder/toolchain and SDK/BID identities;
- candidate artifact and executable digests;
- external harness digest;
- effective PAPER profile and contract binding;
- PAPER account and host fingerprints;
- required scenario/evidence set;
- kill-switch observations;
- final authoritative reconciliation state.

After that tuple is established, later movement of `refs/heads/main` does **not** invalidate the campaign. It cannot change the already selected source or artifact. A new commit is simply a different future candidate.

Any change to a bound tuple member requires a new qualification campaign. A failed or absent campaign leaves `paper_authorized=false`. LIVE remains unavailable.

## Consequences

The workflow no longer performs remote-main before/after polling or records branch-pointer receipts. It retains the controls that materially protect Broker mutation: immutable owner dispatch identity, exact source checkout, hostile artifact verification, pinned builder/SDK/harness inputs, protected PAPER environment, credential/network isolation, bounded scenario contract, kill switch, journal-before-send behavior, uncertain-outcome recovery, and final authoritative terminal reconciliation.

Ordinary development may continue while a PAPER campaign is running. Evidence remains attributable to the exact qualified artifact instead of to the transient state of a branch pointer.
