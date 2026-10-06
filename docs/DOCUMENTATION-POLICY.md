# Documentation maintenance

Update the owning contract when observable interfaces, persistent formats,
authority boundaries or failure behavior change. A private rename or internal
refactor does not require synchronized status tables or rewritten file lists.

`python3 scripts/check_documentation.py` checks local navigation links. It does
not enforce module-header templates, generated indexes, keywords, paragraph
counts or an arbitrary document size. `docs/index.md` is an ordinary maintained
navigation page; `module-catalog.json` remains a directory ownership map used by
Git/CMake source checks, not a second feature-completion register.

Useful technical documents explain inputs, units, identities, state transitions,
durability, failures and representative tests. Implemented, installed, qualified
and experimentally profitable are different states; neither a label nor a code
merge establishes the latter states.

Historical HeptaDLL records are frozen provenance. Do not reopen retired runtime
or ABI branches to update their dates. Corrections and broken-link repairs are
allowed; new work belongs to current main. See [main convergence](technical/main-convergence-20261006.md).

Ordinary CI executes actual native and Python tests once. Deep sanitizers and SDK
relocation are manual workflows; release and Broker qualification keep their
separate acceptance requirements. Exact run outcomes belong in run receipts,
not copied into every current module page.
