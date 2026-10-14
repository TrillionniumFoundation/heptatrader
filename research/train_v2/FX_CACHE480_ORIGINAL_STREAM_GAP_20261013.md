# Original IB PAPER quote stream incident: exact source failure provenance

**Date:** 2026-10-13 local UTC+8. **Outcome:** `HOLD` / read-only
forensics. No source data, trader, existing monitor, decision, or
production state was edited. This report audits the **first** cycle of
the already-running 480-cycle 25%-CPU verified-cache source-health
experiment, not a new historical experiment or a new IB request.

## Evidence already available, pinned before examination

- Frozen 480-cycle plan (same original EUR.USD PAPER period)
  SHA256 `8a77da0fcdd3d251a19d53209fa7a836386848380f92e3681590b33ce2bf98d5`.
- Exact first immutable cycle health snapshot SHA256
  `10287642a8d196cc7bbed8783dfa9b8fad2531495de10a74f49d090719e94292`.
- Exact original first-cycle status/decision receipt SHA256
  `7f2f88ca47d181a0a8415c427913a848834a487f8b731f8e9fa3183a4206226b`.
- Those bytes store **19/24** original source-health probes marked
  USABLE; **two** `ENDPOINT_GAP` and **three** `PATH_GAP`, with
  original `HOLD_NEW_FUSION_WINDOW`, no observer-order authority.
- The status captures 12 original SHA256-bound source/archive
  prefix records. The verifier opens each original file O_NOFOLLOW,
  owner-private, read-only, verifies exact prefix byte SHA256 even
  after subsequent append, and independently replays each
  `heptatrader.laya-shadow-quotes.v1` record sequence, `previous`
  chain, SHA256, local observation clock, PAPER period, and legitimate
  quote bid/ask/expiry. Archive pages are verified at the byte level;
  quote records are independently parsed. No post-snapshot extra bytes
  may change the reconstruction.
- The first verifier attempt rejected a **mistyped hard-coded receipt
  hash** before producing any positive result; the literal was
  corrected and the code rerun. Source files, historical cohorts,
  original receipt and original HOLD did not change.

## Exact recovered local source incident

At original quote event time on **2026-10-13**:

| Pocket4 local time (UTC+8) | Original SHA-chained observation |
|---|---|
| 17:29:12.527 | Last distinct verified original quote before the gap |
| 17:31:15.622 | First distinct verified original quote after the gap |
| **Gap** | **123,095 ms** without a new verified quote event |
| Collector receipt gap | **119,190 ms** between the last and next quote record |
| Original collector quote record sequences | **1282 -> 1344** |
| Intervening original `FAILURE` records | **61** |
| Quote source file | The **same** original 30min segment, not a normal segment rollover |

The 61 actual source failures are part of the original hash chain:

| Error code | Count |
|---|---:|
| `AUTHORITATIVE_QUOTE_STALE` | 32 |
| `AUTHORITATIVE_QUOTE_UNREADY` | 13 |
| `EXECUTION_EVENT_RESPONSE_READ_FAILED` | 6 |
| `EXECUTION_EVENT_SERVICE_NOT_READY` | 1 |
| `JSONDecodeError` | 9 |

The before/after quote subscription identities were different. The
report keeps only SHA256 fingerprints of those subscription strings,
not the original identifiers in code/GitHub.

Cross-checking the same two original event times against the first
recorded 24-probe health result:

- Both `ENDPOINT_GAP` probes are **numerically reproduced exactly**:
  the first subsequent quote arrived 74,800 ms and 15,584 ms
  beyond their respective previously frozen due times, both >5,000ms.
- All three `PATH_GAP` probe observation windows **overlap** this
  independently verified >30-second local quote stream gap.
- All five affected probes correspond to the preexisting original
  monitor's negative states; no result was rewritten or upgraded.
- Two later `INPUT_UNAVAILABLE` statuses in subsequent 480-cycle
  observations coincide with the same local-source gap/warm-up period;
  they were not used to alter the original first-cycle denominator.

