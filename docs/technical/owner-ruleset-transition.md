# Owner-operated repository governance

Status: CURRENT
Applies to: explicit repository-owner administration, not runtime authorization

## Live observation and intended minimum

An authenticated read on 2026-09-28 observed Ruleset `22597364` active on the
default branch with only `deletion` and `non_fast_forward`, no bypass actors,
required reviews or Merge Queue. That protects history but does not prevent an
owner from merging while exact-head engineering checks are missing or pending.

The intended minimum is therefore:

- retain deletion and non-fast-forward protection;
- require exactly `canonical-admission-exact-head`, bound to GitHub Actions app
  integration `15368` with strict up-to-date branch semantics;
- keep required approvals, CODEOWNER review and Merge Queue absent unless a
  genuinely independent process later justifies them.

The admission job is always event-reachable. For a documentation-only pull
request it verifies the exact source/documentation job. For code, `main` and
merge-group subjects it additionally requires exact-head Core Runtime plus GCC
and Clang sanitizer jobs, including declared workflow, job and source identity.
Missing, skipped, failed, spoofed or differently bound checks never pass.

## Application and readback

`scripts/plan_owner_ruleset.py` accepts the exact live ruleset identity and
scope, preserves unrelated rules, removes stale review/queue/status rules, and
emits one no-overwrite JSON payload. It performs no network write. Apply that
payload only after the admission workflow has merged to `main`, using repository
Administration permission, then fetch Ruleset `22597364` again and validate the
readback with `--verify-after`.

Source tests, a generated payload or an HTTP success response are not readback.
If the live ruleset identity, conditions, bypass actors or baseline protections
change, the planner rejects and requires fresh review rather than adapting
silently. This repository governance never grants Broker, PAPER or LIVE
authority.
