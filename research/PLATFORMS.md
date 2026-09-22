# Offline SDK platform acceptance and legacy-consumer boundary

The canonical source remains `heptatrader/main`, continued on the existing
`integration/heptadll-modular-20260920` branch. This document extends the
post-#113 delivery boundary; it does not create another implementation line.

## Three independently qualified products

| Product | Platform boundary | Acceptance scope |
|---|---|---|
| HeptaResearch Data/Analytics/Replay/Strategy and native replay CLI | Existing Linux GCC/Clang; added native Windows x64/MSVC and macOS arm64/x86_64 AppleClang jobs | Source build, every applicable registered test, actual install/relocation and external consumer, explicit header/archive boundary |
| POSIX research import/model launchers | Linux and macOS only | Existing strict capture/parse/atomic-publication and installed-command suites; not installed on Windows |
| HeptaStrategyClient, application-key Python caller and Execution services | Existing Linux/native Unix service acceptance only | Existing independent client/package/recovery and runtime/PID1 jobs; offline success does not expand this boundary |

A job definition is not a passing result. Record the exact commit/tree, run,
compiler, architecture and `acceptance.json` from a completed run before treating
one profile as accepted. A failed, cancelled, queued or skipped job qualifies
nothing. Main-merge runs remain separate from PR-head runs. No old DLL, VS class
layout, macOS Universal archive or cross-toolchain ABI parity is inferred.

## Reproducible native builds

The additive jobs in the existing HeptaDLL Modular Integration workflow run
native AppleClang on `macos-14` arm64 and `macos-15-intel` x86_64, and native MSVC
on `windows-2022` after selecting the x64 Visual Studio developer environment.
They use Ninja, Release and strict compiler diagnostics. CMake's C++11 feature
requirement is retained; MSVC uses its supported language mode for that feature
set, not a nonexistent `/std:c++11` switch. Existing GCC and Clang sanitizer
jobs, timeouts, quotas, client tests and production installation remain intact.

`tests/research/platform_sdk_acceptance.py` rejects a mismatched host, moved or
dirty HEAD, reused build directory, missing required tests or skipped/failing
cases. It checks actual SDK metadata and packages only the existing four
archives/four headers plus offline assets. Its receipt identifies source tree,
compiler and OS, executed cases and the inner archive SHA-256. Artifacts are
experimental developer SDKs, not tagged production/broker releases.

The installed model test selects the native executable suffix. The existing
external SDK test carries the original generator/platform/toolset into the new
project and inspects CMake file-api compile/link evidence. Unlike
`compile_commands.json`, file-api works with VS and Xcode generators too.
When a compile database exists it is additionally inspected, not replaced.
Original source/build/staging paths must not leak through escaped Windows
paths; the consumer must use includes and libraries from the relocated prefix.
All existing consumer calculations, failure cases and negative component/version
checks remain active.

## Migration, not binary replacement

C01/C02 in the [consumer register](../docs/technical/heptadll-consumers.md) can
select a specifically accepted offline platform package and rebuild their
applications against the canonical headers. Native CSV/model streaming remains
available on Windows; POSIX Python import/model commands are not simulated by
empty Windows launchers. Keep unsupported callers on their recorded retained
version or perform an explicit source adaptation.

C03/C04's durable client/application support does not become Windows/macOS
service support merely because research compiles there. C05-C08 original
HRO1/JSON outboxes, wide Decimal/custom APIs, strategy/vtable/ABI and unknown
external deployments remain retained. Old library source, notices, builds and
releases stay in HeptaDLL-main; no vendor/history/data/manual is imported.

Before replacing a real legacy artifact, record its actual owner acknowledgement,
original release/digest, platform/toolchain, required API/ABI, input format,
persisted record format and golden input/intent/output comparisons. A scoped
RETAIN decision permits continued canonical development; no fictitious owner
sign-off is needed. Do not archive the original repository or discard old request
records based only on platform CI. No trading authorization is part of this work.
