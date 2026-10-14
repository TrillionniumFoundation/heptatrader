# Post-fill generation and original quote continuity repair

Scope: the installed PR178 IB PAPER native runtime, not a model promotion. Base source tree is `f11508840bef81e355101f55cd50ec92429a1108` (remote b4f97b22 / local ee497f5d). Research PR181 c15a3dda, the original eleven-row Value candidate, Laya candidate results, frozen windows, and the registered Neuron256 serialization retry are not refit or redefined.

## Original incidents and evidence limits

The running native owner recorded three distinct exits on 2026-10-09 (UTC+08): post-fill reconciliation failure at 02:23:18; stale secondary cash-quote stream unsafe at 03:07:23; post-fill acknowledgement failure at 03:08:09. By the initial 07:21 read the original service had restart count 3, not the previous audit's count 1. Current-boot `oom_kill` was zero. These are not attributed to another OOM.

The retained OMS sequence for a BTC reduce-only sell has cumulative execution 0.16509636 followed by 0.2796508, native final status and duplicate final status. The first risk refresh starts after the partial fill; it can finish after the full fill, showing the expected zero position while its captured exposure generation still predates the final fill. The old runtime's pre-ACK condition checked completed components and position but not that captured exposure. The exact production adapter/runtime regression reproduces `IB_POST_FILL_RISK_ACKNOWLEDGEMENT_FAILED` before the fix.

A separate partial cancellation carries a native Cancelled status for the actual local order followed immediately by broker code 202. The generic admission classifier treats 202 as blocking, and the wrapper previously made even an idle quote admission permanently unsafe. IB's published message catalogue calls 202 a cancellation notification; this is not a reason to trust every 202 or suppress the original order/risk event.

The earlier cash-sized BTC order also produced `IB_RISK_UNTRUSTED_BOUND_ORDER_EVENT`. Its rejected callback was not retained with complete fields in the available original journal. This change does NOT fabricate that callback, loosen terminal quantity/client checks, assert the order was safely cancelled, or claim that particular rejection has been fully explained. Missing or inconsistent economic/terminal evidence remains fail-closed.

## 1. Exact exposure before acknowledgement

`DrivePostFillRiskRefresh` reads one adapter-lock recovery snapshot and requires complete coherent risk, the current connection epoch, and risk-absorbed exposure equal to the latest exposure generation, in addition to every existing component, position, terminal and baseline-generation condition. The same eight-second deadline, three-attempt bound and 250ms stability interval remain. An older complete refresh cannot proceed to checkpoint/ACK merely because the current position happens to match.

The new test uses the real adapter and runtime post-fill methods with a socket-free synthetic broker. It covers the observed partial/full ordering, duplicate finals, a fresh latest-exposure refresh, missing PositionEnd, a wrong position, missing terminal evidence and the unchanged fail-closed deadline. The only synthetic order method records into the private fixture, never a real broker.

## 2. Corroborated cancellation notices are not global idle quote failures

`IBLocalCancelNoticeWitness` retains bounded, current-epoch metadata of actual local submissions. It requires an original native Cancelled/ApiCancelled status from the same broker client, nonconflicting account, valid cumulative/remaining quantities and actual local order identity before recognizing a subsequent exact code 202. No localized text is used as authority. Witnesses expire; reconnect and numeric-ID reuse invalidate them.

This recognition only changes whether the callback poisons IDLE market-data admission. OPEN/RESERVED admission still fails, the original callback still follows the normal journal/risk/error path, and generic 202 remains blocking. Unknown, foreign, malformed, stale-epoch, advanced-reject and uncorroborated messages retain their original behavior. No trading fuse or order-authorization rule is removed.

## 3. Renew before original quote expiry, never restamp prices

Original captured source probes showed 5–15 second missing endpoints and repeated secondary EUR request IDs renewed about fifteen seconds apart. The original quote validity and endpoint tolerances are five seconds. Waiting until expiry plus a fifteen-second renewal floor caused avoidable missing original evidence.

The native polling loop now checks secondary renewal once per second. For the unchanged 5000ms profile it requests renewal after 3000ms, reserving two seconds for the poll and genuine broker response, with at most one cancel/request pair per three seconds for the secondary instrument. This changes request scheduling, not the quote-validity limit. Only fresh native BID and ASK callbacks on the new request may restore readiness. Old-ID callbacks cannot repair it; the primary BTC subscription and ownership are untouched.

An intermediate regression exposed that more prompt renewal must not cancel the old subscription during a known cash-farm outage. Current-epoch scoped 2103/2119 now suppresses secondary renewal until scoped 2104. The old price remains stale, not promoted. This preserves the existing stream for genuine restoration callbacks and avoids a cancel/locally-refused-resubscribe gap. Generic or old-epoch farm messages cannot supply readiness. The prior failed full-runtime test is retained, rather than weakening its two-newer-leg requirement.

## Validation and controlled installation boundary

Validation uses Debug assertions and the real pinned 10.30.01 SDK Release build. Full native tests, SDK-linked post-fill/notice/quote/full-runtime scenarios, Python Core/Source, and component ownership must be recorded against the delivered source. Model training tests/results from the previous turn are not counted as this native validation.

Installation must use the existing user new-risk pause, naturally complete already-dispatched operations, retain the original service selection/configuration/journal/leases/checkpoint, and replace only the stopped Execution binary selection. No flatten, cancellation, price substitution, broker-client replacement or state rewrite is permitted to manufacture a clean switch. Restoration of already-running PAPER drivers is distinct from model promotion and remains subject to original native checks. Failed startup/ownership/source verification retains the guard and uses the preserved rollback selection.

Private evidence root: `~/.local/state/heptatrader/operator-evidence/postfill-neuron256-20261009T0723/`. Actual installation, continuous native/source samples and the original Neuron256 retry result belong in the final execution receipt; this source document does not preclaim their success.

## Unchanged Neuron256 gate and experiment

The only permitted retry is the existing `training-execution-20261009T0154/neuron256-trial-config-v2.json`, original output `neuron256-fit-v2`, original data/seed/steps/splits/objective/cost assumptions and the same resident `heavy-worker.lock`. Reverify configuration/source hashes and source/memory admission at launch. Do not create a new trial to escape a denied admission, reuse failed windows, re-run the completed Value fit or change statistics to obtain a PASS. A completed checkpoint, independent reload and measured development result are still not canonical-owner or future-efficacy qualification.

References for callback semantics: Interactive Brokers TWS API `message_codes.html` (202) and `order_submission.html` (duplicate status and execution callbacks). These references explain message semantics, not the private account's terminal or source-quality evidence.
