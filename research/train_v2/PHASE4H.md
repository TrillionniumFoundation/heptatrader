# Train V2 Phase 4H: read-only original IB EUR/USD session boundary audit

2026-10-11 Pocket4, stacked on Phase 4G, NO trading, broker API, deployment
or model authority. This resolves the leading *investigation* question behind
AUTHORITATIVE_QUOTE_UNREADY, not every future market-data entitlement problem.

## Actual IB regular FX weekly session, source and limits

IBKR's published IDEALPRO regular FX schedule uses America/New_York:
Sunday 17:15 through Friday 17:00, with a daily 17:00-17:15 rollover gap.
Timezone conversion must use IANA DST rules, not hard-coded UTC offset.
Official public product-hours reference:
https://www.interactivebrokers.co.uk/en/trading/productsprint.php

These regular hours are **calendar expectations**, not evidence of broker
uptime, live entitlements, exchange/IB holidays, stale quote safety, legal
candidate enumeration or permission to trade.

On Pocket4 at 2026-10-11 02:24 +08 the corresponding New York local time
was Saturday 2026-10-10 14:24 -04, in the weekend-closed interval.
The next scheduled regular opening is Sunday 2026-10-11 17:15 ET,
which is Monday 2026-10-12 05:15 China time / 06:15 Japan time.
Special closures and real subscriptions must still be rechecked.

## Verified actual original source transition

The independent, read-only market-session auditor verifies the existing
owner-private resident Laya PAPER config + exact period SHA and reads the
already locally SHA256-chained original segment files with the unchanged
Laya source parser. It does NOT make a new IB request or restart Gateway.

- Configured local feed: 165 original segment files checked under the
  existing Laya feed root.
- Locally hash-chain-verified per-segment unique quote entries: 86,939;
  deduplicated real original quote timestamps: 86,642.
- Cross-segment equal-timestamp bid/ask price conflicts: **0**.
- Unchanged existing Laya resident_data.read_quotes independently
  confirms the identical latest original quote receipt and the 86,642
  unique source timestamps.
- Latest locally witnessed genuine original IB quote:
  2026-10-09 21:00:00.199Z, which is Friday 17:00:00.199 New York.
- The first subsequent authenticated *local source-chain* FAILURE event:
  2026-10-09 21:00:01.204Z, reason AUTHORITATIVE_QUOTE_UNREADY.
- Those offsets are within ~1.2 seconds of official weekly close.
  This is strong observational consistency with normal weekend closure,
  NOT a broker-signed statement that entitlement is always healthy.
- Full final read-only source calendar assessment:
  HOLD_FX_REGULAR_SESSION_CLOSED; trading authority remains FALSE.

Final v3 immutable private evidence:
~/.local/state/heptatrader/operator-evidence/train-v2-market-session-20261011/actual-source-calendar-receipt-v3.json

SHA256:
6a3c8672e842e5f274566733acbd98aeee918f274802455ca68ad1244018e446

All 165 configured segments were available at this measurement, with
ZERO older files omitted. The code also has a bounded last-200 source
profile for the point at which the continuous feed grows beyond 200
segments. In that case an explicit omitted count prevents a falsely
claimed full historical attestation; the audit remains research-only.

Earlier v2 price-conflict verification is separately retained (SHA256
0e5aa5d244d611300acd9b1e38b7bcef70fc981bd709d9a7d5aa247ba8216ce5),
and the preceding v1 receipt is retained with SHA256
3c7c539c9cb572d9cb24ac9aca313f43060cb9702cfe2f2c1105f699a302b444.

## Why no restart or TTL relaxation

The actual production executiond is connected and its risk, account,
position and terminal reconciliations are complete. Its live observation
shows EUR.USD subscription dispatch was accepted but the secondary EUR.USD
bid and ask age-valid flags are false during the closed interval.
The original Laya quote feed keeps writing actual
AUTHORITATIVE_QUOTE_UNREADY failure events; its absence of eligible quotes
is **not** a proof of a dead service, USB fault, or lost model.

The source reader and the real shadow pre-action policy must not turn a
historical or delayed price into a fresh 1.5s-bounded quote. In this
weekend context, changing IB market-data-type, restarting live PAPER
services, lowering source requirements, or retroactively injecting
quotes would add risk without fixing a verified fault. No such change
was made.

## Implementation safety

original_market_session.py is an explicitly read-only one-shot auditor:

1. Validate current private PAPER feed config, UID, exact period SHA,
   original source root and only bounded segment files; fail if a
   symbolic link, changed source or oversized collection is encountered.
2. Verify each segment using the original Laya SHA256 chain, original
   IB-shaped data invariants, source timestamps and zero trading writes.
3. Enforce original Laya cross-segment timestamp/price conflict
   semantics. Deduplicate equal timestamps rather than treating
   repetitions as statistically independent examples. If the active feed
   grows beyond 200 segments, inspect the newest 200 and explicitly
   record the omitted older count; NEVER call that a full-history proof.
4. Compare latest verified quote with original first subsequent failure,
   preserving their source-prefix receipt and their differences
   from the official Friday close boundary.
