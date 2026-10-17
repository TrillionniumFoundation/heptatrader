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
