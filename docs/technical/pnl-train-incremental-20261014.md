# Completed-day incremental IB history, 2026-10-14

Research-only, no order capability or strategy cutover. Legacy 24-hour cutoff
and 30-day requests remain default. The explicit COMPLETED_UTC_DAY policy only
permits UTC 23:59:59 strictly before today's UTC midnight and at least five
minutes in the past. window_days is an integer 1..30; non-30-day windows require
exactly one chunk. The wire guard binds the bounded duration to the frozen plan.
Client 8984, process-global lock, original 15-second pacing, immutable requests
and receipts, no retry of UNKNOWN, request errors or unfinished chunks retained.
Head timestamp requests are cancelled using only their own read request IDs.

Target for this execution: end 2026-10-13T23:59:59Z, seven-day window, one chunk,
MIDPOINT/BID/ASK plus IB schedule and heads; overlap with the frozen prior dataset
is compared rather than overwritten. Revised historical bars cannot backfill any
old live missed/censored prediction, nor serve as contemporaneous reward.
