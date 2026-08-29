# Broker network isolation

Status: current for the documented target-host policy; host enforcement is environment-specific
Applies to: `scripts/hepta_broker_egress_policy.py`, broker-owning systemd units and policy examples
Verification: same-revision CI for syntax/contracts; target-host validation required

An Agent can generate and execute code, so withholding broker credentials is insufficient. Agent and Tool Gateway identities must also lack direct reachability to local broker API ports. Broker network egress belongs only to the fixed broker-owning Execution UID.

## Current policy surface

- `scripts/hepta_broker_egress_policy.py`;
- `systemd/hepta-broker-egress-policy.service`;
- `systemd/hepta-broker-network-policy-v1.json`;
- reviewed broker-owning Execution identity.

Common local IB API ports are protected; policy loading failure tightens to deny-all and fails. The rule complements, never replaces, session capability, deterministic risk, journal, fencing, kill switch or reconciliation.

## Invariants

1. Agent, MCP adapter and Tool Gateway cannot reach protected broker ports.
2. Simulator identity receives no broker network privilege.
3. Only the broker-owning Execution service may establish the venue session.
4. All Agent mutations enter through authenticated typed local IPC.
5. Missing/invalid policy or nftables failure prevents PAPER activation.
6. Dynamic campaign/receipt/attestation machinery is not required.

Rootful nftables validation is a deployment/optional CI lane, not part of every source edit.
