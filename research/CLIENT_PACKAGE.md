# HeptaStrategyClient developer SDK

This package makes the existing forward-only `NativeStrategyClient` consumable
outside the source tree. It is separate from the four-library offline
`HeptaResearch` package. It exports the **same three canonical root-build
archives**, not a copied client implementation, second runtime or broker bridge.
It does not grant access to a session, account, network or trading environment.

## Build, install and consume

Build from a complete checkout (Linux/local Unix transport):

```sh
cmake -S . -B build/client-sdk -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DHEPTA_ENABLE_IBAPI=OFF
cmake --build build/client-sdk --target hepta_research_native_client --parallel 2
cmake --install build/client-sdk --prefix "$PWD/stage/client-sdk" --component StrategyClientSDK
ctest --test-dir build/client-sdk -R '^hepta_research_native_sdk_install$' --no-tests=error --output-on-failure
```

In a different project:

```cmake
cmake_minimum_required(VERSION 3.16)
project(MyStrategy LANGUAGES CXX)
find_package(HeptaStrategyClient CONFIG REQUIRED COMPONENTS Client)
add_executable(my_strategy main.cpp)
set_target_properties(my_strategy PROPERTIES CXX_STANDARD 11 CXX_STANDARD_REQUIRED ON CXX_EXTENSIONS OFF)
target_link_libraries(my_strategy PRIVATE HeptaStrategyClient::Client)
```

Set `CMAKE_PREFIX_PATH` to the installed prefix, which can be relocated.
`#include <hepta/research/native_strategy_client.h>` is the entry header.
`Client` links `NativeToolClient`, `ToolProtocol` and the platform threading
contract transitively. There is no Gateway, Execution, OMS, OpenSSL or vendor
SDK library dependency. Eight explicit public headers contain request/result
values, normalized instrument/order data, discovery and client APIs; the
privileged host, session binding, registry and execution-authority declarations
are not in this header closure. Existing wire values/layouts are unchanged.

## Execution boundary

Construct a longer-lived `NativeToolClient` with the existing session token-file
and local socket configuration, then borrow it with `NativeStrategyClient`.
The source client lifetime must exceed the wrapper lifetime. Construction from
temporaries is rejected. Never place tokens in source or package metadata.

`PreparedOrder` supports the existing LMT/DAY profile; unsupported contract
fields and historical auto-open/close semantics are rejected, not translated
into a different trade. Preview through the real Gateway. Persist the proposal,
Execution-issued command ID and permit before submission; an uncertain reply
requires status inspection or the exact same identity, not a new mutation ID.
A true transport result is not an accepted order. Cancellation and authoritative
flatten retain their existing ownership and preview rules. No package API makes
research positions, quotes or fills authoritative. The wrapper does not retry
mutations automatically or connect to a broker.

LIVE remains unavailable; CTP remains deferred, and XT retains its priority.
This SDK is not a historical HeptaDLL source/ABI replacement, strategy migration
certificate, host deployment or broker qualification.

## Packaging and source identity

`StrategyClientSDK` is an explicit developer install component. Every one of its
install rules is `EXCLUDE_FROM_ALL`: ordinary root installation and production
release contents remain unchanged. The standalone offline SDK still rejects a
required NativeClient component; callers opt into this **separate** package.
No service, account configuration, vendor binary, private history or market
sample is included. Public integration authorization does not establish rights
for a later literal import from the historical repository.

The root repository VERSION owns the release label. Numeric package versions
require an exact match. `strategy-client-build-info.txt` and CMake variables
record source SHA and clean/dirty/unavailable provenance; the build configuration
and toolchain must be retained. They do not promise cross-compiler or future ABI
compatibility. Rebuild consumers for the selected package and qualify it with the
matching Gateway/Execution deployment.

## Executable acceptance

The root research CTest lane installs only this component, relocates it, removes
the original prefix, copies the existing native-client behavioral test as an
external C++11 consumer, and compiles/runs it against the installed archives.
Compiler/sanitizer flags propagate to that consumer. The test checks the actual
archive symbols, exact header/asset closure, dependency targets, unavailable
components/version rejection and negative compilation of privileged classes.
It also executes the generated research install script **without** a component
and requires that no SDK file is installed. Canonical root install/package tests
independently retain their complete production-namespace checks.

The existing real Gateway/Execution and SIGKILL recovery tests remain separate
and unchanged. Installed client linkage/behavior is not evidence of a broker
connection, host isolation, remote CI completion or LIVE approval.
