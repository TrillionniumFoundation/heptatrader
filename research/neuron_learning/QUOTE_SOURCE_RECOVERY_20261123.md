# Pocket4 original quote transport and prospective windows

The first repaired Pocket4 feed record, captured at **2026-11-23 01:57:00.683 UTC**, carries the native failure reason `SOCKET_CONNECT_FAILED`. It preserves exit code 4, zero stdout bytes and the digest of the 22-byte diagnostic. The SHA-256 of the chained observation is `7b1f9805cdc70954526701e73f6a575abefd41159eedc652cf1ddfc22d30ae59`.

| Check | Observed result |
| --- | --- |
| Original native read, 01:43:45.190 UTC | `market.get_quote instrument=EUR.USD` exited 4, empty stdout, `SOCKET_CONNECT_FAILED` on stderr |
| Earlier active segment at this sample | 102 failures categorized as `QUOTE_TOOL_JSON_INVALID`, 0 quotes. Their original stderr is unavailable. |
| Installed repair | Applied the two diagnostic hunks from HeptaTrader draft #532 commit `ab60609fa494063a9b283fd977eaa21e50302795` to the running historical collector with zero fuzz. |
| Test qualification | All 52 tests from the diagnostic, original transport fault, and feed health suites passed with the installed dependencies, both locally and on Pocket4. These include synthetic positive, stale, malformed and non-authoritative responses. |
| Release scope | Verified all 108 runtime members before staging and again after the switch. `learn/laya_cell/shadow_source.py` is the sole changed member. The reconstructed unit only changes the release/configuration references and retains all three resource overrides. |
| Continuity | The previous segment produced an END and a matching collector receipt. Trainer and evaluator retained their PIDs across the read-only feed restart. |
| Post-switch independent re-read, 01:57:56.407 UTC | Active service, 13 chained failures all carrying `SOCKET_CONNECT_FAILED`, 0 admitted original quotes, 0 orders sent. |
| Scheduler and historical trials | Read the scheduler database with `mode=ro`. Of 45 distinct NO_FORWARD_TRIAL receipts, 44 match current COMPLETED jobs. All 45 say no trial was allocated. Of the 44 matched jobs, 26 have `EXACT_REQUIRED_PRIOR_NO_POSSIBLE_STRICT_GAIN` and 18 have `NO_CONTINUOUS_OPEN_WINDOW_FITS`. One detached receipt was excluded. The latest of the 45 recorded deadlines was 2026-11-05 22:15:00.959 UTC. |

The old release's installer excludes the 20 nested test assets that its running configuration pins. The replacement release keeps all 20. The dry run rendered the original unit template in a temporary user home, and the observed final unit matches that rendering with the expected config and release paths. The hardware resource drop-ins were compared by digest before and after cutover. The original unit bytes and immutable runtime remain available on Pocket4 for rollback. No execution, broker, gateway, trainer or evaluator unit was restarted by this change.

## Outstanding external dependency

Execution startup still reports `IB_FX_CASH_RESTART_CATCHUP_EXECUTION_INVALID`; the gateway's evidence stream reports `TOOL_GATEWAY_EXECUTION_EVENT_READINESS_TIMEOUT`. The complete original 53,092,563-byte OMS journal and the original FX restart attestation match the hashes examined in the prior recovery review. Its remaining submission was accepted and sent, and the final retained order status was `PreSubmitted` with zero filled and a positive remaining quantity. The narrowly searched operator/recovery locations yielded zero new candidate historical broker statements. The prior seven-barrier API snapshot covers available current-day records and cannot establish the disposition of that old order.

A separate retained broker-observability log overlaps the submission. The aligned final 6 MiB includes 33,582 records, spanning 2026-11-03 07:15 UTC through 2026-11-04 05:02 UTC. From the original acceptance (with a two-second timestamp margin), it contains exactly one order-status, execution-details, or completed-order callback. Its order and API client IDs, exact millisecond, status, zero filled quantity, and remaining quantity agree with the journal. Its status is `PreSubmitted`; this diagnostic record omits connection epoch. This check found zero additional disposition evidence. The post-switch operational readiness publisher reported 0/24 usable windows with source revalidation pending.

The broker/recovery owner needs the original order's authoritative historical execution or terminal-cancellation evidence, with matching identities, quantities and cash reconciliation. Once the original gateway emits fresh admissible EUR.USD quotes, a newly registered trial can accumulate observations across its own future window. The existing 24 source-readiness probes are operational checks. The separate monetary evaluator requires original settled, attributed, fee-complete trades over its own multi-date window. This run contributed zero new quotes, labels or financial observations.

Prior review and exact proof hashes: [HeptaTrader recovery evidence](https://github.com/TrillionniumFoundation/heptatrader/blob/ab60609fa494063a9b283fd977eaa21e50302795/research/neuron_learning/QUOTE_SOURCE_RECOVERY_20261121.md).
