# Runtime, Research and Validation Scripts

Status: current entrypoint
Applies to: `scripts/` navigation only
Verification: `./scripts/dev_core.sh`
Authority: entrypoint only; canonical authority is `docs/development/`

The canonical developer entrypoint is [`../docs/development/LOCAL-DEVELOPMENT.md`](../docs/development/LOCAL-DEVELOPMENT.md). Module, contract and pull-request workflows are linked from the documentation index.

`scripts/dev_core.sh` is the local deterministic core gate. Validators inspect repository, documentation, schema, module, configured CMake graph, install and research boundaries. Scripts do not create product capability or override exact-revision evidence.
