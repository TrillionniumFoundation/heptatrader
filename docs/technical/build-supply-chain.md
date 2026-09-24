# Build and supply-chain technical reference

Status: CURRENT  
Applies to: CMake, build ownership, vendored compatibility inputs, IB SDK inputs, install rules, and deterministic packaging

## Canonical profiles

The maintained Linux core profile disables the legacy monolith, legacy simulator, IB API, IB probe, and deprecated bridge. The IB profile is a separately supplied, pinned SDK build and remains qualification-gated. Build ownership is derived from the selected live CMake File API model and `docs/module-catalog.json`; there is no checked-in expansion of every CMake target/dependency/translation unit.

## Inputs

Repository source, compiler/toolchain, CMake options, source SHA, source epoch, release label, and external SDK tree are explicit inputs. Credentials and host-generated authorization state are never build inputs. Vendored compatibility material has an owner and version/manifest but does not imply runtime support.

## Outputs

A tested build produces one canonical install tree. Packaging snapshots regular single-link files from pinned directory descriptors, normalizes metadata, writes a manifest, and publishes package/checksum/receipt without replacement. The package digest is the deployment identity.

## Drift controls

A new CMake target may use already-owned sources without a metadata ceremony. A new implementation translation unit must have one canonical module owner, and every tracked owned C/C++ source must be reachable from the selected live CMake graph or explicitly declared `unbuilt`. A new Git-tracked production path fails component coverage until assigned to a documented module. A new install file is included in the package manifest and installed-tree comparison; a local rebuild creates a new candidate.

## Reproducibility and limitations

Equal source and identity inputs are expected to reproduce package bytes on the supported builder. Compiler and system-library reproducibility outside that builder is not implied. IB SDK and Intel decimal inputs are external, pinned, read-only qualification inputs and are not vendored.
