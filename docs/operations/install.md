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

## IB build boundary

IB integration requires a separately supplied, pinned IB C++ API source directory. Configure `HEPTA_ENABLE_IBAPI=ON` and `IBAPI_ROOT` only in an isolated builder. The SDK and credentials are not vendored and must not be committed.

A successful compilation does not authorize PAPER. Host identity, credential, network, kill-switch, profile, environment approval, and qualification receipt remain required.

## Installation tree

The source build produces binaries but does not currently provide a complete canonical install/package target. A controlled deployment must place binaries, scripts, units, policy JSON, and configuration at fixed root-owned paths, record content digests, and verify ownership/modes before startup.

Do not infer that a path mentioned in a plugin or module document is installed merely because the source file exists.
