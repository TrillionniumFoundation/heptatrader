# User PAPER scoped owner recovery with retained cross-asset exposure

The mixed user PAPER account has two isolated strategy owners: BTC/PAXOS/USD and
EUR/IDEALPRO/USD. Neither owner is the whole-account finalization owner. Restoring
one recovery receiver must not fail merely because the other owner still holds
its authorized position. A Gateway restart may retain an **unexpired, exact
user-PAPER recovery receiver** in recovery-only state. That receiver's existing
expiry still bounds restart restoration, and an explicit authenticated `paper-resume` is
required before mutation authority returns. Expired, revoked, conflicting,
pending-fence or terminal/finalization records do not become resumable merely
because their token file or durable lease record still exists.

## Exact scope binding

The execution profile explicitly binds the crypto owner with
`HEPTA_IB_PAPER_CRYPTO_AGENT_ID` and `HEPTA_IB_PAPER_CRYPTO_SESSION_ID`.
The corresponding non-crypto user PAPER session is accepted as the FX owner only
when the same configured agent/account/domain is used and the Gateway session
binding contains exactly one reviewed contract:

- BTC owner: `BTC.USD|BTC|CRYPTO|PAXOS|USD`, max session quantity 1 BTC.
- FX owner: `EUR.USD|EUR|CASH|IDEALPRO|USD`, max session quantity 100,000 EUR.

The Gateway/ToolHost repeats this contract-scope proof when consuming a scoped
owner audit. A scoped result cannot be replayed against the other asset or an
arbitrary session.

## Scoped resume evidence

For the crypto owner, session audit/resume can return
`SESSION_CRYPTO_OWNER_ZERO_CONFIRMED` only when the coherent authoritative BTC
position is exactly zero.

For the FX owner, session audit/resume can return
`SESSION_FX_OWNER_ZERO_CONFIRMED` only when the coherent authoritative EUR/USD
position is exactly zero. BTC may remain nonzero under the isolated crypto
owner.

In both cases the authoritative BTC and FX snapshots must share the same Broker
connection epoch and position generation, the account gross must equal the two
reported positions, and the retained cross-asset position must remain within
the existing policy envelope. Active orders, uncertain commands, incomplete
history/risk, post-fill reconciliation, terminal fences, stale generations or
any scope mismatch still block resumption. The release operation independently
repeats the same audit rather than trusting an earlier caller-side flag.

Reported broker position and gross values remain the real whole-account values;
they are never replaced with zero to imitate global flatness.

## Distinct lifecycle operations

Gateway startup may retain an unexpired, non-finalized user recovery receiver
when the global audit reports a non-flat account. `CanResumeExactUserPaperRecord`
requires the same token, generation, account and domain, with no conflicting
record for that owner and no pending/completed durable fence or finalization.
`RestoreLeases` checks the original receiver expiry before and after its audit;
an expired receiver follows the existing recovery/finalization path.
The receiver's current recovery deadline is distinct from the former trading
lease's deadline; an explicit recovery operation can establish recovery-only
authority without restoring trading authority.

Explicit `paper-resume` can handle that exact recovery-only receiver, or an
active user-PAPER bearer with an Execution-only recovery marker. It requires the
original bearer to remain in the host catalog, verifies its generation and
durable owner, audits the server-owned asset scope, releases only that owner's
fence, then commits the next lease generation. Ordinary `renew` and
`renew-active` do not have this recovery-only promotion capability.

The resident driver uses only its own instrument position as a precheck for
scoped resume. That local precheck is not authority: native Execution proves both
asset snapshots and Gateway proves the exact session contract binding before
the owner is released.

`RecoveryAuditOwner`, `FinalizeRecoveryOnlyOwner`, PAPER finalization/ACK and
terminalization retain their account-wide zero-position rules. Scoped owner
resume and active-lease continuation results cannot be used as global zero
proofs. Ordinary renewal does not promote a recovery-only lease.

## Diagnosing a missing session

`SESSION_NOT_FOUND` from tool discovery identifies a missing host-catalog bearer;
it does not by itself distinguish expiry, revocation or a restoration failure.
Use the existing `heptatrader session owner-audit --token-file PATH` with the
original owner token and the deployment's actual Gateway/Execution runtime
configuration to inspect its durable owner and obtain native audit evidence.
The public wrapper forwards this top-level mode but does not load the service
environment. `OWNER_AUDIT_USER_PAPER_SCOPE_REQUIRED` therefore identifies a
missing or mismatched configured scope, not a missing token. Use the existing
configuration; do not guess account, Agent, UID or PAPER authority values to
satisfy the check. This operation does not renew or change the lease, release the native
kill switch or grant trading authority. The native audit can reconcile OMS and
request Broker recovery, so it is not a promise of a side-effect-free Broker read.

`OWNER_AUDIT_EXISTING_TOKEN_REQUIRED` means that token is absent from the durable
store. The native `hepta-gateway-catalog` helper can inspect the existing store
without an Execution request or a lease write. Use the selected release's helper
with the actual configured store, lease-key path and user cleanup interlock. Its
projection contains hashed owner/bearer identities, generation, expiry and
recovery/fence/finalization state. Compare the configured owner hashes to
distinguish a stale token file from an owner with no remaining record. Never
extract bearer values from the encrypted store or edit its records.

A successful owner audit still does not prove that the host catalog has restored
the bearer or that `paper-resume` is admissible. Verify the exact lease state,
current recovery deadline and generation before selecting a recovery operation.
An absent row alone is not retirement evidence or permission to provision a
replacement. A new authorized lease is a separate lifecycle operation; it must
preserve the old owner's settlement evidence and account for driver generation
and any residual native owner fence. After an admissible resume, repeat both
owners' authoritative native health, orders and positions checks before the
separate native-hold release.

Historical attribution has two current limits. Non-provision supervisor requests
can have empty Agent/session fields in their journal records, and automatic
user-PAPER expiry/finalization does not append a supervisor operation record.
An exact-session journal filter therefore cannot prove that no renewal, rotation
or automatic cleanup occurred. Correlate the available supervisor records with
driver state and the Execution OMS `session_owner_recovery_only`,
`session_owner_fenced` and `session_owner_fence_release` events. OMS binds the
session in `trace_id` and the Agent in `source` as `agent.tool:<agent-id>`.
Partial file scans or unverified record chains remain diagnostic evidence.

## Validation

Regression coverage includes both directions:

- zero BTC with retained -100,000 EUR permits only the BTC owner scoped resume;
- zero EUR with retained BTC permits only the FX owner scoped resume;
- global recovery remains rejected while either asset is nonzero;
- wrong agent/session/account, wrong contract binding, incomplete or mismatched
  position generations, active orders, uncertain commands and stale lease
  generations remain rejected;
- same-token resume advances the lease generation and survives supervisor
  restart without changing the other owner's state.

Live PAPER acceptance remains separate from the regression coverage above;
this recovery contract does not replace current runtime evidence.
