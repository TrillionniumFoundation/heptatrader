# Document Authority and Single-Truth Rules

Status: current normative
Applies to: `docs/`, repository/package README entrypoints, registries, generators, validators and evidence
Verification: `python3 scripts/generate_documentation_views.py --check` and `python3 scripts/check_documentation_control_plane.py`
Authority: documentation-governance authority

The Hepta documentation control plane contains three authoritative object classes: normative documents, machine registries and exact-revision evidence. Human-readable generated views are derived projections, not a fourth source of truth.

## Authority order

For behavioral semantics:

```text
CONSTITUTION / accepted ADR
  > versioned schema and contract
  > ModuleManifest, physical source ownership and configured build graph
  > capability registry
  > program registries and generated views
  > explanatory prose
```

For current completion state:

```text
exact-revision or protected external evidence
  > registry-derived state
  > generated Markdown view
  > PR summary, issue or discussion
```

## One canonical body

- Every topic has one canonical document or registry entry.
- Compatibility aliases, redirect Markdown, copied bodies, `docs/legacy/` and `docs/proposals/` are forbidden.
- Historical development prose, images, PDFs and dormant project/build entrypoints live only in Git history, not in `docs/` or `legacy/`.
- All files under `docs/` are registered exactly once.
- All Markdown outside `docs/` is registered as `entrypoint-only`, declares no independent authority and links to one canonical `docs/` target.
- Generated views are written only by `generate_documentation_views.py`.
- Legal texts and vendor provenance are not development documents. Legal files remain at repository root; provenance uses machine-readable JSON.

## Metadata and dynamic state

Every normative or generated Markdown file contains `Status:`, `Applies to:`, `Verification:` and `Authority:` within its first 14 lines. Normative documents cannot hard-code a mutable commit SHA, workflow result or “all gaps closed” assertion. Repository entrypoints must declare `Authority: entrypoint only`.

## Structural truth

A module statement is valid only when the formal manifest schema, physical source owner, configured CMake target/source/dependency graph and registry cross-references agree. Directory names and static target-name searches are not sufficient evidence.

## Change rule

Before adding a topic, confirm no canonical authority already exists. A registry change must regenerate every affected view. Removing or renaming a document requires removal of every registry, package, install, service, CI and code reference in the same change. A change that cannot make generators and validators pass in one tree cannot enter review.
