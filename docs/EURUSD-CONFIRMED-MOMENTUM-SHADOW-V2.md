# EUR.USD Confirmed Momentum SHADOW v2

## Purpose

This pipeline replaces the fixed `NO_TRADE` research scaffold with a
deterministic, replayable SHADOW strategy. It does not authorize PAPER
mutations and cannot authorize LIVE trading.

The pipeline has eight independent stages:

1. A separately reviewed, root-owned WATCH seam publishes a canonical lease
   receipt and an exported snapshot v2. The strategy programs cannot acquire,
   rotate, or revoke that authority.
2. `hepta_bounded_shadow_observer.py` consumes one immutable sample at a
   10-second cadence, enforces the bounded observation policy, and never
   catches up a missed 15-minute decision slot.
3. `hepta_shadow_market_history.py` appends receipt-bound WATCH samples to a
   canonical hash chain with an atomic incremental head, and materializes
   closed UTC one-minute and five-minute sampled bars from a bounded rolling
   window.
4. `hepta_market_official_source_extractor.py` parses a pinned set of retained
   official calendar and RSS response bytes without network access and emits a
   canonical SHADOW-only extraction receipt and source bundle.
5. `hepta_market_evidence_normalizer.py` verifies the root-owned extraction
   receipt, replays the exact pinned extractor over the retained bytes, and
   emits strict provenance-bound calendar and information artifacts.
6. `hepta_market_context_builder.py` validates read-only WATCH evidence,
   proves freshness, and computes numeric features.
7. `hepta_eurusd_confirmed_momentum_strategy.py` evaluates the pinned
   strategy package and emits either a bounded `TradeIntent` draft or
   `NO_TRADE`.
8. `hepta_strategy_shadow_runner.py` publishes only validator-approved,
   canonical SHADOW receipts and commits state after the receipt is durable.
   `hepta_strategy_replay_evaluator.py` later evaluates SHADOW candidates against
   later authoritative marks with explicit transaction costs.

None of these programs calls an execution or broker mutation interface.

## Installed Files

- `/usr/libexec/hepta_market_context_builder.py`
- `/usr/libexec/hepta_market_evidence_normalizer.py`
- `/usr/libexec/hepta_market_official_source_extractor.py`
- `/usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py`
- `/usr/libexec/hepta_bounded_shadow_observer.py`
- `/usr/libexec/hepta_shadow_market_history.py`
- `/usr/libexec/hepta_strategy_replay_evaluator.py`
- `/usr/libexec/hepta_strategy_shadow_runner.py`
- `/usr/libexec/validate_hepta_strategy_decision_receipt.py`
- `/usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json`

## Evidence Contract

The context builder consumes:

- a canonical `hepta.shadow-watch-snapshot.v2`;
- at least 361 ordered independent authoritative quote updates at a proven
  5–15 second quote gap spanning at least 5,400 seconds;
- exactly seven no-lookahead 15-minute resampled confirmation points;
- at least forty complete, consecutive, closed five-minute OHLC bars;
- a fresh economic-calendar v3 artifact whose attested official source coverage
  includes both EUR and USD for the evaluation instant;
- a fresh information-provenance v3 artifact with the same dual-currency
  source coverage.

Snapshot v2 records collection start, collection finish, and completion time
for every authoritative read. Historical v1 snapshots remain exportable and
replayable, but they cannot prove portfolio freshness and therefore result in
`NO_TRADE`.

Active collection is sealed as `hepta.shadow-market-history-record.v3` at the
fixed capture cadence. A still-fresh quote may repeat its authoritative
timestamp only when its complete normalized quote identity is unchanged; that
capture is marked `quote_changed=false` and cannot increase readiness, feature,
or replay quote counts. Same-timestamp mutation, an update gap above 15 seconds,
or a legacy v2 record in an active probe/replay path fails closed.

Every history append also consumes a canonical
`hepta.shadow-watch-lease-receipt.v1`. The receipt must be a root-owned,
single-link regular file with mode `0400` or `0440`; it binds the WATCH
generation, accepted lifetime, trust domain, fixed Agent UID, operation, and
the previous receipt digest for a rotation. A generation rollover is accepted
only when the receipt chain is exact and the capture gap remains at most 15
seconds. Any rejected capture emits one typed `SAMPLE_REJECTED`, accounts the
rejected and skipped slots, closes the current segment once, and leaves the
observer terminally `STOPPED`; it never creates a replacement-segment storm.

