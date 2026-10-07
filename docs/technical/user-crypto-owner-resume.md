# User PAPER scoped owner recovery with retained cross-asset exposure

The mixed user PAPER account has two isolated strategy owners: BTC/PAXOS/USD and
EUR/IDEALPRO/USD. Neither owner is the whole-account finalization owner. Restoring
one recovery receiver must not fail merely because the other owner still holds
its authorized position. Expired or restarted leases remain recovery-only on
disk and in the host; an explicit authenticated `paper-resume` is still
required before mutation authority returns.

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

Gateway startup may retain a fenced, non-finalized user recovery receiver when
the global audit reports a non-flat account. Explicit `paper-resume` verifies
the same token, generation and durable account/domain owner, audits the
server-owned asset scope, releases only that owner's fence, then commits the
next lease generation.

The resident driver uses only its own instrument position as a precheck for
scoped resume. That local precheck is not authority: native Execution proves both
asset snapshots and Gateway proves the exact session contract binding before
the owner is released.

`RecoveryAuditOwner`, `FinalizeRecoveryOnlyOwner`, PAPER finalization/ACK and
terminalization retain their account-wide zero-position rules. Scoped owner
resume and active-lease continuation results cannot be used as global zero
proofs. Ordinary renewal does not promote a recovery-only lease.

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

For this change the Python PAPER-driver suite passes 43/43 and the complete
native CTest suite passes 49/49. Live PAPER acceptance remains separate evidence.
