# Rollback, backup, and restore

Status: CURRENT  
Applies to: persistent runtime state and immutable deployment artifacts

## Back up

Retain, with restricted access and integrity digests:

- exact source and binary artifact identity;
- OMS journal and durable command/fence state;
- session lease store and schema version;
- terminal recovery witnesses;
- non-secret profile and policy files;
- configuration and installed-file manifests;
- qualification receipts and broker evidence where applicable.

Do not back up live token or broker credentials into ordinary artifact bundles. Use the deployment secret-management system.

## Rollback preconditions

A binary rollback is allowed only when the older binary can read the current journal, lease, protocol, and configuration schemas. If compatibility is not explicitly tested, keep mutation closed and perform a reviewed migration or forward fix.

## Rollback procedure

1. engage the kill switch and stop new risk;
2. fence/revoke sessions and reconcile unresolved commands;
3. stop Gateway then Execution;
4. snapshot persistent state without modifying it;
5. install the prior immutable artifact and matching non-secret configuration;
6. run schema and ownership validation;
7. replay and reconcile under recovery-only mode;
8. run core and deployment smoke tests;
9. restore bounded authority only after review/qualification.

## Restore failure

A missing, corrupt, incompatible, or partially restored journal/lease store is a fail-closed condition. Never synthesize an empty state to make startup pass when a broker account may still contain positions or orders.