**Important causal limit:** the source collector emits a read-only
`market.get_quote` query once per second. The collector was recording
`FAILURE` rows during the gap and its original source record SHA chain
continued. Therefore this is **not merely a quiet/no-price-change
market** and not simply an expired monitoring status. But the
recorded error names alone cannot unambiguously distinguish upstream
IB quote invalidity, lost Tool Gateway connectivity, partial native
event response, a broker subscription recreation, or contention.
The old Tool Gateway and feed user services show no overall
restart here; this does **not** prove each endpoint remained
healthy. There is no independently IB-signed snapshot, official
market session proof for the precise instant or production Witness.

## Independent physical qian-qi byte-level reparse

The qian-qi host received **only** the original 771,978-byte prefix of
the implicated source segment, plus its previously sealed plan, first
health JSON and receipt; files are 0600 within a 0700 private directory.
The file's *exact first-prefix* SHA256 was
`b20e5e067c239df5b3696b54200f858d8edc2058ec7bf8826aae6a21d6e2d73f`,
not the later complete segment's SHA after subsequent appends.

Independent stdlib verifier:
`research/train_v2/fx_original_cache480_gap_independent.py`.
No network or broker SDK. It parses every source record, verifies the
sequence, previous/hash chain, PAPER header, quote event availability,
and the original status's disjoint 24-probe states independently of
the Pocket4 research algorithm.

An initial qian-qi run **failed closed** when it incorrectly required
every row in the *captured source prefix* to be available before the
source monitor's original `at_ms`. That full prefix includes records
appended **during** the assessment. The final verifier validates the
full raw SHA chain but uses **only** events with original local
`at_ms <= monitor.at_ms` for its causal source-gap attribution.
It does not use future quote rows to requalify the original HOLD.

The final independent qian-qi receipt confirms exactly:
`event gap=123095ms`, `FAILURE=61`, `negatively impacted probe=5`,
original HOLD, and no broker order, signed IB quote or model promotion.

Private receipt:
`~/.local/state/heptatrader-fx-cache480-gap-crosshost-20261013/qian-independent-gap-v2.json`

SHA256:
`741db965db7dec2cc3786d67ce3ed06d4e8d595f636d11f4920b08a313da234e`.

The same independent receipt is retained on pocket4 under the
`fx-cache480-original-gap-forensics-20261013` operator evidence folder.
All failed preflight attempts remain described rather than being
misrepresented as source-tamper incidents.

## Exact next repair gate (no unauthorized production cutover)

1. Independently bind the Tool Gateway's and execution event-service's
   **17:29–17:31** source-side error timeline to immutable service
   epochs/counters; do not use lifetime totals as proof a particular
   event failed. If original logs are missing/rotated, explicitly HOLD.
2. Reproduce a false/partial tool response in an *isolated, mock quote
   transport*, with bad JSON, service unready and stale IB quote.
   Client must **fail closed**, preserve the distinct reason code,
   avoid order effects, and recover on the next genuinely authoritative
   fresh IB quote, never retaining or refreshing a stale timestamp.
3. Inspect actual original quote subscription lifecycle and subscription
   changes using only supported read-only diagnostics. **Do not**
   auto-resubscribe the production broker from a research analyzer or
   relax 5s original quote TTL / 30s path thresholds.
4. Require sustained source continuity with all first-party failures
   preserved, independent cross-host exact-byte replay and rollback
   qualification before a permanent health monitor switch.
5. Separate trade/Train V2 admission remains HOLD; the current
   15/30/60/120m study had **0/4** eligible entries and **0/16**
   maturity rewards. Original Owner/Witness/LedgerWriter, complete
   Activity/Flex/Nav and statistically credible after-cost alpha
   remain separate hard gates.

## Code, report, tests

`research/train_v2/fx_original_cache480_gap_forensics.py` reads only
original immutable campaign records and private quote-chain files.
Its output is O_EXCL, fsync'd, 0600 under the normal private 0700
operator-evidence directory:

`~/.local/state/heptatrader/operator-evidence/fx-cache480-original-gap-forensics-20261013/first-cycle-gap-v1.json`

Report SHA256:
`7b415e0dc4fc55d99610531242fa48f167958752b900300a822b326b2a602c49`

Tests reject source record tampering, self-consistent mismatched PAPER
period, duplicate JSON, nonfinite data, invalid quote, public/symlink
source, altered historical prefix, changed timestamp prices,
falsified endpoint delay, invented gap overlap, or zero-length gaps.
Source quote collection, trader APIs, existing health monitors, FX/BTC
driver/PnL services and active 480-cycle precommit are all untouched.
