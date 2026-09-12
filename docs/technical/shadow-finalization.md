# Read-only SHADOW finalization and integration acceptance

Status: CURRENT
Applies to: source-only experimental SHADOW evidence pipeline

## Interface and inputs

`scripts/hepta_shadow_finalize.py` takes one canonical observation policy, its runner state, all canonical iteration receipts, chronologically ordered nonempty history directories, an explicit finalization timestamp, cadence/jitter, and a new output path. It calls the existing history and replay validators; it does not capture data, evaluate another decision, mutate a session, contact a broker or grant authority. The CLI flags are `--policy`, `--state`, repeatable `--receipt` and `--history`, `--output`, `--finalized-at-ms`, `--cadence-ms`, and optional `--maximum-jitter-ms`.

Stop the collector first. The finalizer holds the runner's exclusive state lock and shared directory locks against cooperative history writers. Directory locks are acquired in path order, while supplied segment order remains the chronological audit order. Retain immutable source inputs in the reader-owned evidence boundary; these locks do not defend against a malicious process with the same UID.

## Validation and publication

All planned iterations must be present and slot-valid. The final state must bind the last receipt digest, decision ID, packet digest and outcome. Every history record and segment boundary is validated, then adjacent captures are checked against cadence, including lease rotation and segment transitions; zero missed samples is not inferred from a successful per-segment audit. The final timestamp cannot precede any sample or decision.

The output is `hepta.bounded-shadow-final-audit-receipt.v2`, consumed unchanged by the sealed decision/mark replay path. Segment counts, record/index/storage bytes, source/head digests and audit digests come from actual retained records. The payload accumulator covers exactly the policy, runner state, decision receipts and history records/heads, not unrelated provider files or all files on the host. Its count is an audit input inventory, not an installation inventory.

Publication is create-only, file- and directory-synchronized. A retry with the identical inputs and final time returns the identical canonical receipt; changed inputs or a different pre-existing output reject. A failed publication leaves sampling and runner state untouched. This is not a terminal lock that prevents the owner from ever collecting again: appending later invalidates the earlier sealed mark binding and requires a new explicit campaign/evidence boundary.

## Time and information provenance

Provider calendars describe a schedule whose coverage includes decision time. Press RSS feeds describe items known at retrieval and must have coverage end exactly equal to retrieval, not a fabricated future endpoint. Each source must still meet the configured freshness bound. The context builder revalidates root-owned extraction attestations and retained payload semantics. Changing document timestamps cannot authorize stale/future evidence.

## Reproducible acceptance

Run ordinary provider-format and component tests through the core Python lane. The full pipeline requires a disposable Linux root fixture: `HEPTA_ISOLATED_PROCESS_TESTS=1 python3 -m unittest discover -s tests/python -p test_shadow_pipeline_integration.py -v`. It refuses an existing `/var/lib/hepta`, creates only temporary root-owned exports and uses UID/GID 1000 for the reader. Cleanup verifies the interlock inode. Do not run on a trading host.

The full test executes the real capture helper with only its HTTPS opener replaced by synthetic responses for all five pinned formats. It exercises actual payload retention, pinned extractor replay, normalization, 1,208 root-exported WATCH contract samples, four lease generations, 40 closed five-minute bars, packet construction, a positive SHADOW decision, interrupted receipt/state publication, interrupted nonempty history head publication and recovery, finalization failure/retry, sealed decisions and marks, and a filled replay whose net result changes sign under explicit costs. It also rejects missing/changed finalization inputs and corrupted replay binding.

The WATCH exports are contract fixtures, not proof of an installed observer, signed broker history or live market prices. The synthetic HTTP seam does not test live TLS, provider availability or current website format. Production host, network and PAPER qualification remain separate external requirements. Source integration completeness is not profitability or permission to trade.
