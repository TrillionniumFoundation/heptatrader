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

The loop runs contract binding validation, then the repository-integrity entrypoint.
Repository integrity invokes documentation control, which checks generated views,
registries, module implementation declarations and gap envelopes. This chain runs
once per top-level preflight, not once per wrapper. All standalone checker CLIs
remain available; there is no cached approval and no skip-validation flag.

Systemd and workflow/context projections, team mapping, schema/catalog, research,
module discipline and change-impact checks still follow. The configured CMake
File API graph, Release core/runtime build, core CTest and Python suites remain
required. Any failed preflight stops before compilation; a child documentation
failure must propagate to the repository checker and the shell driver.

The CMake query is installed before configure and read after configure. A regex over `CMakeLists.txt` is not accepted as proof of the actual compilation graph.

Build directories must be children of repository `build/` or a controlled runner-temp directory. Never write generated state into the source tree, a broad system path or `legacy/`.

Local success is not merge, PAPER or production evidence. The unchanged exact head and merge candidate must pass their read-only CI lanes; external IB PAPER qualification remains separate.

## Validation behavior and direct regressions

`hepta_document_metadata.missing_metadata` defines the shared four-field header
contract: Status, Applies to, Verification and Authority must have nonempty values
within the first 14 lines. Both document and repository validators use this
function; a field on line 13 or 14 no longer disagrees between the two lanes.

`check_module_implementation_evidence.validate(root)` keeps the selected root local
to that call. Sequential or parallel validation of another tree does not change
the default root. Strings are validated before deduplication or enum lookup, so
malformed scope/gate/state/budget inputs return diagnostics rather than unhashable
container exceptions. Empty registry objects, invalid gap collections, duplicate
gap IDs, root-only evidence and unsafe manifest paths reject. This remains a
structural declaration check, not behavioral coverage or receipt authentication.

Workflow finalizer-name lint requires an invocation-shaped command. A bare word
in a report name or `echo "finalize"` is not mutation authority. Known finalizer
invocations, write permissions and the existing explicit mutation patterns remain
rejected. This is limited static lint, not complete YAML/shell interpretation or
a sandbox; trusted workflows, least privileges and independent review still apply.

The focused regression command is:

```bash
python3 -m unittest discover -s tests/python -p 'test_development_validation.py'
```

The tests execute real validator functions on temporary fixtures. Shell-driver
tests deliberately substitute tool endpoints to count calls and inject failure;
they do not establish successful CMake, canonical graph, broker or deployment
execution. Run the unchanged complete canonical loop for integration evidence.
