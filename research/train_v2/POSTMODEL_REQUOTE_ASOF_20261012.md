# Train V2 — original post-model quote as-of feasibility (2026-10-12)

Read-only evidence and new-policy architecture audit. Isolated worktree based
on Phase 4R original opaque-model latency branch c552e13 (PR #229).
Never installs a trader, model, producer, Laya adapter, canonical Owner,
systemd task or exchange writer. No requests, model inference or orders.

## Exact problem and distinction

Real original PAPER LLM response arrives after its INPUT quote's optimistic
5000ms deadline in the large majority of original requests. For the unchanged
opaque model, REQUEST to DECISION P50 was 6377ms and original quote to DECISION
P50 was 7624ms in the independently reviewed 5135 original graphs. The same
original quote cannot be rescued by fabricating a newer timestamp.

A possible NEW policy may compute an older-source model forecast asynchronously,
then check a separate newly authoritative quote at final decision time. But that
quote was NOT an input to the original opaque model, so an old BUY or SELL
cannot be retroactively re-labeled as an admitted new-policy action. Original
model confidence is NOT a logged selection propensity. This is only a
retrospective feasibility bound on input availability; no economic alpha,
product authorization, native LedgerWriter ACK or original PREPARED proof.

## Original sources and finite analysis

The source is the installed original EUR/USD PAPER REQUEST/RESPONSE/DECISION
hash-chain under the three original pinned attribution releases, cross-checked
against each release's unmodified signed-by-local-hash runtime manifest and
original decision graph verifier. It is paired with the original complete
numeric quote segments in the current Laya original source archive.

The numeric quote archive only admits segments matching the exact numeric
19-digit/8-hex name, original local hash-chain, proper END, same accounting
period and EUR/USD instrument. The newest not-END-ed segment is excluded;
non-latest unsealed segments, duplicate segment starts, symlinks, original
source conflicts and over-budget assets fail closed. A partially written
segment is never silently treated as a completed signed source.

Quote selection is a strict AS-OF query keyed by the original time the
quote was actually locally AVAILABLE, never solely the earlier broker
event timestamp. The latest already available quote must also have
event_time <= model_return_time <= original IB-reported stale_after_ms
and <= event_time+5000ms. Out-of-coverage model decisions remain UNKNOWN.

The returned action distribution is ORIGINAL observed BUY/SELL/HOLD only,
not a complete execution-opportunity population. No original post-risk
candidate set, veto reasons, actual native per-action propensity, signed
CURRENT owner or account-complete financial reward is inferred.

## Real bounded read-only pilot

The initial immutable proof inspected 185335751 bytes in 252 fully
sealed original numeric quote segments, with one unsealed newest segment
excluded and 100 legitimately empty sealed segments. 1856 complete
original EUR/USD model-return graphs were in the source-time bounds;
1675 had an original fresh local quote at model return, 181 did not.
Among the fresh-quote subset, 1667 quotes were newer than the original
LLM input, while 8 were not. Original returned BUY 295, SELL 298,
HOLD 1082 among the fresh subset; none was promoted.

The later immutable v2 source snapshot includes 1861 verified original
EUR/USD model-return graphs, with 5 beyond the sealed quote archive and
kept UNKNOWN, 1856 in the pinned quote time bounds, 1675 original
fresh-source upper-bound matches and 181 with no fresh source. Its
pinned quote archive contains 185627829 verified original bytes.

The original quote's remaining validity time at the historical model return
for the qualifying 1675 has P50 3396ms, P90 4410ms, P99 4787ms
(nearest rank, conditioned on a successful as-of match).
This cannot be transferred to future runtime-signing SLOs.

## Hypothetical additional processing budgets, NOT successful actions

The probe explicitly counts how many already matched model-return records
would still fit *assumed*, NOT measured all-in time after model return
for future signing, native witness, risk, market safety and PREPARED.

Assumed extra 100ms: 1671 eligible-time upper-bound matches.
250ms: 1660.
500ms: 1633.
1000ms: 1575.
2000ms: 1370.

The 1000ms case contains original returned HOLD 1017, BUY 274,
SELL 284 — merely original model output labels, never a legal trade
count. Requiring a full original Native-ACK and risk SLO may reduce
all counts, potentially to zero.

Original quote versus latest quote price drift on the matched subset
is measured without using it to revise the forecast, recover missing
labels, or claim profitability. A large drift must trigger a fresh
economics/risk decision in any future independent policy.

## Security and counterexample acceptance

New adversarial tests check the original quote/source time, original
model graph identity, incomplete RETURN/RESPONSE, duplicate across
releases, fake action grammar, wrong accounting period, original hashed
quote corruption, duplicate source start, symlinked/malformed source,
unsealed earlier segment, latest unsealed exclusion, future-arriving
quote, expired quote, changed model source time, nonfinite/crossed prices,
bounded latency slack and all outcome denominators.

No hidden secrets, prompts, raw model response, rationale or private
account IDs are emitted to the aggregate proof. The program seals
a create-only fsynced research report and pins its exact source code
and adversarial test SHA256. Earlier immutable v1/v2 reports stay intact.

## Next genuinely new policy gate

1. Declare a NEW model/policy identity and source/forecast TTL/horizon,
   always admitting an explicit NO_TRADE/ABSTAIN candidate. The old LLM
   output is a frozen earlier-input forecast with source hash and model
   inference timestamp, not a requalified original trade action.
2. Pre-register a new prospective campaign. Independently enroll
   Generator and Observer principals under canonical product root trust,
   with role/signature separation, not test-only host keys.
3. Capture fresh source after the forecast exists, independently sign it,
   revalidate its true broker TTL, run a fast deterministic final policy
   and risk/economics checks, and fsync original Native LedgerWriter plus
   independent Witness **before** original PREPARED and quote cutoff.
4. Document every opportunity, including NO_TRADE, quote missing, source
   gap, stale forecasts, risk veto, late ACK, agent timeout or no model
   dispatch. True behavior-policy selection propensity must be logged
   by the actual generator and cannot be inferred from confidence.
5. Use unseen independently observed market and after-cost execution
   outcomes, correct censoring and original OMS fees and total PnL.
   Require net advantage over the no-trade benchmark and an authorized
   Canonical Artifact Owner CURRENT selection before any cutover.

No currently installed trader, original trial deadline or incomplete
historical outcome can be retroactively converted into this new policy.


## Final source/test-bound Pocket4 result and whole-tree regression

Final independent source/test-bound original readback:
~/.local/state/heptatrader/operator-evidence/train-v2-postmodel-requote-20261012/original-requote-source-v3-post-tests.json

Final immutable SHA256:
b91d9c5f971878972a1b599c4f66c90bd2f7117e555b8ceae664e26912d0b440

The final read saw 1,863 original fully verified EUR/USD model returns.
Seven returns fall OUTSIDE the 252 sealed original numeric segment source
coverage and are UNKNOWN. 1,856 lie inside the actual original sealed
time bounds, of which 1,675 have a post-model-return original locally
available quote still inside the IB-reported expiry; 181 have no such
qualified source. 1,667 fresh records use a quote newer than the model
input. The latest excluded numeric segment remained unsealed; neither
it nor its contents were used to repair the seven unknown returns.

The actual period-scoped quote archive fingerprint is pinned in the
final result, comprising 185,745,806 verified original source bytes,
252 sealed numeric segments and 100 legitimate empty sealed segments.
The result also binds the exact auditor Python source SHA256 and
adversarial unit-test source SHA256. Original account/private prompt,
model output and broker identity bytes remain inside owner-private
inputs and are not exported by this aggregate.

For 1,675 matched upper-bound records, the remaining original quote
TTL at LLM response/decision had P50 3396ms, P90 4410ms, P99 4787ms.
The purely assumed post-return budget counts were 1671 at 100ms,
1660 at 250ms, 1633 at 500ms, 1575 at 1000ms and 1370 at 2000ms.
In the 1000ms upper-bound subset the original LLM action labels are
BUY 274, SELL 284 and HOLD 1017; none is a newly authorized
trade. Do not infer new-policy win rate, propensity or eligibility
from these counts.

Exact-tree tests:
- Post-model quote/source adversarial suite: 30 passing.
- Full combined Python + historical research pytest: 1,997 passing,
  23 pre-existing skipped, 835 passing subtests, zero failures.
- Source/build/ownership partition: 106 passing, zero failures.
- Work was exclusively read-only research plus isolated code/tests/docs;
  0 new IB broker requests, 0 orders, 0 model inference calls,
  0 new signed native Owner privileges, 0 runtime service changes.

Final logs are preserved at:
~/.local/state/heptatrader/operator-evidence/train-v2-postmodel-requote-20261012/
  combined-final-pytest.log
  source-final.log

Independent trader health is not qualified by this research. During
the session Pocket4 Execution remained active with cumulative restarts
296; journal reason includes IB realtime data error 10197. The Laya
process was active but its own job status continued to HOLD resource or
source admission. Full-account PnL was still incomplete. None of these
states was changed by this isolated audit.

**Release state:** RETROSPECTIVE_READONLY_UPPER_BOUND_NOT_ORIGINAL_LLM_POLICY,
all promotion/activation/authorization remain DENY.
