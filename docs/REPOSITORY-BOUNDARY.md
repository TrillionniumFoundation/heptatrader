# HeptaTrader repository boundary

This repository is a standalone, clean snapshot of the latest local HeptaTrader
candidate.  It is intended for source review and engineering work; it does not
grant PAPER, LIVE, broker, or mutation authority.

## Snapshot provenance

- Source tree: reviewed local Round122 candidate (local filesystem path omitted)
- Source commit: `e8b67c7e3c16e3e7c9316f71756ce78c4ab4d585`
- Source version: `0.1.0-beta.1-round122`
- Source baseline manifest digest: `sha256:7d4ae21bbf4ddb248662c7687914af5fc5c98601ca9c415b58c8286cf4624f72`
- The older `HeptaTrader-master` worktree contained uncommitted local repair
  experiments.  It was left untouched and is not silently mixed into this
  reviewed snapshot.

## Included

The complete project layout is retained: native HeptaTrade code, legacy
compatibility components, adapters, policies, scripts, systemd units, tests,
release manifests, and documentation.  Provenance manifests for the reviewed
IB/CTP/prebuilt overlays are retained with the source tree.

## Excluded from the publish snapshot

The following are local or generated state and are deliberately not versioned:

- `.deploy/`, runtime/release logs, build trees, IDE caches, Python bytecode,
  and validation run logs;
- broker/session files under `Tools/Trade/*` and `Tools/Quotes/*`, including
  trade-log CSVs and `.con` connection state;
- ignored machine-local `HeptaTraderConfig.xml` files; use the checked-in
  example/paper templates instead;
- unreviewed legacy executable outputs (`Tools/Centos/*.out` and
  `Tools/MarketDataReceiver.exe`).

Vendor and prebuilt payloads covered by the metadata manifests are deliberately
omitted from this source snapshot.  The manifests remain as provenance and
content-identity records, while an operator may provide exact matching files
as a separate local overlay after reviewing the applicable licenses.  No
payload distribution authorization is asserted by this repository.

The CI gate enforces this metadata-only boundary (`--payload-mode absent`);
the CTP compatibility headers under `Interface/CTPTradeApi*` are forwarding
stubs and do not contain the vendor header payload.  A real CTP build therefore
requires an explicitly provisioned, matching overlay and remains disabled-
experimental.

## Snapshot checks

The standalone snapshot was checked with the source-baseline, CTP vendor,
prebuilt-overlay, direct-broker-path, and strict workspace-layout verifiers.
The first five boundary checks pass.  The native code-quality checker still
reports three pre-existing line-budget exceptions in the latest cancellation
fix (`ib_paper_execution_runtime_composition.cpp` at 680 lines;
`ib_paper_execution_runtime_startup.cpp` at 248 lines, with one 86-line
function).  That fix is retained because its shutdown-cancellation behavior is
covered by regression tests; it needs a separate code-quality review before a
release promotion.

## Safety status

All authorization flags in the source baseline remain false.  This repository
contains no live credentials, broker session state, order ledger, or claim of a
successful PAPER run.  Any future runtime use must go through the existing
fail-closed admission and authority checks.
