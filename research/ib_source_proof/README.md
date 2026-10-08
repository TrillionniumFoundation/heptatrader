# Read-only IB EUR/USD independent source proof (negative result)

This module is research-only. It does not modify the executing broker session,
the EURUSD_ORIGINAL_ENTRY_CONF_0.62_USD100K_REFERENCE_NOTIONAL PAPER policy, BTC,
accounting, order permissions, the existing Laya source, or failed experiments.

## Real experiments, 2026-10-08 JST

Two separate API clients contacted the same locally installed official IB
Gateway PAPER port 4002, after strict client-ID and immutable SDK validation.
Neither client could transmit order or account messages. The outbound wire gate
allowed only API start (71), live market-data type (59), EUR.CASH/USD IDEALPRO
stream request (1), its own market-data cancellation (2), and for the first
trial TickByTick BidAsk (97) plus own cancellation (98). Every request was
validated at the EClient.sendMsg byte boundary, and higher-level order/account
methods were disabled. The client IDs 8955 and 8956 were distinct from the
executing client 701 and original NAV observer 8952.

Both 60-second trials successfully passed nextValidId and CASH-farm 2104,
sent their valid read-only subscription requests, completed their bounded
durations, canceled their own subscriptions and disconnected normally.

| Trial | Own quote callbacks | Existing native EUR/USD quote updates | Existing-native previously known, <=5s fresh seconds |
| --- | ---: | ---: | ---: |
| 8955 TOP + TickByTick | 0 | 32 | 56/60 |
| 8956 TOP only | 0 | 34 | 59/60 |

Both independent clients had 0/60 fresh source seconds, 0 order messages and 0
account request messages. Results are derived from independent SHA256-chained
immutable client receipts and native source prefixes on the same UTC-second
grid. The earliest failed engineering smoke exposed an overly restrictive
startApi blank optional-capabilities field; this was fixed before the two
above complete observations. Do not treat that first exception as a quote
subscription failure.

**Interpretation:** the native execution subscriber received legitimate
quotes while each separate API client got no price callback. Cause of the
second-client delivery gap could be entitlement, routing, or subscription
state; it has NOT been proven. A new direct feed cannot replace the native
one and cannot be described as fixed.

## Controls and strict next gate

No interpolated future endpoint, synthetic Broker timestamp, repeated cached
price counted as a new tick, broadened 5-second freshness TTL, broadened
30-second quote-path gap, or relaxed five-minute endpoint tolerance.
The previously rejected 17/24 paired Fusion window remains rejected.

The separate 24-probe source readiness gate must satisfy at least 20 usable
operational cells and at most four missing, plus latest actual quote within
five seconds, before a *new* frozen future Fusion effectiveness trial is
registered. Readiness is not predictive qualification or canonical owner
admission.

The English/mmBERT paired full-checkpoint feature preflight is engineering
only. Current USD100k net-USD A/B requires actual exact-input, original
command, fully settled and actual-commission-complete closed rewards. Old25k
and floating PnL are never admitted.

No runtime trading authority, actor update or model selection is exposed.

## Offline regression commands

    PYTHONPATH=research python3 -B -m unittest discover -s research/ib_source_proof/tests -p 'test_*.py'
    PYTHONPATH=research:research/neuron_learning python3 -B -m ib_source_proof.audit --config PINNED_CONFIG --root FIRST_EVIDENCE --root SECOND_EVIDENCE --output NEW_AUDIT_PATH

The online probe is one-shot only and creates a new immutable evidence root.
The tests use synthetic SDK wire objects and require no Broker connection.

Reference: https://www.interactivebrokers.com/docs/tws-api/
