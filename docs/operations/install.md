# Installation

Status: CURRENT
Applies to: source development and controlled host integration

## Supported scope and acceptance baseline

| Surface | Maintained boundary | Not implied |
|---|---|---|
| Canonical core | local Linux Agent/Gateway/Execution simulator, explicit Unix identities and sockets; CI's Ubuntu 24.04 x86-64 disposable-host baseline | all distributions/architectures, remote Agent transport or a production-host installer |
| Simulator | fixed EUR.USD/GBP.USD CASH execution fixtures and documented risk limits | exchange microstructure, historical market replay or profitable strategy evidence |
| IB PAPER | optional separately built SDK-linked candidate; fixed qualified profile and exact artifact/account/host tuple | ordinary core deployment enabling PAPER, multiple contracts/active orders or LIVE |
| SHADOW | read-only research components and synthetic integration acceptance | installed unattended controller, verified live provider availability or execution authority |
| CTP / XT-QMT | explicitly disconnected experimental interfaces | real transport, order routing or completed broker integration |
| Legacy | retained shared compatibility material; old monolith, strategy and Pegasus products are retired | supported old build profiles, automatic migration or alternate order authority |

Use the [simulator contract](../modules/simulator.md), [IB scope](../modules/ib-paper.md)
and [SHADOW contract](../modules/shadow-research.md) for exact limits. The simulator's
10,000 admitted-order ceiling applies to retained history, not an automatically
reset daily allowance. Other bounds, including [recovery capacity](../technical/oms-recovery-capacity.md),
may stop new entry earlier. Restarting or deleting history is not a reset policy.
Unsupported features stay explicitly unavailable rather than acquiring a fake
success path merely to satisfy a module checklist.

## Supported source build

The canonical development target is Linux with CMake 3.16+, GCC or Clang with
C++11 support, OpenSSL and zlib development headers, Python 3 and pthreads.
Complete Python/source-workflow validation additionally requires PyYAML
(`python3-yaml` on the current Ubuntu lane). These are build/development inputs,
not permission to provide Broker SDKs or credentials to Agent processes.

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

`./scripts/dev_core.sh` performs the supported core loop. Python partition
ownership stays in `scripts/run_python_tests.py`; do not duplicate its lists.
Enabled historical monolith, simulator or 0DTE bridge flags now fail explicitly
at both CMake entry points. The explicit OFF values above remain accepted.

## Canonical install tree

The top-level CMake project defines one maintained install tree. Install into
an empty staging root before deployment or packaging:

```bash
stage="$(mktemp -d)"
DESTDIR="$stage" cmake --install build/core --prefix /usr
python3 scripts/check_systemd_units.py --install-root "$stage/usr" --profile core
```

The tree contains canonical daemons/CLIs, `hepta-preflight`, selected helpers,
systemd/tmpfiles assets, capability/preflight policy, build metadata and current
documentation. Examples remain non-secret templates, not effective authority.
Do not copy loose build binaries to a host. Build a deterministic package from
that tree, or use the builder's `--build-dir`; see [packaging](release-package.md).

Daemons/CLIs install under `/usr/bin`, private Python helpers under
`/usr/libexec/heptatrader`, and units under `/usr/lib/systemd/system`. Actual
executable/credential-code payloads are cross-checked. Core omits unusable
IB/policy units and IB kill-switch tmpfiles. The reporter and bounded journald
collector are installed together. Observer timer, scrape and alert examples
remain inert under `/usr/share/heptatrader/examples/systemd/monitoring`.
Provision the separate trusted observer and approve actual receiver/routing
before activation; see [collection](../technical/telemetry-collection.md) and
[reporting](../technical/oms-operational-report.md).

## IB build boundary

IB requires a separately supplied pinned C++ API and Intel Decimal Floating-
Point Math Library archive. Enable `HEPTA_ENABLE_IBAPI`, `IBAPI_ROOT` and
`IBAPI_DECIMAL_LIBRARY` only in an isolated builder. The SDK ABI is
`CALL_BY_REF=0 GLOBAL_RND=0 GLOBAL_FLAGS=0`; the native SDK/decimal probe must
pass. Missing/incompatible libraries and binary-double substitutes fail.

The qualifying builder accepts regular non-symlink `libbid.a` directly inside
`HEPTA_IB_BUILD_SDK_ROOT`, mounted read-only as `/sdk/libbid.a`. The SDK tree
digest binds source plus archive; mutable external library paths are not
qualifying inputs. SDKs and credentials must not be committed. Successful
build/install/preflight does not authorize PAPER. Host identity, credential,
network, kill switch, fixed profile, applicable environment approval and real
broker-observed qualification remain separate requirements.

## Deployment and compatibility rule

Production-like hosts install only approved content-addressed archives. Package,
installed manifest, effective non-secret configuration, static-host preflight
and applicable qualification evidence must bind the same candidate. Rebuilds
or file replacements create a new candidate.

Support is tested for an exact previous/candidate artifact pair and persisted
states, not a floating promise of arbitrary N-1 compatibility. OMS event schema,
gzip storage encoding and encrypted session layout are separate axes. Old
binaries cannot read gzip merely because the event schema is still v4; expand
stopped state with the approved newer helper before an explicitly tested
downgrade. See [rollback](rollback-backup.md) and [support window](../technical/persistence-support-window.md).
Never retire an old reader without a deployment-state inventory and migration.

The old optional products and their exclusive source/configuration graph have
been retired together. The root `Interface/` and `Tools/` trees are removed
from the active source and package; their exact historical source remains in
Git for provenance. Any consumer or licensing review applies to that
historical source, not to a supported active path. This source retirement is
not a migration of a running old deployment. Do not restore synthetic CTP/XT
success for compatibility; see [retirement](../technical/legacy-retirement.md).

The [operator walkthrough](../technical/simulator-operator-walkthrough.md) and
[real systemd acceptance](../technical/systemd-simulator-acceptance.md) describe
separate tasks. The latter requires an empty disposable VM and must not run on
an existing host installation. Actual target-host identities, collection,
notification, prior-state compatibility and multi-day operation remain external
acceptance, not consequences of a green source check.
