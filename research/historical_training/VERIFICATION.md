# Frozen campaign evidence and matched-result follow-through

This continuation starts from research HEAD db8658d, not the older bdc3619 report.
The 24-slot campaign and offloaded same-lead1 paired experiment already exist.
Do not install a duplicate campaign, change candidate f7f18c, shift missed slots,
backfill predictions, restart trading, or rerun large encoders on pocket4.

## Existing runtime and resource incident

At resumption on 2026-10-08, PR #178 had frozen old PAPER Execution and both
strategy drivers after its reconnect/admission incident. Its existing kill
switch remains engaged. This is not a new intentional stop by this research
continuation. Original-owner recovery and strict NAV remain separate gates.
The research campaign is an independent read-only historical client, not a
replacement quote source and not authority to resume either trading driver.

Earlier pocket4 CPU encoding caused GLOBAL OOM and killed existing Laya evaluator
workers as well as the new job. The 4 GiB cgroup cap did not prove host isolation.
The incident and missed evaluations remain retained. Do not describe the whole
workflow as having zero workload interference. Same-input CPU encoding was
completed on qian-qi, under disclosed backend and projection implementation
amendments; those failures are not newly successful trials. This continuation
performs no encoder forward passes or model selection.

## A reproduced comparator-validation defect

The original forward validator reconstructed candidate probabilities and Brier,
but trusted result.train_prior_brier and result.uniform_brier for campaign
aggregation. On a disposable copy of the first campaign result, replacing those
comparators with 1.99 and 1.98 still passed the original audit. Original receipts
were not changed. A hash alone identifies bytes; it does not prove the meaning
of every metric in those bytes.

`campaign_integrity` now recomputes BOTH comparator scores from the exact frozen
prior and observed class. It rejects nonfinite scores/targets, boolean/float
class identities, invalid counts and prior distributions before numerical
comparison. It binds each observed child to its original durable claim, SDK
inventory, collector code, parent candidate/calendar and read-only assurance.
The existing feature/probability/target reconstruction still runs unchanged.

The aggregator consumes verified comparator values, not unchecked result fields.
Final statistics reject duplicate/moved/out-of-order anchors and invalid scores.
Status refresh uses the same nonblocking lock as the collector, preventing a
parallel status/completion publication from racing an active window. Original
24-slot coverage, class/day thresholds, mean gain, alpha, costs and ABSTAIN
policy are unchanged. There is no interim efficacy test.

A validator-only runtime revision can copy the previous immutable installed
runtime into a new directory, replacing ONLY multiwindow.py and adding
campaign_integrity.py. Candidate, collection, prediction, period, timer, SDK,
original parent plan, claims and closed receipts must remain byte-identical.
Upgrade only when the research service is inactive and outside its activation
window, with the campaign lock held. No production service is modified. The
original service file and previous runtime/config are retained for rollback.

## Same-profile semantic experiment: verified, not promoted

Same lead1 profile, same serialized inputs and chronological role times:
512 fit / 128 tune / 128 calibration / 256 check. Every matched arm refits on
640 fit+tune rows. Backbones are frozen; numeric inputs and 64-coordinate frozen
semantic features use the same classifier family and predeclared grids. The
full-data frozen reference used 24,175 training rows and remains explicitly an
unequal-training-budget reference, NOT a matched arm.

Existing development Brier results (lower better):

| Arm | Brier | Mean gain vs matched numeric | Exploratory 95% day-block interval |
|---|---:|---:|---|
| Matched Numeric | 0.641088 | 0 | [0, 0] |
| English | 0.643950 | -0.002862 | [-0.031097, 0.024456] |
| Multilingual | 0.643898 | -0.002810 | [-0.023276, 0.015367] |
| English 50/50 mix | 0.634040 | 0.007048 | [-0.006670, 0.020398] |
| Multilingual 50/50 mix | 0.635907 | 0.005181 | [-0.004836, 0.014157] |
| Full-data frozen reference (unequal budget) | 0.632604 | diagnostic only | diagnostic only |

All semantic/mixed improvement intervals versus matched Numeric include zero.
No stable semantic improvement is established, and no result changes the
prospective frozen candidate. None of the three monetary policies passed
calibration admission. Diagnostic zero-buffer check PnL was Numeric 0 trades,
USD 0; English 14 simulated trades, USD -264.12; multilingual 28 simulated trades,
USD -307.30. These diagnostic trades were NOT sent to a broker or admitted to
trading. Historical bid/ask side prices already include spread; assumed extra
roundtrip costs are 0.4 bp commission and 0.5 bp slippage.

`matched_completion_audit` extends the prior stationary-head/result audit:
train-only value normalizers; exact tune-only classifier and value penalty
choices; ALL calibration buffer options; selected/rejected policy; all possible
check decisions (including omissions); time/side/entry/exit identities; totals,
drawdown and fixed-trade cost stress. Raw encoder-vector files, input/text/model
and parent-plan bindings are checked, then the declared fixed64 projection is
reconstructed. Maximum raw-to-projected difference was 0.0 for both 1,024-row
arms, with zero backbone forward passes and no original-result writes.

The first extended audit used canonical JSON for the text-hash list, whereas
the retained encoder used ordinary json.dumps. It correctly stopped at that
mismatched auditor assumption. The auditor was corrected to the ORIGINAL
producer's serialization; the failed audit log remains. No source data was
rehashed or modified to force a pass.

## Current campaign evidence and what cannot be claimed

The original schedule has 24 nonoverlapping full-span windows across six
weekdays, October 8, 9, 12, 13, 14, 15. Last planned maturity: October 15 17:35 UTC
(October 16 02:35 JST), followed by the original final sweep at 17:38 UTC.
At this continuation's first readback, 1 slot was observed, 1 censored by
EXISTING_SOURCE_REVIEW_GATE_NOT_READY, and 22 were future slots. The earlier
one-window smoke is not part of these 24 and cannot be counted twice. The
observed campaign slot had candidate Brier 0.943828 versus prior 0.677406 and
uniform 0.666667; it is not selectively omitted. No interim aggregate verdict.

Remaining account proof is independent: native retired-owner evidence is not
an authenticated Broker terminal record for an unfilled remainder, and callback
completion/arithmetic is not a common valuation basis. Keep BTC original
terminal gaps, strict NAV, opening valuation/flows and production freeze clear.
No email authorization, new broker login, Gateway upgrade, owner transfer or
order/recovery mutation is part of this verification continuation.
