# Startup and shutdown

Status: CURRENT  
Applies to: canonical simulator and IB PAPER candidate

## Simulator startup

1. Validate configuration, state directory, and socket ownership.
2. Start simulator Execution and wait for readiness.
3. Start the session-supervisor socket associated with the Tool Gateway (no separate supervisor daemon).
4. Start the Tool Gateway and verify Execution/event connectivity.
5. Provision a bounded Agent session.
6. Discover tools and verify catalog/schema hashes.
7. Run read-only health, account, position, order, and quote checks before mutation tests.

## IB PAPER startup

1. Keep the kill switch engaged.
2. Validate dedicated OS identities and filesystem boundaries.
3. Apply broker egress policy and prove Agent/Gateway denial.
4. Validate the fixed PAPER profile and authorization credential.
5. Confirm TWS/IB Gateway is a PAPER session on the configured loopback port.
6. Start IB Execution and wait for connection epoch, next-valid-ID, quote, account, position, active-order, terminal-order, and execution barriers required by the profile.
7. Replay the journal and reconcile unresolved commands/owners.
8. Start Gateway/supervisor and provision only the reviewed bounded session.
9. Complete external qualification/approval. Do not disarm the kill switch from Agent code.

## Graceful shutdown

1. Stop admitting new risk.
2. Fence or move sessions to recovery-only.
3. Query unresolved command status.
4. Cancel or authoritatively flatten only under the explicit exit policy.
5. Reconcile broker state.
6. Revoke sessions and durably complete terminal cleanup.
7. Flush journals and stop Gateway before Execution.
8. Keep broker egress deny-all and kill switch engaged after IB shutdown.

## Crash restart

Do not assume the prior process stopped before venue send. Replay command/send-attempt state, create a new connection epoch, obtain fresh authoritative barriers, and reconcile every unresolved mutation before reopening risk.

The [simulator operator walkthrough](../technical/simulator-operator-walkthrough.md) gives installed paths, effective defaults, concrete commands and failure-to-action mappings. The [real systemd acceptance](../technical/systemd-simulator-acceptance.md) exercises those installed assets on a disposable VM.
