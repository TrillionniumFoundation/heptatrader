# Owner-operated Ruleset transition

Status: CURRENT
Applies to: an explicit repository-owner administrative change, not runtime authorization

## What is implemented

`scripts/plan_owner_ruleset.py` consumes a fresh Ruleset read and emits a GitHub
PUT payload without contacting GitHub. It removes only `pull_request` and
`merge_queue`. Deletion and non-fast-forward protection, the four real exact-head
checks, their GitHub Actions binding and all other rules are preserved. No bypass
actor is added. A changed identity, scope, enforcement, bypass list or missing
baseline check requires renewed review rather than a permissive fallback.

Retaining real checks means direct owner updates must still satisfy the server's
check requirements; this is not a blanket exemption for untested commits. PRs can
remain a useful engineering interface without manufacturing two independent
reviewers or using Merge Queue as trading authorization. This refines the intended
operating choice in [ADR 0002](../adr/0002-owner-operated-repository.md).

## External action remains unperformed

The implementation session can read the active Ruleset but has no administrative
write action. No source commit changes server permissions. The observed Ruleset
`22597364` still requires two approvals, code-owner/last-push review and Merge
Queue until an authorized owner applies a reviewed transition. No review was
fabricated and no workflow/token workaround is used to evade this boundary.

An owner with the correct existing administrative access can retain a snapshot,
review the generated payload, re-read for concurrent changes immediately before
applying it, and verify exact server readback:

```bash
gh api repos/TrillionniumFoundation/heptatrader/rulesets/22597364 > ruleset-before.json
python3 scripts/plan_owner_ruleset.py --observed ruleset-before.json --output ruleset-owner-plan.json
# Review both files; stop if the server changed since ruleset-before.json.
# The following is the explicit administrative action, NOT executed by this tool:
gh api --method PUT repos/TrillionniumFoundation/heptatrader/rulesets/22597364 --input ruleset-owner-plan.json
gh api repos/TrillionniumFoundation/heptatrader/rulesets/22597364 > ruleset-after.json
python3 scripts/plan_owner_ruleset.py --observed ruleset-before.json --verify-after ruleset-after.json
```

Keep the before snapshot for rollback and confirm the UI/effective branch rules.
Concurrent administrative changes have no automatic merge policy in this helper.
A passing plan test or an OPEN/CLOSED issue is never proof the PUT happened. The
PAPER protected environment, runner custody, accounts, kill switch and all
runtime authorization controls are out of scope and unchanged.
