# Historical data demand probe

The Pocket4 increment attempt on 2026-10-17 connected to the existing paper
Gateway, then stopped with UNKNOWN cash HMDS state and zero historical
requests. Waiting for a farm notice must not be reported as a data request
rejection.

## Explicit configuration

- cash_hmds_startup_wait_sec: a bounded nonzero wait, normally 30 seconds.
- cash_hmds_probe_on_unknown: true to permit one price-bar request after that
  wait expires with no matching farm notice and no connection failure.
- skip_head_timestamp_requests: true. An increment does not need to repeat
  earliest-data queries. The default remains the legacy seven-request plan.

The probe is the first outstanding MIDPOINT, BID or ASK request in the existing
plan. It is not an additional request or a reconnect loop. A completed,
nonempty, error-free response on this connection permits the remaining bounded
requests. Head timestamps, schedules, empty data and cached receipts cannot
establish demand readiness. The observed farm state remains UNKNOWN until an
actual farm notice changes it; a separate receipt records the successful probe.

Any disconnect observed after the probe is armed invalidates this session,
including after a successful probe. A later connected notice cannot revive it. Fatal connection events, pacing errors, request errors and
timeouts stop the session. Existing client 8984 locking, paper-only handshake,
contract and message restrictions, immutable chunks, request spacing, capacity
and historical cutoffs remain enforced.

Old experimental head_probe_on_unknown and unknown_probe_kind configuration
keys are now rejected with a migration error, rather than silently ignored.

## Receipt provenance and counts

Each session records the actual collector module paths and hashes.
requests_sent_this_run is retained for compatibility and counts guard-admitted
SDK dispatch attempts, including an SDK call that raises. The explicit
request_count_scope disclaims wire delivery or broker acknowledgement;
matching original callbacks independently prove actual request completion.
request_methods_invoked_this_run also records calls blocked before transport.
Only an original end callback and durable response establish request completion.

Official references:
- https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes
- https://www.interactivebrokers.com/docs/tws-api/doc/md/earliest-data-point

Tests cover successful data without farm notices, one-request-only behavior,
disconnect/fatal races, failed and empty responses, cached-response misuse,
actual request counts and unsupported old configuration.


## Dataset acceptance

For the new opt-in mode, a RECEIVED chunk alone is insufficient. Dataset
collection requires immutable successful session witnesses for every originally
received chunk, the exact configuration, paper verification and a valid probe
bound to its request ID and response hash. A failed probe cannot be recertified
by a later successful run that merely resumes cached chunks. Existing legacy
source datasets retain their prior acceptance path.

## Pocket4 verification, 2026-10-18

The affected regression suites passed 74 tests with zero skips. The actual
read-only increment completed four requests in 76.019 seconds: 570 MIDPOINT,
570 BID and 570 ASK bars, plus SCHEDULE. The first price request activated an
UNKNOWN farm; IB emitted cashhmds CONNECTED approximately four seconds later.
The collector sent no orders and did not reconnect.

Data validation retained 537 paired bars inside the requested interval. The
284 overlapping bars exactly matched the previous dataset and 253 timepoints
were new, producing 73,992 paired bars through 2026-10-16 20:55 UTC. No missing
scheduled bars, crossed prices or revision conflicts were observed. The
returned calendar header ends at 21:00 UTC; only the explicitly derived
analysis interval through that boundary is proven. The original query through
23:59:59 UTC remains marked incomplete. Historical data does not repair the
original live feed or any past forward trial.

The successful session predates the additional disconnect-latch and source-
acceptance hardening. Its exact executable hashes remain in the immutable
session receipt; the final consumer revalidated that same successful session
and identical paired bars without another broker request.
