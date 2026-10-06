# Cost-aware PAPER research

Canonical implementation and limitations: ../../docs/technical/cost-aware-entry.md.
Six helpers, the inherited 140 tests, real-base integration and field regressions.
Only the independent read-only observer is eligible for this initial installation;
no calibration is activated and the trading driver is not switched by installation.

Run tests with the repository core partition or tests/run_with_receipts.py.
Generated reports/captures belong in a private evidence directory, never Git.

The executable observer and driver honor an explicit factory-reset stop before
any live I/O. Use the existing replay tool's `--holdout-decision-end-ms` together
with `--fit-cutoff-ms` for frozen training / heldout decisions / quote-only outcome
evaluation. It does not promote calibration or authorize a driver. See the
technical contract for field completeness, time boundaries and limits.