In trust-domain mode, a root-owned WATCH custodian transaction publishes
`ACTIVE` only after the supervisor acceptance, exact generation, root fence,
Agent token, and canonical receipt all agree. The reviewed host bootstrap must
prove that durable `ACTIVE` state before it starts custodian supervision or
collection. Only the custodian may call the low-level session bootstrap,
rotate, reconcile an ambiguous result, or close the generation; strategy,
observer, collector, exporter, and campaign code cannot do so. The root
exporter copies the validated receipt byte-for-byte to a separate
root-to-reader `0440` path, so the observer never needs access to the Agent
token directory.

A contiguous segment also binds one execution-service epoch, fencing
generation, tool catalog, and descriptor set. Custodian-supervised collector
oneshots may repeat only while that authoritative execution epoch and durable
WATCH transaction remain active. If the execution service restarts, the
observer closes the segment and stops; only a newly admitted campaign may
warm up from zero, so evidence is never spliced across an unproven authority
transition.

Production P1 never preloads this segment from a probe or an earlier campaign.
The 361-quote count needs 3,600 seconds at the fixed ten-second cadence, while
the stricter 5,400-second span needs 541 samples.  Forty consecutive closed
five-minute bars are the controlling requirement at 200 minutes.  The formal
warmup therefore uses the full 210-minute materialization window, leaving a
bounded phase/start margin before the first decision.  The independent load
probe is dispatched 20 minutes before that formal warmup anchor and is closed
before the fresh formal WATCH/history segment begins.

History append is constant-work with respect to segment length. Each immutable
record has a fixed sequence name and advances `history-head.json` atomically.
A published record without a committed head requires an explicit full-chain
recovery; legacy hash-in-filename directories require a new provisioned
segment and are never silently migrated. Normal materialization reads a
210-minute tail window, while terminal observation cleanup performs a complete
hash-chain audit for every segment before the observer may enter `COMPLETE`.
Large raw snapshots and sampled payloads are ephemeral after their immutable
history record is committed; the packet, source-window manifest, evidence
artifacts, decisions, and final audit receipt remain durable.

Calendar and information provenance is provable only through
`hepta.market-source-bundle.v2`. It references a canonical root-owned,
non-writable extraction receipt under the configured trusted root. The receipt
pins the deterministic extractor ID, version, and code SHA-256; binds retained
raw payload paths and byte digests; states explicit EUR/USD coverage and
completeness; and binds the exact semantic events/items digest. The normalizer
requires the installed extractor's actual source SHA-256 to equal the production
pin, re-hashes every retained payload, replays the pure extractor, and requires
the replayed sources, completeness, events, and items to equal the receipt
byte-for-byte after canonicalization. The context builder later reopens and
revalidates the receipt and payloads before setting `provenance_provable=true`.
Legacy source-bundle v1 and derived calendar/information v2 artifacts remain
replayable for audit, but they are deliberately unprovable and therefore force
`NO_TRADE`.

### Official source extraction

The production extractor accepts exactly five root-captured HTTPS response
bodies:

- the BLS Economic News Release Schedule iCalendar;
- the Federal Reserve FOMC meeting calendar;
- the ECB Governing Council meeting calendar;
- the Federal Reserve all-press-releases RSS feed;
- the ECB press RSS feed.

URLs, provider roles, media types, and the supported RFC 5545, HTML, and RSS
shapes are pinned in code. Unknown properties, missing entries, duplicate IDs,
unexpected redirects, malformed dates, changed page identity markers, and
unsupported source sets fail closed. BLS event timestamps are converted with
the versioned US-Eastern rule embedded in the extractor. Official calendar
pages that publish only a date are never assigned a guessed announcement time:
the extractor emits 45-minute high-impact sentinels over a conservative UTC
guard interval around the whole named day. Only the two-day lookbehind and
fourteen-day lookahead are materialized as events; the complete parsed calendar
range remains bound in the receipt.

RSS items and their oldest coverage boundary derive from every item in the
retained XML. The coverage end is the exact successful HTTPS `fetched_at_ms`
attested by the separate root capture boundary. It is not inferred from the
latest publication time: a quiet official feed remains complete at a later
successful fetch. The extractor itself has no HTTP, socket, broker, PAPER, or
LIVE capability.

The evidence root must be owned by the trusted UID and not group/world
writable. The capture manifest, extraction receipt, and every retained payload
must also have one link and have no write bits.
The normalizer rejects symlinks, hard links, owner-writable `0600` evidence, and
path/inode changes during a read.

```sh
/usr/libexec/hepta_market_official_source_extractor.py \
  --capture-manifest /var/lib/hepta/market-evidence/capture-0001.json \
  --evidence-root /var/lib/hepta/market-evidence \
  --receipt-output /var/lib/hepta/market-evidence/receipt-0001.json \
  --bundle-output official-source-bundle.json
```

