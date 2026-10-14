# Slow forecast / fast action: separate research boundary

`research/forecast_action_boundary.py` defines a new opt-in, broker-free policy
boundary. An immutable forecast records original input, model/policy identity,
start, production and actual durable-commit time. Final repricing consumes a
genuinely new authoritative quote observed after the forecast commit. The old
input expiry is retained, not extended or relabeled as an execution quote.

Final expiry is the minimum of the new quote's original expiry and two declared
forecast/input-age bounds measured from the original input time. Instrument and
source epoch must agree. Policy/model drift, repricing beyond its declared bound,
future/late timing or reused quote identity rejects. BID/ASK executable-side prices
are used for the research edge; a configured margin is not proof of realized
fees or net profit. `require_durable_before_deadline` checks a clock sampled after
the caller's actual fsync; expired durable decisions are not submitted or rebound.

This module does not authenticate arbitrary caller data by itself. Its consumer
must validate the source, actual timestamps and forecast journal. The companion
`forecast_action_probe.py` uses the existing original Laya chain parser and checks
its exact prefix bytes. It never calls market.get_quote or another Broker method.

The bounded probe predeclares one 60-second new-source window and 6.2-second
controlled latency, with a zero-alpha original-midpoint forecast. It writes plan,
forecast, decision and result/failure once in a separate private directory. It
checks new real quote delivery and journal timing, not remote-LLM latency, model
quality, old cohort recovery or trade execution. No original plan, trial, source,
worker, model selection or execution profile is changed. A timing PASS has
authorization_effect NONE and cannot release the current PAPER stop.

## Observed new-source timing, 2026-10-14

A single new window began at 2026-10-14T13:17:55.882000+00:00. The existing Pocket4
original feed was read without a new Broker request. After the declared 6.2-second
controlled delay and forecast fsync, the old input quote was expired. A genuinely
new quote arrived and its decision was durable 593 ms after its source timestamp,
leaving 4,407 ms before its unchanged original expiry. The four original result
files were copied to qian-qi, their remote digests matched, and the actual decision
and post-fsync deadline were independently recomputed with the same source code.
Result SHA-256: `896e174ef2f0acd1e700f23f6faf1dc06c4710266464576fbfa45f77452dc2c8`.

This is one real-source transport/journal timing check with a zero-alpha baseline
and injected delay, not measured remote LLM inference, economic model validation,
SLO/durability certification or trading qualification. The feed exposes a
subscription identity; the probe uses that as its local scope and does not prove
a Broker connection-epoch transition. A trading consumer must obtain authoritative
connection identity through its existing runtime. No new order or Broker data
request was issued; no production feed/worker/stop state was modified.
