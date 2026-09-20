# HeptaResearch offline SDK package

This is an experimental developer artifact, not an installed HeptaTrader
trading service, a broker qualification, or a HeptaDLL ABI replacement. It
contains the four portable research libraries and the offline replay example.
The canonical NativeStrategyClient remains a separate root-build boundary;
its header and library are intentionally absent from this package.

## Build and install

From a complete HeptaTrader checkout:

```sh
cmake -S research -B build/research-sdk -DCMAKE_BUILD_TYPE=Release
cmake --build build/research-sdk --parallel 2
ctest --test-dir build/research-sdk --output-on-failure
cmake --install build/research-sdk --prefix "$PWD/stage/research-sdk" --component ResearchSDK
```

Installation is enabled only for the standalone research build. The root
HeptaTrader build and production package manifest are unchanged. GNUInstallDirs
customizations must be relative, without parent traversal. A staged prefix may
be moved before consumption; installed CMake interfaces must not reference the
original source/build/staging directories. Set HEPTA_RESEARCH_INSTALL_SDK=OFF
for a standalone compile-only build.

## Consume from a different project

```cmake
cmake_minimum_required(VERSION 3.16)
project(ResearchConsumer LANGUAGES CXX)
find_package(HeptaResearch CONFIG REQUIRED COMPONENTS Data Analytics Replay Strategy)
add_executable(research_consumer main.cpp)
target_link_libraries(research_consumer PRIVATE HeptaResearch::Replay HeptaResearch::Strategy)
```

Configure the consumer with CMAKE_PREFIX_PATH pointing to the installed prefix.
The exported names are HeptaResearch::Data, ::Analytics, ::Replay and ::Strategy.
Use headers under hepta/research. Replay pulls in Data and Analytics transitively;
Strategy pulls in Data. No vendor SDK, OpenSSL, Gateway, execution journal or
native client is a transitive package dependency. NativeClient or any other
unsupported required component fails configuration rather than silently
providing a stub.

The replay executable takes TICKS.csv SESSIONS.csv INSTRUMENT PERIOD_US FAST SLOW
UNITS. Synthetic input examples are installed under share/HeptaResearch/examples
with the default data directory. The research account is not authoritative
broker state, and EOF cancels remainders rather than inventing liquidation.

## Version, source identity and compatibility

The repository VERSION is the sole release-label source. CMake's numeric package
version uses its major.minor.patch prefix and accepts only an exact requested
numeric version. A matching label/version is NOT a source or ABI equivalence
proof: inspect sdk-build-info.txt and HeptaResearch_SOURCE_SHA, retain the exact
commit and compiler configuration, and rebuild consumers for the selected SDK.
Source exports without Git are labelled unavailable; a dirty/unverifiable
worktree is explicitly labelled, not silently certified. No broader binary
compatibility or redistribution clearance is inferred from successful builds.

## Acceptance

The standalone CTest suite includes a real installation/relocation acceptance:
it installs into a temporary prefix, moves it, removes the original prefix,
configures/builds/runs an external C++11 consumer against all four exported
libraries, verifies transitive includes, runs the installed replay CLI, and
rejects an unsupported native component and an incompatible package version.
Compiler and sanitizer flags are carried into the consumer, so sanitizer builds
cannot accidentally test a different link closure. Tests do not connect to a
broker or change host services. Failure is not skipped.

The public integration workflow retains successful GCC SDK archives, SHA-256
checksums and exact-source metadata as CI artifacts, not tagged production
releases. A queued workflow or an existing artifact from a different commit is
not acceptance of the current source.

## Finite-price numerical boundaries

Bar-series means and same-direction research entry costs use incremental convex
updates, rather than summing divided prices or price/quantity products. Constant
finite observations must remain constant, including the largest finite double;
a representable mean must not be rejected merely because an intermediate sum
rounded above that bound. The same behavior is exercised through the moving-
average strategy and through the installed, relocated SDK's Data/Analytics APIs.

This does not relax price validation, position capacity, fill identity or true
realized-P&L/fee overflow checks. A rejected fill leaves its identity and account
state uncommitted; exact successful fill retries remain idempotent. These are
research numerical guarantees, not exchange settlement, risk approval or broker
state. The integration PR records the executed configurations and keeps local
standalone acceptance separate from canonical/root and remote qualification.
