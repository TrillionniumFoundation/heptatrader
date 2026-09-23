# Owner-operated repository governance

Status: CURRENT
Applies to: explicit repository-owner administration, not runtime authorization

## Policy and live observation are different

Repository operation does not require manufactured independent reviewers or a
merge queue. Deletion and non-fast-forward protection should remain enabled.
CI results certify their own exact source/artifact; they do not authorize trading.
[ADR 0002](../adr/0002-owner-operated-repository.md) records the operating intent.

A read on 2026-09-23 observed Ruleset `22597364` with only `deletion` and
`non_fast_forward`. Effective rules for `main` matched. The former claim that
two approvals, Code Owner review and Merge Queue were still mandatory is obsolete;
`OWNER-RULESET-002` was removed from the actionable gap register. This is a dated
observation, not a promise about future hosted configuration. No server setting
was changed by this source cleanup.

Read current facts before any administrative operation:

```bash
gh api repos/TrillionniumFoundation/heptatrader/rulesets/22597364
gh api repos/TrillionniumFoundation/heptatrader/rules/branches/main
```

## Historical migration helper

`scripts/plan_owner_ruleset.py` remains a narrowly scoped, offline migration
helper for the older four-required-check baseline. It is not a recurring gate,
not a valid plan for arbitrary newer rulesets, and not evidence that a PUT ran.
It must reject an unsupported baseline instead of inventing permission. Its
historical tests do not require restoring the old review/queue restrictions.

Temporary tool access in a development session is not a persistent product gap.
Changes to hosted permissions require a fresh read, explicit owner authority,
a reviewed change and post-change readback. PAPER environment custody, account,
kill switch, credentials and runtime authorization remain separate and unchanged.
