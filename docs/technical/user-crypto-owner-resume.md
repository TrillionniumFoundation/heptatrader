# User PAPER crypto owner recovery with a retained FX position

The user crypto session is not the whole-account finalization owner. Restoring
its recovery receiver must not fail Gateway startup merely because the same
PAPER account holds EUR/USD. Expired leases remain recovery-only on disk and in
the host; an explicit authenticated `paper-resume` is still required.

## Exact scope binding

Optional execution profile keys `HEPTA_IB_PAPER_CRYPTO_AGENT_ID` and
`HEPTA_IB_PAPER_CRYPTO_SESSION_ID` name one exact crypto owner. They are valid
only in the existing single-operator mixed BTC/PAXOS/USD + EUR/IDEALPRO/USD
PAPER profile and are included in its authorization digest. Match them to
Gateway's reviewed agent and `HEPTA_TOOL_CRYPTO_SESSION_ID`. An absent binding
preserves the previous strict account-wide policy. Agent requests cannot
supply an instrument exemption or select another account.

For that owner only, session audit/resume can return the distinct
`SESSION_CRYPTO_OWNER_ZERO_CONFIRMED` result. The coherent authoritative BTC
position must be exactly zero. BTC and FX position epochs and generations must
match the account snapshot; the full account gross must equal those two exact
positions and the retained FX quantity must stay within its existing envelope.
Active orders, uncertain commands, incomplete history/risk, post-fill refresh,
terminal fences, and any scope mismatch still block resumption. The release
operation independently repeats this audit rather than trusting an earlier
caller-side check. Reported broker gross and net quantities remain real whole-
account values; they are never replaced by zero to imitate finalization.

## Distinct lifecycle operations

Gateway startup retains an already fenced, non-finalized user recovery receiver
when the global audit reports a non-flat position. Maintenance does not
repeatedly rewrite an unexpired user recovery lease or attempt global cleanup.
Explicit `paper-resume` verifies the same token, generation and durable
account/domain owner, audits the server-owned scope, releases only that owner's
fence, then commits the next lease generation. Another same-owner token or any
pending/finalized transaction is not resumable. FX remains independently fenced.

`RecoveryAuditOwner`, `FinalizeRecoveryOnlyOwner`, PAPER finalization/ACK and
terminalization retain their account-wide zero-position rules. Scoped resume
and active-lease continuation results cannot be used as global zero proofs.
Ordinary renewal does not promote a recovery-only lease. Failed activation
uses the existing write-ahead recovery path; there is no token replacement,
manual lease JSON edit, false flat flag or Broker-order resend.

## Validation

Regression coverage includes held -25,000 EUR with zero BTC, nonzero BTC,
unknown residual exposure, incomplete/mismatched position generations,
wrong agent/session/account, profile binding hashes, terminal/finalization
separation, startup restoration of expired leases, explicit same-token resume,
stale-generation refusal and restart persistence. The complete native suite
passed 49/49 for this change. Live PAPER canary evidence is a separate operator
record and is not implied by these offline tests.
