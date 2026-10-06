# Cost-aware PAPER research

Canonical implementation and limitations: ../../docs/technical/cost-aware-entry.md.
Six helpers, the inherited 140 tests, real-base integration and field regressions.
Only the independent read-only observer is eligible for this initial installation;
no calibration is activated and the trading driver is not switched by installation.

Run tests with the repository core partition or tests/run_with_receipts.py.
Generated reports/captures belong in a private evidence directory, never Git.

The executable observer and driver honor an explicit factory-reset stop before
any live I/O. The resident driver uses the raw remote-model distribution plus
deterministic transaction costs; it never reads a local calibration artifact.
Use hepta_cost_oos.py register/evaluate for a preregistered fixed remote-model
OOS window followed by a fixed quote-only outcome tail. The legacy replay CLI
is diagnostic only and never grants strategy acceptance. No refit occurs in that
window. --fit-cutoff-ms is reserved for optional local-model training readiness
and does not block the current remote strategy. Neither mode promotes a model or
authorizes a driver. See the technical contract for time and cost boundaries.

After a deliberate accounting reset, observation may use a separately provisioned
native WATCH-only cycle through `--observation-profile`. Legacy launch paths and
the trading driver still honor RESET-STOPPED without exception. The seventh
optional helper validates this observation access and renews only an existing
WATCH lease; it has no trading or account-period management interface.
