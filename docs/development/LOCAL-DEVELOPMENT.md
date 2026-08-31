# Local Development

Status: current
Applies to: developers changing active runtime, documents, registries, schemas or research
Verification: `./scripts/dev_core.sh`
Authority: canonical developer entrypoint

## Dependencies

A core developer environment requires CMake, a supported C++ compiler, OpenSSL development headers, Python 3 and the Python `jsonschema` package with Draft 2020-12 support. On Ubuntu:

```bash
sudo apt-get install cmake ninja-build g++ libssl-dev python3 python3-jsonschema
```

## Canonical loop

```bash
./scripts/dev_core.sh
```

The loop validates, in order:

1. deterministic generated documentation views;
2. the document registry, repository entrypoints and historical cleanup;
3. formal ModuleManifest Draft 2020-12 instances and registry cross-references;
4. physical source ownership and exact migration exceptions;
5. repository, schema/catalog and research contracts;
6. a configured CMake File API target/source/dependency graph;
7. Release core/runtime builds, core CTest and Python contract tests.

The CMake query is installed before configure and read after configure. A regex over `CMakeLists.txt` is not accepted as proof of the actual compilation graph.

Build directories must be children of repository `build/` or a controlled runner-temp directory. Never write generated state into the source tree, a broad system path or `legacy/`.

Local success is not merge, PAPER or production evidence. The unchanged exact head and merge candidate must pass their read-only CI lanes; external IB PAPER qualification remains separate.
