# HeptaTrader

Status: current entrypoint
Applies to: repository navigation only
Verification: `python3 scripts/check_documentation_control_plane.py`
Authority: entrypoint only; canonical authority is `docs/`

HeptaTrader is a deterministic, Agent-facing trading control and execution runtime with a capability-free research/replay plane.

The single current development-document authority is [`docs/README.md`](docs/README.md). Product scope, capability maturity, architecture, module ownership, contracts, roadmap, verification and operations must be read from that graph and its machine registries; this README does not create an independent capability claim.

```bash
./scripts/dev_core.sh
```

Active runtime code is under `HeptaTrade/`; `legacy/` is quarantined inactive source and cannot provide a build, install, documentation or runtime entrypoint. Execution remains the sole venue-mutation authority. IB PAPER remains conditional on protected external qualification; CTP, XT/MiniQMT and LIVE remain unsupported/fail-closed.
