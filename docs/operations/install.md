# Installation

Status: CURRENT  
Applies to: source development and controlled host integration

## Supported source build

The canonical development target is Linux with CMake 3.16+, GCC or Clang with C++11 support, OpenSSL development headers, Python 3, and pthreads.

```bash
cmake -S . -B build/core \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build build/core --parallel 2
ctest --test-dir build/core --output-on-failure -L core
```

`./scripts/dev_core.sh` performs the supported core loop.

## Canonical core install

The SDK-free build now provides a CMake install manifest for the Tool Gateway, deterministic Execution service, command-line clients, MCP/helper programs, policy/schema files, documentation and deployment inputs. Install into a staging root before host integration:

```bash
cmake -S . -B build/install -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=OFF \
  -DHEPTA_ENABLE_IBAPI=OFF \
  -DHEPTA_BUILD_LEGACY_MONOLITH=OFF \
  -DHEPTA_BUILD_LEGACY_SIMULATOR=OFF
cmake --build build/install --parallel 2
cmake --install build/install --prefix /opt/heptatrader-stage
```

The core profile installs `hepta_tool_gatewayd`, `hepta_executiond`, `heptactl` and `hepta_sessionctl`. It deliberately does not install `hepta_ib_executiond`. Systemd and tmpfiles files are installed below the HeptaTrader data directory as reviewed deployment inputs; the package does not silently create users, credentials, authorization markers or network rules.

## Component packages

`cmake/HeptaInstall.cmake` defines `runtime`, `documentation` and `deployment` components and configures CPack TGZ and Linux DEB generators. Build auditable component archives from the configured build directory:

```bash
cd build/install
cpack -G TGZ
# On a Debian-family builder:
cpack -G DEB
```

`install-package-smoke` builds the SDK-free runtime, installs it into an empty root, verifies expected binaries and policy files, rejects an accidental IB executable or symlink, scans for obvious private-key/account material, and opens every generated archive.

A generated package is a source-delivery artifact, not PAPER authorization. Host users, groups, protected credentials, nftables state, service enablement and qualification remain explicit deployment steps.

## IB build boundary

IB integration requires a separately supplied, pinned IB C++ API source directory and an Intel Decimal Floating-Point Math Library archive. Configure `HEPTA_ENABLE_IBAPI=ON`, `IBAPI_ROOT` and `IBAPI_DECIMAL_LIBRARY` only in an isolated builder. The Intel library must use the SDK's by-value/local-rounding/local-flags ABI: `CALL_BY_REF=0 GLOBAL_RND=0 GLOBAL_FLAGS=0`. Configuration executes a native SDK/decimal interoperability probe and fails if the library is missing, incompatible or represents BID decimal values as binary floating-point bits.

For the qualifying artifact builder, package the native archive as the regular non-symlink file `libbid.a` directly inside `HEPTA_IB_BUILD_SDK_ROOT`. The builder always uses `/sdk/libbid.a` from its read-only SDK snapshot; the existing `sdk_tree_sha256` binds the archive contents along with all SDK sources. External mutable library paths are not qualifying inputs. The SDK/library and credentials are not vendored and must not be committed.

Only an explicit IB SDK profile installs `hepta_ib_executiond`, as a separate `ib-paper` component. A successful compilation or package operation does not authorize PAPER. Host identity, credential, network, kill-switch, profile, protected environment approval and qualification receipt remain required.

## Host integration boundary

A controlled deployment copies or maps the staged deployment inputs into fixed root-owned host paths, creates dedicated identities, delivers secrets through the host credential system, validates ownership and modes, applies deny-by-default broker egress, and records the installed-file digest. Package installation never disarms a kill switch or provisions an Agent session.

Rollback must use an artifact whose journal, lease, protocol and configuration schemas are explicitly compatible with the retained state. See [`rollback-backup.md`](rollback-backup.md).

## Failure semantics and verification

Missing targets, malformed version data, an unexpected broker binary in the core profile, archive traversal, unsafe installed links, secret-like material or an incomplete install tree fails package smoke validation. Before production use, independently verify artifact checksums, installed ownership, effective configuration, service identities and the relevant source/external qualification receipts.
