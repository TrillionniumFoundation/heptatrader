# Incident response

Status: CURRENT  
Applies to: canonical runtime

## First actions

For any possible order, position, identity, journal, credential, or broker-state ambiguity:

1. engage the operator kill switch for IB PAPER;
2. stop new session provisioning and fence active owners;
3. preserve journals, service logs, runtime configuration digests, exact binaries, and host/network state;
4. use read-only status and authoritative broker queries;
5. cancel/reduce/flatten only through guarded Execution paths;
6. do not delete state or retry uncertain mutations with new command IDs.

## Priority conditions

- **SEV-1:** unknown/duplicate order, position mismatch, possible unauthorized broker access, journal durability failure after possible send, kill-switch uncertainty, credential exposure, or inability to reduce risk.
- **SEV-2:** degraded quote/account/order refresh, repeated reconnect, high callback lag, excessive rejects, or session recovery-only/fence anomalies.
- **SEV-3:** isolated recoverable read failure or non-authoritative observability loss.

## Required evidence

Capture exact commit/artifact digest, process identities, unit versions, configuration/profile digests, kill-switch and egress-policy state, execution epoch/generation, session owner/generation, command IDs, journal sequence range, venue order/execution IDs, authoritative positions/orders, and a UTC timeline.

## Recovery rule

The system returns to risk-increasing operation only after journal replay, fresh venue barriers, owner and command reconciliation, root-cause remediation, regression/fault tests, independent review, and the applicable external qualification. A clean process restart alone is not recovery evidence.
