# Autonomous PAPER Campaign

## Boundary

This control plane authorizes bounded IB PAPER campaign cycles only. It never
authorizes LIVE, never connects to a broker, never carries a session token or
preview permit, and never replaces HeptaTrader risk, OMS, execution, events,
or reconciliation.

The untrusted Agent keeps using the canonical Agent OS tools. The root campaign
operator only decides whether one exact canonical intent may enter the existing
5–20 second one-shot marker window. V5 has two exact policy shapes: the
`external-p1-finalized` LMT path combines the independently finalized P1 graph
with the certified installed-byte closure; the `local-only` MKT path binds the
same certified installed bytes while retaining the proven local campaign
recovery, one-active-order, 24-hour/720-cycle, and forced end-flat boundaries.
The legacy local-only v4 authority shape remains quarantined.
The external shape is narrower: exactly one EUR.USD `LMT/DAY` canary, quantity
one, one active order, an exact 300-second policy window, and mandatory
end-flat. It requires the canonical
root-owned v2 WATCH-to-PAPER handoff and its forward-only restoration evidence;
the live `alpha.env` must still be the exact restored dormant PAPER profile.
The same handoff must also seal the forward-only runtime-profile hardening
transaction, with both candidates absent; `alpha.ib-paper.env` must remain the
exact reviewed 767-byte artifact (SHA-256
`99dd8ab1cd612989906a972abcaad0dd4234d908ea4ce295c0c01a9059604ee4`).
External consumers never rewrite either profile.

The PAPER admission verifier is read-only evidence machinery. Its
`hepta.paper-testing-admission-candidate-receipt.v1` output always says
`paper_authorized=false`, `live_authorized=false`,
`mutation_authorized=false`, `direct_broker_access=false`, and
`order_submission_authorized=false`. A `GO` candidate is a prerequisite, not
authority. Only an explicit active campaign policy v3 or v5 can grant the
separate bounded PAPER mutation authority described here; v5 is required for
the 24-hour local campaign path, and no policy can grant LIVE.

## Default state

The installed `hepta-ib-paper-campaign-policy-v1.json.example` is a legacy,
disabled example:

- `enabled=false`
- `mutations_authorized=false`
- `paper_only=true`
- `live_authorized=false`

Installing packages or enabling the socket does not authorize a campaign.
Policies v1 and v2 are accepted only when both `enabled` and
`mutations_authorized` are false, and an `open_cycle` can never use either
legacy shape. An active campaign requires an exact compact-canonical v3 or v5
document. V5 `admission_mode=local-only` is the bounded MKT/DAY path; it is
created only from a disabled seed plus a certified deployment closure and does
not claim external P1 evidence. Active v4 documents are rejected with
`CAMPAIGN_POLICY_V4_ACTIVE_P1_ADMISSION_REQUIRED`.

A root operator must separately provision
`/etc/heptatrader/paper-campaigns/<domain>.json` as a single-link
`root:root 0600` file and explicitly bind a campaign ID, strategy ID/version
and digest, time window, EUR.USD scope, quantity, cycle count, cooldown, and
one-shot TTL. Policy v3 additionally carries:

- `source_baseline_sha256`, the frozen source baseline digest;
- `admission_receipt_name`, one safe `.json` filename below the fixed
  `/var/lib/hepta/paper-testing-admission` root;
- `admission_receipt_file_sha256`, the digest of the complete canonical
  candidate file; and
- `admission_receipt_body_sha256`, the candidate's sealed body digest;
- the absolute current-finalization pointer path and its complete-file and
  sealed-body SHA-256 digests; and
- the exact `ADMISSION_GO` tombstone path and its complete-file and sealed-body
  SHA-256 digests.

Policy v5 accepts two disjoint exact field sets. The external shape is the
union of terminal v3 bindings and certified deployment identity and requires
`admission_mode=external-p1-finalized`, including direct P1 audit and production
WATCH-to-PAPER handoff pins. The local shape requires
`admission_mode=local-only`, `MKT/DAY`, EUR.USD only, 25,000 maximum quantity,
one active order, 24 hours/720 cycles maximum, certified deployment
file/body/transaction pins, and end-flat. It contains no P1 or WATCH fields and
therefore makes no external-P1 claim.
The external shape additionally fixes `max_cycles=1`, `max_quantity=1`,
`max_active_orders=1`, an exact 300-second window, and the canonical handoff path
`/var/lib/hepta/p1-admission/p1-watch-to-paper-handoff-receipt-v2.json`.
Handoff v1 is never accepted.

