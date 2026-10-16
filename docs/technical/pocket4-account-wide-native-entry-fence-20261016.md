# Pocket4 original-owner account-wide native new-entry fence

The native Execution Coordinator now implements a sticky, journal-durable
account-wide NO-NEW-ENTRY admission fence on its original coordinator mutex.
This is a new scoped control operation through the existing Execution Unix
protocol (FenceAccountNewEntries=17), not a parallel owner, kill-switch service,
broker adapter or separate trading system.

Only the existing configured user PAPER Crypto custodian is authorized:
userScope=true, matching Crypto agent/session, exact Broker account,
venue=IB, executionDomain=PAPER and recovery ingress fence=0.
Other FX/crypto sessions, alternate account, untrusted simulator contexts
and unconfigured service authorities reject before writing any OMS event.

The Coordinator installs the local account admission map under the
same mutex as PlaceOrderLocked BEFORE fsyncing account_new_entry_fenced.
It acknowledges success only after that durable append. An append failure
retains the local stop and triggers the original journal-poison mutation block. All fresh
Place commands for that account are rejected with ACCOUNT_NEW_ENTRY_FENCED,
regardless of agent/session or FX/BTC instrument. Same-command idempotent
reads remain possible. Cancel and reduce-only Flatten paths are unchanged.

If a Broker Place/Flatten callback is already in progress when the fence
is committed, it may still complete. The control returns
ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_RISK_OUTSTANDING and conservative
local pending count. When none is visible, it still returns
ACCOUNT_NEW_ENTRY_FENCE_INSTALLED_NOT_STOP_PERMIT. An empty local owner
map never means the global Broker account has no other active order.

ApplyRecoveredOwnershipEventLocked validates and replays the original
account hold. The canonical OMS checkpoint always preserves the
original account_new_entry_fenced record per account in hot replay,
including after subsequent generation compactions. Invalid recovery
records fail closed. This v1 is deliberately non-releasable: no
unverified paper-on or session-owner release may undo it.

This is ONLY an original-coordinator account admission fence. It is not
an account-wide BROKER stop permit, a globally coherent cross-client active
and unknown order ledger, or an independent Broker-native stop/OCA that
survives Driver death. P0 #331 remains OPEN. Never remove RefuseManualStop
or restart resident trading services on strength of this feature alone.

All tests and artifacts remain on Pocket4, using isolated journals,
different agent sessions, deliberately blocked Broker callback, native
Unix IPC, restart recovery and two generation compactions. No resident
production account hold is installed during source qualification.
