# Legacy Pegasus market-data CSV format

Status: LEGACY  
Applies to: default-disabled top-level `HeptaSimulator/`

The historical Pegasus simulator accepts a CSV-like market-data format and XML index. It is retained only for legacy compatibility and is not the canonical deterministic Execution venue.

The current simulator lives under `HeptaTrade/simulator/` and is documented in [`modules/simulator.md`](modules/simulator.md). New deterministic fixtures should explicitly bind instrument identity, timestamp model, quote/fill sequence, initial position, fee/slippage model, and injected failures; they should not depend on a developer-specific drive path.
