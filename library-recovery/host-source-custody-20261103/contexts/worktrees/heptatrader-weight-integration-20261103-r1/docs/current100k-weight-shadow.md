# Current100k direct weight study and prospective shadow comparison

This research workflow separates a reproducible diagnosis from prospective evidence of usefulness. It does not change the trading actor or grant order authority. The existing financial-forward trials retain their original records and producer identities.

## What is compared

Freeze the original value head, base-model identity, original input rows, feature construction source, and numerical execution profile before constructing candidates. The historical labels have already been inspected. Every result on these rows is a development result, including a chronological split of the same rows.

Encode each original input once using the verified frozen encoder. Save the feature tensor and its ordered row identities. All candidate heads consume that cache. Export every candidate as safetensors, reload it into the actual linear module, and compare its forward result with the expected calculation. Keep original GPU versus new CPU numerical differences separate from the paired CPU comparison.

The comparison includes the unchanged parent, a bias-only mean correction, mean and median constants, a norm-constrained closed-form readout, a reproduction of the original SGD recipe, mechanistic local patches, and a bounded same-budget training arm. A constant baseline need not satisfy a candidate-head parameter constraint; identify it as an analytic baseline. Predictions in the prospective protocol share the same USD scaling and output clipping rule.

Changing an intercept can reduce mean error without creating input discrimination. Report prediction standard deviation, centered feature geometry, actual changed coordinates, and the weight-norm-based upper bound on possible output variation alongside MAE, RMSE and Huber loss. Distinguish an equivalent reparameterization from a behavioral change. A patch which loses to the simpler readout is retained as a negative result.

## Compute accounting

Separate base loading and feature extraction, candidate construction, serialization/reload, prospective encoder inference, and cheap per-head inference. The expensive feature extraction is shared by direct editing and training. A sixteen-step reference is not by itself an equal-compute training comparison. Fix and record the head-construction time budget, optimizer recipe, step ceiling, actual iterations, process CPU time and wall time. Also disclose the agent's search and implementation effort; a millisecond matrix operation does not represent the entire cost of finding a patch.

## Original-request binding

A resident predictor loads the verified encoder before waiting for a request. It reads the original attribution stream, computes the two possible action features from the exact original request, evaluates all frozen heads, and durably seals a common prediction publication. Each attempt has a terminal outcome. The observer subsequently joins the publication to the exact original command identity.

The publication timestamp is taken after the prediction and commit records have been flushed. To count as a valid prediction, publication must precede both original command preparation and the first actual fill. A late prediction stays late. A missing prediction stays missing. A non-trade request is not a closed trade. All model arms use the same observed action and the same fee-complete eventual outcome; the unexecuted action does not acquire an invented counterfactual reward.

The attribution reader may reuse previously validated immutable segments and append-only prefixes within one process. Mutated old prefixes, source registration changes, partial tails and invalid record ordering remain fail-closed. This optimization does not modify the live driver.

## New producer lineage

When the active trading producer differs from the historical training producer, explicitly register a transfer experiment with both identities. Do not substitute the active manifest into an old trial or pretend that the old training population came from the new producer. Formal adoption requires the existing NEW_PRODUCER_SCOPE registration workflow; this research comparison does not mint its signed acceptance receipts.

## Prospective window and analysis

Freeze the complete set of arms before the future input window begins. Retain the existing fourteen-day input window and one-day settlement grace period. Preserve the support requirements: twenty observed trades, three UTC days, five trades in each time half, at least 95 percent coverage, no unresolved original commands at final assessment, two outcome-sign classes, the fixed output bound, and fee stresses of 1, 1.5 and 2 times actual reported execution fees. The minimum practical MAE improvement remains USD 0.10.

Use paired outcomes for all comparisons. Group temporally overlapping episodes and UTC-day observations when presenting block-resampled uncertainty. Correct for the frozen family of comparisons. Bootstrap intervals are model-dependent diagnostics, not a proof of independent financial returns. Report the fixed final assessment and its precise inference method; interim summaries do not authorize selection.

An evaluator is a separate process which reads the authoritative current ledger, original command bindings and sealed predictions. Current fee, settlement or attribution revocations invalidate an outcome; previously seen commands cannot disappear from the denominator. Historical training rewards are rechecked. Source changes or missing support leave the final comparison incomplete rather than converting missing observations into favorable results.

## Resource and deployment contract

Run from an immutable source release with pinned configuration and candidate hashes. Apply CPU affinity and BLAS/OpenMP/thread limits before Python imports. Reuse the existing resource admission policy, manual holds and heavy-worker lock. Bound resident sessions and release model objects before releasing their lease. Keep existing training and evaluation services eligible for admission; measure whether the chosen profile actually meets the pre-open deadline under this policy.

A deployment is not verified merely because its service is active. Read back the source/configuration hashes, plan identity, candidate identities, worker status, actual prediction publication, original command binding and before-open timing. Preserve failure receipts. Stop collecting model inputs at the fixed input deadline and continue settlement replay only through the fixed closure policy.

Private inputs, features, model tensors, reward records and host configuration remain in the owner's private experiment directory. Commit generic implementation, tests and reproduction instructions to the repository.

## Interpretation

Development improvement establishes that a numerical edit changes the observed computation. Prospective paired improvement tests usefulness for this producer, behavior scope and time period. Neither result alone establishes broader market profitability or warrants automatic execution selection. The research modules always report trade authority and automatic selection as false.

## Arming while the original producer is paused

`supervise_current100k_weight_shadow.py` pins the research configuration, predictor template and a qualified backend receipt. It waits for an original request no older than sixty seconds and successful admission through the existing value-predictor source/resource checks before registering the fixed future window. It neither removes a native kill switch nor restarts the trading actor. A dormant input stream does not consume the fourteen-day window.

Registration happens once, five minutes before the input window starts. The supervisor retains that window across restarts, writes the exact predictor configuration, supervises independent prediction/evaluation processes, stops model work at the input deadline and stops after a final assessment exists. An interrupted registration without its receipt is held for inspection; it is never backfilled. A changed producer, candidate set or numerical profile cannot silently reuse the registered window.
