# PAPER host driver contract

Status: CURRENT
Applies to: the portable PAPER-V4 controller and its separately installed host driver
Implementation: `scripts/hepta_ib_paper_harness.py`, `scripts/hepta_paper_rollout_host.py`
Tests: `tests/python/test_paper_campaign.py`, `tests/python/test_ib_paper_rollout.py`

## Responsibility and deployment boundary

The portable controller is reviewed repository code. It controls stage sequencing,
immutable identity, stable command IDs, bounded calls, evidence collection and
per-cycle verification. The host driver supplies the installation-specific bridge
to the **canonical Gateway and Execution Service**, plus read-only authoritative
snapshots and OMS/Broker observations. It must not become a second order authority.

The driver is an external deployment dependency. This repository supplies its
versioned contract and an in-memory test double, **not a qualified real-host driver
implementation**. Passing those tests proves controller behavior, not a Broker run.
Missing or incompatible drivers fail before progression. PAPER SDK/session custody,
root installation, credentials and account qualification are not synthesized here.

The execution user alone retains Broker transport authority. A root-owned driver
file does not imply that the Actions runner runs as root. Host-specific delegation
must remain bounded to the already authorized session and must independently enforce
account, profile, artifact and caller identity. The driver cannot provision an Agent
session, disarm a kill switch, change risk limits, select LIVE, or read another
campaign's credentials based on caller-supplied fields.

## Invocation and wire contract

The controller invokes the digest-pinned regular executable once per request:

```text
<root-owned-driver> --request <private-request.json> --response <new-response.json>
```

The environment contains only `PATH=/usr/bin:/bin`, `LC_ALL=C` and a private `HOME`.
Standard input is closed; ordinary standard output/error are not treated as evidence.
Requests and replies are regular non-symlink single-link JSON files, bounded to 4 MiB,
with duplicate keys and non-finite values rejected. The driver must write a complete
reply atomically and omit credentials, tokens and raw secret-bearing environment.
Every request/reply remains in the campaign's `driver-calls/<request_id>/` directory,
including on subprocess failure or timeout. Calls default to a 120-second deadline.
Timeout is an unknown outcome, never a reason to resend a mutation.

Request fields are exactly `schema`, `request_id`, `operation`, and `payload`.
Schema is `heptatrader.paper-host-request.v1`. Replies have exactly `schema`,
`request_id`, `operation`, and `result`, with schema
`heptatrader.paper-host-response.v1` and the identical correlation fields.
The driver must reject unsupported fields/methods rather than forwarding arbitrary
shell commands, paths or Broker requests. Its effective identity must be measured
from installed runtime state, not echoed from the request.

## Supported operations

| Operation | Input and output responsibility |
|---|---|
| `inspect` | Receives campaign binding and candidate executable path. Independently checks the running candidate, account fingerprint, exact instrument/profile, host identity, authorization, and network/credential isolation. Returns the matching binding, `account_mode=PAPER`, `profile_order_mode=EXTERNAL_P1_CANARY_LMT_DAY`, and positive isolation observations. |
| `barrier` | Receives binding. Requests a fresh authoritative account/order/position refresh and resolves command state. Returns the complete barrier contract below; never treats missing positions or open orders as empty. |
| `place` | Receives binding, cycle ID, stable command ID, instrument, BUY, quantity 1, LMT/DAY. Uses current authoritative quote, Execution preview and bounded permit, then forwards exactly that command through Gateway. A transport timeout remains uncertain. |
| `flatten` | Receives binding, cycle ID, stable command ID and instrument. Uses Execution's authoritative flatten preview and dedicated guarded flatten path. It must not infer an opposite order from the controller's intended quantity. |
| `await_terminal` | Receives binding/cycle/command ID. Reads status and correlated callbacks until that command has an authoritative terminal outcome. Returns `status=terminal`, matching command ID and normalized `journal`/`callbacks` arrays. A place acknowledgement alone is not terminal economic proof. |

Mutation replies are accepted only with matching `command_id` and `status=accepted`.
All other replies stop the stage. The current portable controller does not retry a
partially filled/rejected/uncertain leg automatically. A partial fill may be closed
only through the explicit guarded flatten operation after the prior order is
terminal; incomplete or contradictory evidence stops further cycles.

For native `heptactl`, mutation identity is carried in `--call-id`; it is not invented
as an unregistered tool field. The driver must use actual discovered tool contracts:
`risk.preview_order` / `trade.place_order`, `risk.preview_flatten` /
`trade.flatten_position`, and read-side quote, account, position, order and command
status tools. Broker credentials are never command arguments or evidence fields.

## Authoritative barrier

A barrier contains exactly `observed_at_ms`, `connection_epoch`, `generation`,
`complete`, `positions`, `active_order_ids`, and `uncertain_command_ids`.
The two lists must be complete, not scoped so as to hide unknown account effects.
P1 permits one authorized CASH instrument in USD; an accepted flat barrier lists
that exact instrument with explicit observed quantity zero. Missing identity is not
zero. The driver rejects unexpected account positions/orders before returning a
complete view.

Each cycle starts and finishes flat. Its final generation must exceed the initial
generation in the same connection epoch. Reconnects and unresolved callbacks require
recovery and a new appropriate experiment; the controller does not silently splice
epochs or resume new risk. Across cycles/stages, time must be non-overlapping.

## Evidence normalization, not evidence invention

The executable schemas live in `scripts/verify_ib_paper_rollout.py`; maintained
synthetic examples are in `tests/python/paper_rollout_fixtures.py`. Every normalized
record carries the admitted campaign/account/profile/host binding. Preserve raw,
non-secret extracts and original journal sequence/callback provenance alongside the
normalized records for incident review.

The native OMS may record `order_intent` and `place_send_attempt` before an IB order
ID exists (`-1` at that point). Normalization correlates these through the stable
command ID, canonical request hash and the later service-owned venue correlation.
It must not pretend that the pre-send record already contained a Broker ID, invent
an earlier timestamp, or renumber records to conceal a send-before-durable-intent
violation. The normalized order identity refers to this proven correlation, not to
an invented historical field. Ambiguous correlations are rejected.

Map actual durable intent/send/reconciled transitions to `intent`, `send_attempt`,
`reconciled`; preserve ordering, normalized order fields and timestamps. Fill records
require service-validated Broker execution IDs, positive quantity/price, matching
account/contract/connection epoch and durable command association. A `Filled` status
string alone is not a fill. Exact duplicate executions may deduplicate; conflicting
execution IDs, missing callbacks, overfills or unexplained commands fail verification.

The controller verifies amount, side, limit-price consistency, per-order P1 bounds,
sequential active orders, intermediate position magnitude and net flatness against
both barriers. These are consistency checks, not cryptographic authentication of a
malicious host. Root custody and real Broker qualification remain separate controls.

## Failure and recovery

No method may retry an uncertain mutation with a fresh command ID. The campaign
records `active` durably before calling the host. Failure, timeout, termination or
completion-state persistence failure leaves this identity fenced and its evidence
intact. Rerunning the workflow cannot silently erase the failed attempt.

Recovery means querying the **existing** command/order identities, establishing real
terminal/flat state and preserving a recovery audit. This revision deliberately does
not expose an automatic state-clearing or retry command. Removing campaign files or
choosing a fresh campaign ID is not a recovery procedure. Host custody must refuse a
new campaign while that account/session retains unresolved mutations.
