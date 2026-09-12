# Documentation maintenance policy

Status: CURRENT
Applies to: repository HEAD
Implementation: `scripts/check_documentation.py`
Tests: `tests/python/test_documentation_control_plane.py`, `tests/python/test_documentation_depth.py`

## Machine-verifiable facts

`module-catalog.json` is the single manually maintained module/ownership index.
Every entry has a unique ID, lifecycle status, document, implementation paths,
test paths, mutation classification and authorization value. The checker
validates those fields, existing paths, local links and capability consistency.
The module table in `index.md` is generated with:

```bash
python3 scripts/check_documentation.py --write-index
```

Normal validation never edits files. The historical development-index URL is a
navigation redirect, not another manually maintained list of modules.
`check_component_coverage.py` independently discovers tracked components,
including previously unknown top-level runtime directories. Directory ownership
is not evidence that every child component is explained or functionally tested.

## Human-reviewed technical content

A useful maintained contract explains concrete inputs/outputs, units, identities,
state transitions, durability boundaries, concurrency, failures, compatibility
and representative tests. Topics that genuinely do not apply can say so. A
scaffold should explain what is absent rather than describe a future design as
implemented. A legacy boundary document is not a claim of full legacy support.

No byte, paragraph, keyword or heading-count score establishes design quality.
Structural lint may pass a concise document; review must still determine whether
a developer can change the behavior using its examples and implementation/test
references. Known missing integration evidence belongs in the gap register.

## Change discipline

Update the owning contract when an observable interface, persistent format,
authority boundary, failure behavior or operation changes. A private rename or
behavior-preserving refactor does not require a prose-only ceremony. Update
actual regression tests when behavior changes, not comments solely to satisfy a
source-token scanner. Protocol reference tables may repeat stable wire facts,
but runtime discovery and the owning serializer remain authoritative.

EXPERIMENTAL, LEGACY and unavailable transports cannot acquire authority through
a status edit. Source, lint, CI, review, receipts and host qualification are
separate evidence domains. CTP/XT remain no-transport, IB PAPER remains externally
qualification-gated, and LIVE remains unavailable.

## CI responsibility

`documentation-control-plane-exact-head` checks source structure and ownership.
Core Runtime CI owns Python behavioral regressions, native tests, installation,
packaging and installed simulator lifecycle. GCC/Clang own independent sanitizer
builds. Preserve these nonempty job names when simplifying implementation; server
required-check configuration is not changed by this policy.
