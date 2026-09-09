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

## Canonical install tree

The top-level CMake project defines one install tree for the maintained runtime. Install into an empty staging root before deployment or packaging:

```bash
rm -rf stage/core
DESTDIR="$PWD/stage/core" cmake --install build/core --prefix /usr
```

The resulting tree contains the canonical daemons and CLIs, `hepta-preflight`, selected runtime helpers, systemd and tmpfiles assets, capability/preflight policy, installed build metadata and current documentation. Example configuration remains under the package share directory and is never treated as an effective secret-bearing configuration.

Do not copy loose build-directory binaries to a host. Build a deterministic package from the staging tree or let `scripts/build_release_package.py --build-dir` create the staging tree itself. See [`release-package.md`](release-package.md).

## IB build boundary

IB integration requires a separately supplied, pinned IB C++ API source directory and an Intel Decimal Floating-Point Math Library archive. Configure `HEPTA_ENABLE_IBAPI=ON`, `IBAPI_ROOT` and `IBAPI_DECIMAL_LIBRARY` only in an isolated builder. The Intel library must use the SDK's by-value/local-rounding/local-flags ABI: `CALL_BY_REF=0 GLOBAL_RND=0 GLOBAL_FLAGS=0`. Configuration executes a native SDK/decimal interoperability probe and fails if the library is missing, incompatible or represents BID decimal values as binary floating-point bits.

For the qualifying artifact builder, package the native archive as the regular non-symlink file `libbid.a` directly inside `HEPTA_IB_BUILD_SDK_ROOT`. The builder always uses `/sdk/libbid.a` from its read-only SDK snapshot; the existing `sdk_tree_sha256` binds the archive contents along with all SDK sources. External mutable library paths are not qualifying inputs. The SDK/library and credentials are not vendored and must not be committed.

A successful compilation, installation, package build or preflight does not authorize PAPER. Host identity, credential, network, kill-switch, profile, environment approval and broker-observed qualification receipt remain required.

## Deployment rule

Production-like hosts install only an approved, content-addressed archive. The approved digest, extracted installed-file manifest, effective non-secret configuration digest, static-host preflight receipt and later qualification receipt must refer to the same release candidate. Any local rebuild or file replacement creates a new candidate.
