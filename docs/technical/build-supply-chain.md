# Build and supply-chain technical reference

Status: CURRENT  
Applies to: CMake, build ownership, vendored compatibility inputs, IB SDK inputs, install rules, and deterministic packaging

## Canonical profiles

The maintained Linux core profile disables the legacy monolith, legacy simulator, IB API, IB probe, and deprecated bridge. The IB profile is a separately supplied, pinned SDK build and remains qualification-gated. CMake File API output is compared with `docs/build-targets.json`; implementation translation units have one module owner.

## Inputs

Repository source, compiler/toolchain, CMake options, source SHA, source epoch, release label, and external SDK tree are explicit inputs. Credentials and host-generated authorization state are never build inputs. Vendored compatibility material has an owner and version/manifest but does not imply runtime support.

## Outputs

A tested build produces one canonical install tree. Packaging snapshots regular single-link files from pinned directory descriptors, normalizes metadata, writes a manifest, and publishes package/checksum/receipt without replacement. The package digest is the deployment identity.

## Drift controls

A new CMake target or implementation translation unit fails build-ownership validation until inventoried. A new Git-tracked production path fails component coverage until assigned to a documented module. A new install file is included in the package manifest and installed-tree comparison; a local rebuild creates a new candidate.

## Reproducibility and limitations

Equal source and identity inputs are expected to reproduce package bytes on the supported builder. Compiler and system-library reproducibility outside that builder is not implied. IB SDK and Intel decimal inputs are external, pinned, read-only qualification inputs and are not vendored.