The optional `--admission-root` changes that fixed root for a controlled test
or deployment environment. It is an operator startup setting, never a request
field the Agent can choose. Every root path component is opened relative to an
already-open directory with no-follow semantics. The final directory must be
root-owned and not group/world writable. The candidate must be a single-link
`root:root 0600` regular file; symlinks, hard links, unsafe metadata, unstable
reads, and component rebinding fail closed.

Before the candidate may be consumed, the operator takes the nonblocking
exclusive PAPER host-authority lease and securely reopens the pinned pointer,
tombstone, candidate, and zero-exposure receipt. The owner must be absent, and
the complete terminal graph must agree on reservation generation and
predecessor, boot, domain, campaign, frozen source, candidate and zero receipt
references, and lease identity. A candidate left behind by a crash before
finalization is therefore never authority.

The pinned candidate must have the exact v1 receipt and 17-input binding
shape, compact canonical bytes, a valid sealed body, `status=GO`, no findings,
`paper_test_admission_candidate=true`, the same domain, campaign, frozen source
digest, and P1-audited `strategy_sha256` as the policy, and
`authorization_effect=NONE_READ_ONLY_CANDIDATE_ONLY`. Every authority flag
remains false. Its evaluation may equal the current millisecond but cannot be
future-dated, and expiry is strict:
`evaluated_at_ms <= now_ms < expires_at_ms`. V3 retains the rule that its
complete active window plus one operator TTL fits inside candidate expiry. V5
requires freshness for the first cycle only and consumes the finalized graph
once under the host-authority lease into an operator-owned root-only receipt.
Later cycles must securely reopen that receipt plus the immutable policy,
finalization and deployment pins; they do not reinterpret an expired candidate
as fresh authority. Missing, changed or cross-boot consumption state fails
closed and halts the campaign.

Those 17 direct inputs are the frozen source baseline; install manifest,
installer receipt v4, and current-install pointer; profile receipt;
activation receipt; P1 audit; full-P0 release-validation closure; Agent OS,
dual-domain, inert PAPER-domain, P1 liveness, broker-network v3, hard-network,
and three-native-VM gates; WATCH-to-PAPER handoff; and authoritative zero-
exposure receipt. An indirect aggregate, runbook assertion, rehearsal-only
report, or older input shape cannot replace any member.

The P1 audit JSON is content-sealed but is neither signed nor self-attesting;
canonical bytes alone are not an endorsement. The trust link is the
production-root WATCH-to-PAPER handoff. It reads the audit as an exact
root-owned `0600` input through anchored trusted directories, securely reopens
it across the mutation transaction, and records its exact file/body hashes.
Admission accepts no legacy handoff shape: the handoff must declare
`production_mode=PRODUCTION_ROOT_SYSTEMD`, identify the fixed installed
producer, bind that producer hash to the frozen source baseline, and bind the
same P1 audit file/body hashes consumed by admission. Thus a fabricated audit
or standalone candidate under non-root control cannot substitute for the root
handoff endorsement.

V3 remains valid for at most four hours and 20 cycles. External-P1 v5 is
exactly one quantity-one `LMT DAY` cycle; local-only v5 is bounded separately
to 24 hours and 720 `MKT DAY` cycles. Both retain one active order, end-flat,
canonical `EUR.USD/CASH/IDEALPRO`, and `live_authorized=false`. External v5
requires BUY limit equal to the observed ask and SELL limit equal to the
observed bid.

The v4 schema still describes the bounded MKT/DAY intent and deployment pins
needed by offline validation, but it is not an active PAPER contract. Its
disabled example and certified deployment-evidence recorder remain available;
neither can disarm the domain, preview, or submit an order. V5 performs the
required direct P1/handoff/finalization/source/strategy/domain/campaign/window
and deployment binding under the root lifecycle and authority locks. A
caller-supplied or merely self-consistent digest still cannot substitute for
the independently finalized inputs.

## Runtime protocol