Every input is strict JSON, rejects duplicate keys and non-finite numbers, and
is represented by a SHA-256 evidence reference. The strategy package digest
binds the strategy configuration, evaluator, context builder, and shared
contracts source. Every information packet has a body digest which the
evaluator independently recomputes before making a decision.

## Decision Rules

The v2 research strategy only considers the `trend` regime. It requires:

- aligned quote momentum, EMA separation, and EMA slope;
- bounded spread and realized step volatility;
- expected movement at least three times estimated cost;
- no high-impact EUR or USD event exclusion window;
- authoritative, fresh, flat portfolio state;
- complete calendar and information provenance.

Any failed hard gate produces `NO_TRADE`. Confidence or narrative text cannot
override a hard gate. News is evidence and a risk veto only; it is not
directional alpha in this version.

## Example

```sh
/usr/libexec/hepta_market_context_builder.py \
  --campaign-id eurusd-shadow-research \
  --iteration 1 \
  --evaluated-at-ms 1800000000000 \
  --strategy /usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json \
  --snapshot snapshot.json \
  --quote-history quotes.json \
  --bar-history bars.json \
  --economic-calendar calendar.json \
  --information information.json \
  --output packet.json

/usr/libexec/hepta_eurusd_confirmed_momentum_strategy.py \
  --strategy /usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json \
  --packet packet.json \
  --output decision.json

/usr/libexec/hepta_strategy_shadow_runner.py \
  --campaign-id eurusd-shadow-research \
  --iteration 1 \
  --evaluated-at-ms 1800000000000 \
  --policy observation-policy.json \
  --strategy /usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json \
  --snapshot snapshot.json \
  --quote-history quotes.json \
  --bar-history bars.json \
  --calendar calendar.json \
  --information information.json \
  --receipt-output receipt-0001.json \
  --state state.json

/usr/libexec/hepta_bounded_shadow_observer.py \
  --campaign-id eurusd-shadow-research \
  --policy observation-policy.json \
  --strategy /usr/share/heptatrader/strategies/eurusd-confirmed-momentum-shadow-v2.json \
  --snapshot snapshot.json \
  --watch-lease-receipt watch-lease-receipt.json \
  --source-bundle official-source-bundle.json \
  --artifact-root observation-artifacts
```

The observer and runner produce SHADOW evidence only. They cannot open a
campaign, acquire WATCH authority, preview an order, or access a broker. The
observer is intentionally one-shot: an external bounded controller supplies
each snapshot and invokes it at 10-second cadence. The existing legacy
15-minute WATCH timer is not a substitute for this collection cadence. The
trusted root host bootstrap keeps one authoritative read-only execution epoch
open for the segment and asks the custodian to rotate the exact current
generation before the one-hour supervisor limit. The bounded controller only
consumes exported evidence; it never invokes the low-level bootstrap or
session supervisor.

Ending or abandoning the segment requires a custodian closure receipt for the
exact campaign and generation. Closure is valid only after authoritative
exact-generation revoke or exact receipt expiry and zero remaining Agent
token, root fence, lease receipt, and exported snapshot/receipt evidence.
PAPER, LIVE, mutation authority, and direct broker access remain false in both
the active transaction and closure receipt.

A completed bounded observation also requires a canonical
`hepta.bounded-shadow-final-audit-receipt.v2`. Its sealed body binds the total
sample count and both missed-sample and missed-decision counts; completion
requires zero missed counts and exact reconciliation with the retained segment
record counts. If any segment audit, storage reconciliation, count
reconciliation, or receipt publication fails, state remains
`FINAL_AUDIT_REQUIRED`; a retry runs only the finalizer and cannot consume a
new market sample.
A separate risk challenger, campaign policy, and explicit PAPER mutation
authorization remain mandatory before any execution path may consume a trade
intent.

## Promotion Gates

Before a PAPER canary, require:

- 10–20 real trading days and at least 200 valid two-minute SHADOW decisions;
- more than 99% complete evidence packets;
- zero authority, audit, or cleanup failures;
- walk-forward and final out-of-sample replay with spread, slippage, and delay;
- stable performance across time-of-day and market regimes;
- independent review of drawdown, concentration, and failure-mode tests.
- at least 72 consecutive real `CLOCK_BOOTTIME` hours with the declared fault
  plan; shorter or accelerated evidence remains `NO_GO` and never authorizes
  PAPER or LIVE.

PAPER order placement and every LIVE capability remain outside this document's
authorization boundary.
