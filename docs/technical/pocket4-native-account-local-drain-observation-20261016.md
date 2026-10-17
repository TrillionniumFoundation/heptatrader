# Pocket4 original Execution owner: one-lock account-local drain observation

2026-10-16. P0 #331 implementation reuses the ORIGINAL durable
account_new_entry_fenced admission fence, original coordinator mutex and
existing user PAPER system.get_health. It does not create a second owner,
a new trading control API, additional Broker connections, or any order send.

The original user PAPER health previously read the owner fence,
owner recovery-only, account-wide entry fence, mutation block and
risk-dispatch bit through several separate coordinator lock acquisitions.
That can mix native state from different instants. The same-account
maintenance user also could not distinguish an outstanding local Broker
callback, live owned order and unresolved original command.

New ObserveAccountLocalDrain(context) takes ONE coordinator mutex and
returns the original scope's exact session/account fence bits, journal
mutation block, risk dispatch bit, ALL original venue-dispatch count
(including cancellation), outstanding account order owners, hot uncertain
commands and unattributed/unknown account-owned or uncertain records.
Unattributed records and ANY in-flight venue dispatch block local
quiescence conservatively. Missing agent/session/account context returns
present=false, never a fictitious zero. Native health emits these bounded
counts and Boolean as account_local_* metrics without exposing account
IDs, tool request identities or Broker credentials.

The ONLY positive local flag is account_local_hot_projection_quiescent;
it requires original durable account fence, no writer poison/mutation
block, zero owned/uncertain/unattributed commands and zero venue sends.
It is deliberately limited to the coordinator's HOT/active projection.
A cold OMS generation may contain history outside these counters.
It never infers a Broker-global active-order zero, joins IB client-specific
IDs to a completed post-fence Broker download, or proves a protective stop.
Original health permanently emits maintenance_stop_permitted=false:
it is a read-only scoped observation, NOT stop authorization.

Original account-fence regression also pauses an original CANCEL venue
callback and verifies the global-in-flight counter blocks a false idle
acknowledgement despite no new risk-increase dispatch. A returned cancel
callback cannot release the local order owner; only a durable original
terminal record does. Original account-fence regression exercises in-flight
Broker callback,
fencing across another session, not falsely draining after Broker Accepted,
draining only after native original terminal record, foreign-account order,
journal fsync failure and restart recovery. Native CTest remains assert-
enabled. The resident Execution, Gateway and Drivers cannot be restarted
on this read-only observational change; issue #331 stays open until
authoritative Broker-global order/unknown-send and independent protection
prove continuity.