`hepta-ib-paper-campaign-operator@<domain>.socket` is owned by the exact
`hepta-agent-<domain>` identity. The root service verifies `SO_PEERCRED` against
the root-owned trust-domain manifest before accepting a request.

The Agent uses `/usr/bin/hepta-campaignctl`:

1. `status` reads campaign state without changing the kill switch.
2. `open_cycle` sends the complete canonical TradeIntent plus its computed
   digest and an authoritative preflight receipt digest.
3. Root takes the PAPER host-authority lease and securely reopens the active
   v3/v5 policy. It also verifies the exact pinned finalization pointer,
   `ADMISSION_GO` tombstone, admission candidate, zero-exposure receipt,
   freshness, and lineage. It validates the complete intent, per-intent
   strategy ID/version/SHA-256, quantity, timestamps, cooldown, and cycle
   budget before any one-shot disarm call. V5 also exact-matches the direct P1
   and handoff pins and the certified deployment closure. Active v4 is rejected
   before any admission/deployment provider or one-shot disarm call.
4. Root calls the existing one-shot authority. The watchdog is active before
   the marker is removed.
5. On the first v5 cycle, Root atomically persists the campaign consumption
   receipt before disarm. After disarm, Root securely reopens the policy,
   finalized graph, consumption receipt and deployment snapshot while still
   holding the lease. V3 revalidates candidate freshness on every cycle; v5
   revalidates the recorded first-consumption time and all immutable pins.
6. Any applicable policy/admission-graph drift, replacement, expiry, or invalid watchdog
   deadline immediately re-engages the marker, leaves `cycles_opened`
   unchanged, and halts the campaign. Only an exact second validation commits
   state to `open`.
7. The Agent calls canonical `risk.preview_order`, then
   `trade.place_order` at most once.
8. Immediately after `place` returns, including reject or uncertain, the Agent
   calls `close_cycle`. Root re-engages the marker before acknowledging close.
9. Fill monitoring, cancel, atomic reduce-only flatten, and final reconcile
   remain canonical Hepta Agent OS operations with broker-owned authority.

Every request ID is persistent and idempotent. Reusing a request ID with a
different payload is rejected. Policy drift, campaign expiry, an interrupted
open/close, or an expired one-shot window re-engages the marker and halts that
campaign. The Agent cannot resume a halted campaign; root must provision a new
campaign ID.

## Shadow-first rollout

Strategy reasoning stays separate from this root control plane. Before any
mutation policy is provisioned, run the strategy continuously in shadow mode:

- build authoritative market/account/order/position/risk snapshots;
- record both TradeIntent and `NO_TRADE` decisions;
- keep evidence provenance and timestamps;
- never call `open_cycle`, `risk.preview_order`, or any `trade.*` tool;
- replay and review enough market sessions before requesting a bounded PAPER
  policy.

The P1 window freezes 10–20 actual trading days and must contain at least 200
eligible decisions at a real two-minute cadence, with strictly greater than
99 percent completeness and no catch-up decisions. A campaign-level
`CLOCK_BOOTTIME` continuity chain spans at least 72 consecutive real hours,
including the guarded projection/teardown gaps. The frozen fault plan,
independent fault results, recovery and cleanup receipts,
service/lease/fence epochs, and independent auditor GO must all bind the same
source, strategy, boot, and campaign, with zero authority, audit, or cleanup
failure. Short or accelerated rehearsals are diagnostic only and must remain
`NO_GO`; they can never emit a PAPER admission candidate. This profile never
authorizes LIVE.

Before P1 observation begins, Root may create only the missing default-engaged
alpha marker with the fixed installed
`/usr/libexec/hepta-p1-paper-kill-switch-bootstrap --run`. The required pins
are the expected PAPER UID/GID, frozen source-baseline SHA-256, and installed
producer SHA-256. Its
`hepta.p1-paper-kill-switch-bootstrap-receipt.v1` must say
`operation=ENSURE_ENGAGED_NON_AUTHORIZING`, `status=COMPLETE`, marker state
`engaged`, and every PAPER/LIVE/mutation/direct-broker/order authority flag
false. Do not call the domain-authority producer to create this prerequisite:
that would manufacture authority before admission.

Campaign implementation and package installation do not authorize starting a
shadow service, enabling this socket, provisioning an active policy, or
submitting an IB PAPER order. Each remains an explicit operator action.
