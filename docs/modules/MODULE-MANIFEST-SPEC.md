# ModuleManifest V3 Specification

Status: current normative
Applies to: all current, experimental, planned, unsupported and deprecated modules
Verification: Draft 2020-12 validation, source ownership validation and configured CMake File API graph validation
Authority: module manifest contract

A module is an authority, state, failure and concurrency boundary—not merely a directory. Every module manifest is validated against `module-manifest-schema-v3.json` before semantic checks run.

## Required identity and lifecycle

Each manifest declares:

```text
schema = heptatrader.module-manifest.v3
stable module id
semantic version
lifecycle
kind and trust domain
authority statement
exclusive or shared-migration ownership
```

`shared-migration` requires an open `migration_gap`. An exclusive module must not carry a migration gap. Unknown fields, malformed nested objects, duplicate arrays, unsafe paths and invalid lifecycle/ownership values are rejected by the schema.

## Required engineering contracts

Every manifest declares:

```text
source roots and CMake targets
provided and consumed contract IDs
allowed and forbidden module dependencies
state model / persistence / writer
concurrency / shard / blocking-I/O / cross-module-lock policy
backpressure and overflow behavior
risk-increase and safe-exit failure behavior
resource budget
DRI / backup / cross-domain reviewers
verification check IDs
technical-guide path and complete coverage-topic set
```

All contract, module and verification IDs must resolve to canonical registries. Current and experimental CMake targets must exist in the configured or explicitly optional profile.

## Physical source ownership

Manifest claims are not the physical ownership authority. The exact owner of every active C/C++ file is defined in `source-ownership-registry-v1.json`. Multiple manifest claims are permitted only through a bounded exception whose participant set exactly matches the observed owners and whose physical owner, gap, milestone and exit condition are explicit.

The configured CMake graph is read through the File API. A source compiled by a target owned by another module requires an exact `(target, source)` migration exception. Tests that directly compile production sources are governed by the same rule. No wildcard exception or “any owner marked shared” shortcut is allowed.

## Completion rule

A module boundary is complete only when:

1. the manifest passes the formal schema;
2. all source files have one physical owner;
3. the configured target/source/dependency graph matches the registries;
4. no unregistered cross-module compilation remains;
5. all migration exceptions linked to the module are removed or remain attached to one open gap;
6. the module has one unique generated technical guide covering every required engineering topic;
7. documentation profiles, manifests, generated guides and canonical registries are byte-consistent;
8. module-local, documentation and system verification pass on the same unchanged revision.
