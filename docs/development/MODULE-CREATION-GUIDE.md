# Module Creation Guide

Status: current normative
Applies to: new current, experimental, planned or unsupported modules
Verification: ModuleManifest Draft 2020-12 validation, physical ownership and configured CMake graph checks
Authority: module onboarding workflow

Create a module in this order:

```text
authority and failure domain
-> stable ID/version/lifecycle/trust domain
-> provided and consumed contracts
-> state writer and consistency model
-> concurrency/shard/backpressure rules
-> numeric and resource budgets
-> DRI, backup and cross-domain reviewers
-> physical source owner and CMake target owner
-> module-specific technical documentation profile and guide
-> negative/fault/performance tests
-> capability and milestone mapping
```

Do not create a directory first and invent its boundary later. Pure policy cannot depend on venue, session or credential code; an untrusted strategy cannot depend on Execution internals; a venue adapter performs transport and event normalization only; Management cannot possess broker authority.

## Required changes

A module change must update, as applicable:

- one manifest under `docs/modules/manifests/`;
- `module-registry-v2.json`;
- one unique entry in `module-documentation-profiles-v1.json` and its generated technical guide;
- `source-ownership-registry-v1.json` for every new active C/C++ path;
- the canonical contract registry and schema;
- the verification, capability, gap and milestone registries;
- generated views through `generate_documentation_views.py --write`.

A new current target must have one ModuleManifest owner and must appear in the configured CMake File API graph. A source must have one physical owner. Shared claims or cross-module compilation require an exact exception whose complete participant set, physical owner, open gap, exit milestone and deletion condition are recorded. Tests link public targets; any temporary direct `.cpp` compilation must be enumerated exactly and removed before its gap closes.
