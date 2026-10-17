# Pocket4 original IB orderStatus permId interlock

2026-10-17, P0 #331. The original account-wide active/protective candidate
census now has a positive Host-wide permId identity from openOrder.
However its subsequent native orderStatus stream was still keyed only by
the CLIENT-specific numeric orderId. For manual TWS orders every unbound
order can have (clientId,orderId)=(0,0). A later unknown manual order status
could leave the earlier COMPLETE global active snapshot incorrectly
unchanged. Likewise a status with a known permId but conflicting
(clientId,orderId) could traverse the original lifecycle or terminal logic,
or make a former visible protective STP/OCA appear current despite pending
cancellation.

This source change adds one original-adapter-lock check at the beginning
of TryDequeueEvent, BEFORE original lifecycle and downstream runtime:
- If a positive known permId disagrees with its known client/order id
  identity, or a known bound client/orderId identifies a different positive
  permId, quarantine the status, invalidate account-global order and
  recovery-audit coherence, and poison risk on positive economic evidence.
- If a positive unseen permId is reported with an ACTIVE order status
  (Submitted/PreSubmitted/PendingSubmit/ApiPending/PendingCancel), invalidate
  the completed all-client snapshot. This includes a second unbound TWS
  order reusing API numeric ID 0. Original status consumers may still
  observe otherwise safe non-conflicting callbacks for bookkeeping.
- If a previously visible protective permId reports PendingCancel,
  invalidate the old protective candidate census instead of letting
  a PendingCancel STP/OCA continue to look like a current complete
  protective inventory.
- An explicitly different IB account does not contaminate the configured
  account. Absent account is not proof of different-account scope.
- Unknown/zero permId does NOT become positive identity or protection.
  An active client0/order0 status without positive permId invalidates the
  global census because every manual TWS order can share that numeric 0.
  A PendingCancel status missing permId also invalidates a visible
  protective candidate matching the original client/order ID. Those
  status callbacks are not treated as new positive terminal authority;
  only a positive contradictory Host identity is quarantined.

This is the existing Broker adapter and order lifecycle, not a new owner
or permission gate. We DO NOT bind TWS orders with clientId=0, create an
order, cancel/modify any order, or infer Broker-independent protection.
After any invalidation a fresh original reqAllOpenOrders cohort must
complete again before native Broker-global zero evidence can be considered.
Visible STP/OCA/parent/transmit labels remain candidates only; in PAPER,
IB can simulate stop behavior and no candidate can prove protection
surviving a Driver or Gateway outage. maintenance_stop_permitted and
account_broker_independent_stop_survival_proven stay hard FALSE.

Native offline callback regressions use actual original adapter:
unbound ID-0 manual with a new positive permId, matching prior manual
status, foreign positive permId vs client/order numeric conflict,
pending-cancel protective candidate, and known OTHER-account event
isolation. No resident broker socket or trading service is touched.
Reference: https://www.interactivebrokers.com/docs/tws-api/ref/order
