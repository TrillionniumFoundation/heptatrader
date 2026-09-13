# Clock, bounded load and offline restore acceptance

Status: CURRENT
Implementation: `tests/simulator_risk_runtime_tests.cpp`, `tests/python/runtime_acceptance_support.py`, `tests/python/test_installed_runtime_processes.py`
Tests: `tests/python/test_runtime_acceptance_support.py`
Scope: broker-disabled simulator fixtures; no deployment or trading authorization

## Deterministic expiry versus real scheduling

The IPC simulator test previously gave its quote a 100 ms lifetime and assumed
that instrumentation, filesystem synchronization and OS scheduling would fit
inside that window. A scheduler delay can correctly produce
`AUTHORITATIVE_QUOTE_STALE`; accepting the quote anyway would be a risk defect.
The test now separates two independent obligations instead of weakening the
runtime check or repeatedly submitting the mutation.

An injected venue clock tests the inclusive 100 ms boundary, rejection at
101 ms, an expired preview before final admission, and a stale quote that
cannot produce an economic fill. A new valid observation permits the original
accepted order to fill exactly once. The real IPC test retains the production
TTL, observes two actual quote timestamp advances with bounded read-only
polling, and still exercises admission, automatic events, cancellation, journal
replay, duplicate fill handling, persisted budgets and conflicting replay.
No production clock, default TTL, risk policy or retry behavior changes.

Run the core tests as a non-root user so the IPC identity scenario is exercised.
For an explicit repeatability check, use the same compiled sanitizer binary:

```sh
ctest --test-dir build/reliability-gcc --output-on-failure \
  -R '^hepta_simulator_risk_runtime_tests$' --repeat until-fail:30
```

Use the equivalent Clang build separately. `until-fail` is a predetermined
stress count that stops on failure, not a retry-until-green policy. Preserve
failed logs and compiler/configuration identity.

## Bounded process measurements

The existing [installed process lane](installed-process-acceptance.md) owns the
new load and restore scenarios; no extra workflow or authorization gate is
introduced. `HEPTA_SOAK_SAMPLES` selects 128 through 2000 quote reads (default
256). The fixture warms up twenty calls, measures each actual installed CLI
invocation, and rejects non-authoritative or stale results. It never retries a
mutation to improve the measurements.

The output includes sample count, elapsed time, completed calls per second,
nearest-rank p50/p95/p99, p99.9 only with at least 1000 observations, and maximum
latency. The measurement boundary includes CLI process startup, authentication,
IPC and response decoding. It is not exchange latency, venue-send latency or a
production SLO. PID-bound RSS, peak RSS, threads and descriptor counts are read
as the corresponding service UID. Descriptors must return to the warmed idle
baseline within a bounded drain; RSS is reported, not misrepresented as a
long-duration memory-leak proof.

Four further cycles preserve a filled position across clean service restart,
recover the same command/lease without re-provision, check duplicate no-resend,
and exit to authoritative flatness. Exactly eight sends are expected. Evidence
binds the candidate source and archive digest. CI's default sample count does
not report p99.9; a disposable local run may select 1000 explicitly.

## Offline checkpoint fixture

The checkpoint helper is a test utility, not an installed backup command. It
requires every fixture process to be stopped and bounds the snapshot to 64
regular single-link files and 16 MiB. Content, size, mode, UID/GID and SHA-256
are checked. Symlinks, hard links, FIFOs, namespace replacement, path traversal,
duplicate keys/paths, oversized data, corruption and wrong service identities
are rejected. All entries are validated before writes to a new private fixture.
An existing state tree is never adopted or overwritten.

The scenario saves the actual signed lease/key and OMS journal with a filled
25-unit position. The restored installed processes recover the original lease,
position and command without provisioning; replaying the same command does
not send again, and a single guarded exit reaches zero. Private checkpoint
bytes and fixture keys stay in TemporaryDirectory and are never uploaded;
exported evidence contains only counts and digests. The trust scope is our
stopped, exclusively owned disposable fixture, not a concurrently writable
production filesystem or an arbitrary untrusted backup distribution service.

## Evidence limits

These tests establish bounded samples and the exact tested artifact pair.
They do not certify a 24-hour soak, every persistent-schema transition, rollback
with an unresolved external order, another deployment host, or IB PAPER.
Production acceptance still requires its actual previous/candidate artifacts,
protected state custody and host-specific evidence. Keep `HOST-ROLLBACK-001`
open for that deployment-specific work; neither passing tests nor this document
creates Broker authority. PAPER/LIVE defaults remain unchanged.
