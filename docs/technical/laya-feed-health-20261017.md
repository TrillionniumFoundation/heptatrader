# Resident EUR quote capture health

The resident parent now publishes a diagnostic heartbeat every five seconds while
the existing read-only collector is running. It reports process state separately
from valid capture counts, failure categories, original last-good quote time and
strict quote expiry. Calendar closure never grants source eligibility.

Startup verifies bounded original journal prefixes to recover the last good quote
and any known later regular-session failure, including quote-free segments. Unknown
or bounded historical coverage is not evidence of recovery. No original journal is
rewritten. The existing source admission and five-second strict TTL are unchanged.

Pocket4 verification: 70 focused unit tests passed. A real ten-second read-only
canary completed with ten AUTHORITATIVE_QUOTE_UNREADY failures, zero valid quotes
and a complete authenticated journal. This is collector health evidence, not
evidence of restored EUR market data. Current Saturday closure prevents a fresh
open-market recovery claim.

Incident evidence: on 2026-10-16 at 16:15:44.647 UTC the execution service recorded
IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197 after the EUR quote stopped advancing
at 16:15:16.801 UTC. IB identifies 10197 as competing live/paper market-data sessions:
https://interactivebrokers.github.io/tws-api/message_codes.html

Deployment uses the existing immutable installer with --only feed. It does not
switch the execution binary, trainer/evaluator runtime or maintenance fences.


## Follow-through on 2026-10-18

The prior Friday failure is also retained in Pocket4's original incident
receipts, not inferred from the weekend calendar:

- `/home/alex/.local/state/heptatrader/operator-evidence/execution-10197-20261016/incident.json`
- `/home/alex/.local/state/heptatrader/operator-evidence/account-entry-fence-20261016/postmerge-live-10197-incident.json`

Both identify the first fatal realtime-market-data failure at 16:15:44 UTC on
2026-10-16. IB's documented 10197 condition concerns competing PAPER/LIVE
market-data sessions. Their retained recovery errors are separate from a
regular market closure. Neither receipt establishes subsequent quote recovery.

A new, distinct upstream outage occurred during the 2026-10-18 follow-through.
At 12:20 UTC the existing Execution cycle was connected with a complete recovery
barrier and an admitted EUR.USD subscription; that did not prove fresh FX data.
Its connection changed and the barrier became incomplete, followed at
12:25:49.683 UTC by `IB_PAPER_BROKER_RECONNECT_UPSTREAM_TIMEOUT`. Subsequent
automatic starts reported `IB_PAPER_ADAPTER_CONNECT_FAILED`. The original log is
retained at `/home/alex/.local/state/heptatrader/operator-evidence/pocket4-optimization-20261018/execution-live-new-incident.log`.
This failure preceded the runtime-configuration consolidation. Gateway login
failure metadata changed during the outage; the desktop was locked, so the
actual login prompt and its specific cause were not inspected.

Reopening acceptance continues to use the existing original-price observer:
fresh original quote timestamps under the five-second TTL, authenticated
source history and the observer's required 13 contiguous five-minute bars.
Calendar opening, a live process or one fresh quote is insufficient to establish
that history. Missing original bars and previously censored trials stay as
recorded; recovery does not backfill old prospective outcomes. The configured
follow-up checks start on 2026-10-18 at 17:20 America/New_York, hourly for four
checks, and report actual restoration, incomplete history or the remaining
source failure.
