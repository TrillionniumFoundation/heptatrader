# Exact 10:05 original vs cached FX source-health comparison — physical qian-qi review

**2026-10-13 pocket4 local UTC+8. Verdict: research monitor freshness
comparison PASS; original 10:05 FX study correctly CENSORED;
production release and trading HOLD.**

## Scope

This is a **postevent, read-only physical-host audit** of the ONE
previously registered exact 10:05 cache-vs-original source-health
comparison. It neither preselects a model nor executes, cancels,
reprices or simulates a PAPER trade.

Original four-window prospective PAPER quote-only protocol was sealed
the previous evening, before any future event. All source, time,
CENSORED and NO_TRADE gates remain unchanged.

qian-qi separately retained the original plan, original seal,
cache-comparison plan and timestamped pre-canary identity receipt
**before 09:59:40**, with exact SHA256 pins:

| Original immutable source | SHA256 |
| --- | --- |
| Original PAPER plan | `02aa4a2662b743203cc925d09d3250bcc0e02155cf5d00bb319b0526448aaa04` |
| Original PAPER seal | `1f13ed5c7a8fcdc29cc0881bf5c7854c263876f5f89bb6bc87633d5b2b33860b` |
| Cache-vs-original fixed plan | `10fea7101627dde050a0b6f6ec1cfa93d7dc746ad0fc57c13b4b7224f7659943` |
| Physical preevent qian-qi readback | `1cf6f67ee369650ccc2a8880f3bb2b8203666a370431cffd5a8896d1cb32560e` |

The original unit was **not modified or restarted**. The sandbox cache
monitor was programmed for 09:59:40, the one-shot dual read for
**10:05:02** with 100ms timer accuracy, both write-once. The
original 10:05 decision remained at +1 second.

## Actual fixed-time as-of evidence

The exact original comparison report was durably written at
**10:05:02.064**, following snapshot at **10:05:02.062**.

| Original 24-probe monitor | Deployed baseline | Sandbox SHA-verified cached |
| --- | ---: | ---: |
| Monitor's original `state` | READY_FOR_REVIEW_NOT_AUTHORIZATION | READY_FOR_REVIEW_NOT_AUTHORIZATION |
| Usable/missing probes | 24/0 | 24/0 |
| Original `at_ms` at 10:05:02.062 | 10:03:35.970 | 10:04:44.062 |
| Health as-of age at comparison | **86,092 ms** | **18,000 ms** |
| Frozen validity limit | 30,000 ms | 30,000 ms |
| Freshness result | **HOLD** | **READY** |
| Mismatched rule/provenance/counts | none found | none found |
| Producer independently IB-signed | no | no |

The original 10:05:01.001 decision and separately retained health
custody shared the **same exact original health SHA** as the baseline
comparison. The original snapshot was already **85,031 ms old** at
the original frozen decision. Therefore its `CENSORED /
ORIGINAL_READINESS_HOLD_OR_STALE` is correct and irrevocable, **even
though it reported 24/24 otherwise healthy probes**.

A warmed, SHA-verified sandbox cache published current 24/24 health in
18 seconds at the **same predeclared comparison time**, without changing
the original monitor's source, IB quote TTL, probe rules, or original
admission. It shows a credible **operational freshness remediation
candidate**; it does *not* show a profitable FX model or genuine broker
execution / native product authority.

Source SHA256:

- Pocket4 original comparison file:
  `29e56a7e37a01a98d5e126632af6c8a59017c2ba75669adf32d09178ecd2af6d`.
- Exactly retained original readiness bytes:
  `016e0c71f7f303e249122361435d953196eb59042fb8e1334d5c37021f977a19`.
- Exactly retained sandbox cache readiness bytes:
  `e3bead2945ca8f57df2129645de0f573d81954b8085b23d5fb07b04a2fd7c294`.

The original source-to-physical-host transfer occurred after this
event and **cannot supply an independently broker-signed price source**,
a hardware time signature, or a pre-decision native Witness. Its
trust is limited to accurately reproducing retained source bytes.

## Physical off-host verification

On qian-qi (no IB Gateway client), the standard-library-only
`fx_1005_cache_crosshost.py` reads the pretrial qian-qi copies,
the one-time original comparison, exact original/cached
24-probe status JSONs, actual original CENSORED attempt and
the independently retained research readiness custody at 10:05.

It recomputes all probe counters, source-prefix SHA256, source quote
age, original/cached status age, exact comparison and decision clocks,
same period, no-trading attributes, stable plan hashes and
zero new orders. It **requires** original `CENSORED`; it is
incapable of upgrading it or substituting the cached READY.

Physical qian-qi report:
`~/.local/state/heptatrader-fx-1005-physical-review-20261013/independent-1005-cache-review-v1.json`

SHA256:
`ed82ab35033d1031a9e0558c782eb34567822674c3b047327dc1d89b4ebd3b9d`.

Ten adversarial tests on actual physical evidence pass, including
cache plan and original plan forgery, fake comparison clocks, original
CENSORED replacement, changed 24-probe count, source provenance
tampering, false cache-ready claim and missing original/cached bytes.
The report is write-once under a private 0700 directory, no
model promotions or PnL rewards.

The sandbox canary ended naturally with result=success after
**24** cached monitoring cycles. Published assessment age under
its real 25% CPU quota was approximately 2.5–3.2 seconds
after warm-up, with cold-start first cycle around 17.9 seconds.
The canary is deliberately not the live source monitor.

## Next hard gate

Do **not** replace the production source monitor merely because
one short sandbox run passed. Preserve the original final
15/30/60/120 maturity receipts for the 10:05 cohort through its
predeclared 12:05 horizon; expect all four to be CENSORED because
the original entry failed.

Then conduct a separate, fully frozen production candidate
validation of SHA-verified caching and a bounded monitor cadence:
sustained 25% CPU, hot and cold start, archive rollover,
missing-source fail-closed, replay-injection and source digest
consistency, and independently retained exact as-of age at future
entry. Require original period and session eligibility.
This technical qualification remains separate from model profit,
Train V2 native FeatureSpec <=60min, Owner/LedgerWriter/Witness,
IB Flex/Activity, funding/financing and full account NAV.

**Trading, model auto-training and promotion: HOLD.**
