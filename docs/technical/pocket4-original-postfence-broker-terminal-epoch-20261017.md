# Pocket4 original-owner post-fence terminal new-epoch observation

2026-10-17; P0 #331. This is a source-only augmentation of original native
account no-new-entry fence and post-fence Broker active-then-risk read (PR
#398). No new IB connection owner, IPC operation, Broker order/cancel,
periodic job, alternate terminal downloader or automatic risk release.

## Proven native sequence

1. Only the original configured PAPER Crypto custodian can install the
   durable no-new-entry account fence in the original coordinator OMS
   journal. The original Broker adapter's one-shot post-fence
   reqAllOpenOrders -> OpenOrderEnd -> coherent account+positions refresh
   must have completed with exactly flat, zero active Broker evidence.
2. A separately authorized existing owner recovery-audit operation (not
   ordinary system.get_health) calls the adapter's existing
   BeginRecoveryAuditBarrier, which refuses to reuse the old connection
   and requires the exact NEXT Broker epoch. Existing runtime economic
   preflight independently requires zero exposure before scheduling its
   already-reviewed, owner-controlled reconnect state machine.
3. At that point only, the original adapter may bind the earlier
   post-fence active+risk generation to the REQUIRED next connection
   epoch. This optional bind does not call Connect or Disconnect itself,
   schedule a reconnect, order or cancel a venue request, or change risk.
4. The existing reconnect owner closes lifecycle ingress, reconciles
   original OMS unknowns, drains pending callbacks, establishes the new
   IB connection, requests fresh reqAllOpenOrders and the one allowed
   reqCompletedOrders+reqExecutions terminal download on that new
   connection, and finishes a new coherent account+position generation.
5. A subsequent read-only health call can observe a positive
   new-terminal-epoch diagnostic ONLY if the exactly required connection
   (not a skipped/later epoch) has complete authoritative active,
   terminal and risk evidence, a complete existing recovery barrier,
   consistent economic exposure and separately exported exact active/terminal/risk request generations in that NEW epoch and no intervening
   Broker local send mutation or stream loss. A completed risk view
   with foreign active orders or nonzero physical positions remains
   nonzero, never zero-by-rounding.

## Non-authorities

This diagnostic is scoped to the original service/IB API-client boundary.
The integer Broker mutation counter is local to the original adapter;
an independent client or manual TWS order can still race Broker downloads.
IB callback OpenOrderEnd and CompletedOrdersEnd do not share a transactional
global account request identifier. An unbound manual TWS order may lack an
API order ID and requires additional global permanent-order ID accounting.
Therefore, even after a fresh post-fence terminal epoch, this proof is NOT
an account-global STOP permit, protection guaranteed after Driver death,
nor an authorization to modify persistent native kill-switch or
RefuseManualStop. User health always preserves
maintenance_stop_permitted=false and account_post_fence_stop_permitted=false.

## Validation and failure handling

The source fixture uses original adapter callbacks, no live Broker socket:
- cannot bind the required terminal epoch before the existing recovery
  control's BeginRecoveryAuditBarrier demands it,
- binding and health reads never call Connect or dispatch Broker mutations,
- transport reconnect must produce the exact next epoch with complete
  active/terminal/risk/download barrier before qualifying,
- new epoch terminal completion cannot be inferred from a prior cohort,
- skipped epoch, disconnect, new Broker order/send mutation, foreign
  active order, absent generation or stale economic evidence are not
  accepted as flat protected STOP, and
- any failed proof remains fail-closed; no retry/resend or hidden cutover.

Only Pocket4 stores the isolated worktree, SDK-enabled build, test logs and
receipts. A production switch is not performed by this source-only fix.