5. Apply DST-aware weekly/daily session clock; CLOSED -> HOLD.
   An OPEN clock by itself does not yield quote authority, and an old
   quote after market reopen remains HOLD. Even genuinely recent quotes
   receive only SOURCE_TIMING_REVIEW_ONLY_NOT_PRODUCT_ACCEPTANCE.

No daemon, scheduler, listener, network connection, trader plugin,
broker query, key access, model training, signing or ledger writer is
created by this auditor. It cannot authorize any new economic event.

The original unchanged Laya local parser/source archive unit tests
were also rerun in the system Python environment (no GPU model needed):
16 original shadow-source tests and 22 original source-archive tests
passed, both with zero failures. Their original logs are retained
in the same private evidence root as laya-shadow-source-original-unittest.log
and laya-source-archive-original-unittest.log.

An attempt to collect the *entire* Laya model test suite in system
Python failed before executing tests because torch is not installed
in that interpreter. This failed collection is retained as
laya-quote-source-tests.log and was not relabelled a passed suite.
Neither global Python packages nor any running model/GPU was changed.

Final original HeptaTrader Python core + historical research suite:
**1,634 passed; 21 skipped; 725 subtests passed; zero failures**.
Exact final log SHA256:
0af299c5e297b604e0effdc3b7656580d5b545df582cd183e18336b1d1c61ad0.

Immutable Phase 4H read-only composite evidence seal:
~/.local/state/heptatrader/operator-evidence/train-v2-market-session-20261011/phase4h-weekend-source-final-receipt-v1.json

Composite SHA256:
1a697497a9170a32ef7570cf61511e79108c03debc9ed062725b02ffff034ca5

## Separate full Canonical Agentd product-host laboratory qualification

This is separate from the actual Forex read-only source audit and
is **not** a production product host enrollment. Original unchanged
hepta-private-ci at the same exact commit was compiled and executed
through codex-hepta-agentd --test terminal_cell_owner:

- First bounded original Agentd compilation expired its controlled
  timeout (exit 124) before executing the integration tests. Its
  original log remains preserved, not called a test failure/success.
- Reusing the native target cache, the unmodified original product
  host test completed: **2 passed, 0 failed, 1 explicitly ignored**.
  The two passing cases cover native authenticated test Decisions,
  separate test Observer outcomes, DatasetSnapshotV3 freeze and
  revalidation, artifact registry/payload persistence and reload,
  source withdrawal, and Agent memory replay/selection invalidation.
  Product host test log SHA256:
  d0fcd3b888426ec3d2646514c38ea2644a8c2a3b966c362bbfc5ce750ed5f93b.
- The ignored host storage/concurrent training profile was then
  **explicitly executed**: 1 passed, 0 failed. It used 7 independent
  **test-only ledger instances** spanning one-owner 64/256/1,024
  record cases plus four-owner 256 records each, totaling **2,368
  test records**. Measured group wall times on this Pocket4 run were
  412ms, 1,583ms, 6,282ms and 1,759ms respectively.
  It verified each retained owner's witness frontier, full-history
  recovery, training, artifact registry reload, and indexed dataset
  read before recording success. It does not demonstrate hardware
  sudden-power failure or a long-term latency/throughput SLO.
  Original host profile log SHA256:
  938a512fe6482710b629b4bb8a5affa9cf43fe1ab96eca12a329252bc771aa03.

The original Host test signing keys are deterministic fixture seeds
within one test runner. Different Ed25519 test keys/controllers DO
NOT establish two independently administered production subjects.
No actual Pocket4 production LedgerWriter service, independent
product root signer, real trading Decisions, original all-action
behavior propensities or independent actual financial Outcomes were
created, imported, selected or deployed.

Immutable separate native Host qualification receipt:
~/.local/state/heptatrader/operator-evidence/train-v2-market-session-20261011/canonical-agentd-product-host-lab-final-v1.json

SHA256:
607d8552a0468418d3e45862befa46c3512dc4eb0ee9bfe516f26fdb2cb7095e

## Next product gates still mandatory

**After** real Sunday forex reopening, verify actual owner-approved
subscription, IB authoritative bid AND ask callbacks, cash quote age,
source TTL, six+ original unique contiguous quote timestamps and a
new strictly causal pre-action receipt. A calendar opening alone does
not constitute these facts. The frozen previous trial history must not
be restamped as new.

Original order-capable behavior-policy complete candidate set, true
action-selection probability for its HOLD/vetoed/traded outcomes,
independently administered Generator/Observer and root/credential
chains, native product LedgerWriter witness ACK, uncertainty recovery,
signed real corrections/revocation, DatasetSnapshotV3 final-use
revalidation, artifact Owner/CURRENT and unseen real after-cost
efficacy are still unqualified. The Phase 4G shadow CSPRNG policy
is a separate research controller and cannot stand in for them.

### Reproduction

From this branch with
PYTHONPATH=research:research/neuron_learning:scripts:

python3 -m pytest -q tests/python/test_train_v2_original_market_session.py

Run the one-time audit by passing the installed resident config to
train_v2.original_market_session --config PATH --output NEW_PRIVATE_RECEIPT.
Do not overwrite existing evidence or claim a regular-hours calendar
prediction is an independently sealed future market trial.
