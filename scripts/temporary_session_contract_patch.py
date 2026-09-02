#!/usr/bin/env python3
"""One-shot exact patch for the registered Session supervisor contract."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ID = "hepta.session-supervisor.v1"


def load(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write(path: str, value) -> None:
    (ROOT / path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def update_contract_registry() -> None:
    path = "docs/contracts/contract-registry-v2.json"
    registry = load(path)
    contracts = registry["contracts"]
    if any(item.get("id") == CONTRACT_ID for item in contracts):
        raise SystemExit(f"{CONTRACT_ID} already exists")
    contracts.append(
        {
            "id": CONTRACT_ID,
            "version": "1",
            "stability": "current-core",
            "document": "contracts/IDENTITY-CAPABILITY-CONTRACT.md",
            "schema_path": None,
            "providers": ["hepta.session.runtime"],
            "consumers": ["hepta.client.runtime", "hepta.gateway.runtime"],
        }
    )
    contracts.sort(key=lambda item: item["id"])
    write(path, registry)


def update_manifests() -> None:
    edits = {
        "docs/modules/manifests/hepta-session-runtime.json": ("provides", True),
        "docs/modules/manifests/hepta-client-runtime.json": ("consumes", False),
        "docs/modules/manifests/hepta-gateway-runtime.json": ("consumes", False),
    }
    for path, (field, bump_minor) in edits.items():
        manifest = load(path)
        values = manifest[field]
        if CONTRACT_ID in values:
            raise SystemExit(f"{path}: duplicate {CONTRACT_ID}")
        values.append(CONTRACT_ID)
        values.sort()
        if bump_minor:
            if manifest.get("version") != "1.0.0":
                raise SystemExit(
                    f"{path}: unexpected version {manifest.get('version')}"
                )
            manifest["version"] = "1.1.0"
            verification = manifest["verification"]
            if "protocol-contracts" not in verification:
                verification.append("protocol-contracts")
                verification.sort()
        write(path, manifest)


def update_normative_contract() -> None:
    path = ROOT / "docs/contracts/IDENTITY-CAPABILITY-CONTRACT.md"
    text = path.read_text(encoding="utf-8")
    marker = "## Session supervisor contract (`hepta.session-supervisor.v1`)"
    if marker in text:
        raise SystemExit("session supervisor contract section already exists")
    section = r"""

## Session supervisor contract (`hepta.session-supervisor.v1`)

`hepta.session.runtime` is the sole durable authority for supervisor lease records, lease generations, predecessor fencing, recovery-only state and PAPER finalization tombstones. `hepta.client.runtime` encodes bounded supervisor requests; `hepta.gateway.runtime` validates peer identity and invokes the supervisor boundary. Neither consumer may manufacture an accepted lease or advance a durable generation locally.

### Request identity and operations

Every request is one of `Provision`, `Revoke`, `Renew`, `Rotate`, `RecoveryQuery`, `PaperFinalize`, `PaperFinalizeAck`, `PaperTerminalizeAck`, `PaperTerminalWitnessPrepare` or `PaperTerminalWitnessAck`. The operation is bound, as applicable, to template ID, current/replacement token, agent ID, session ID, peer UID, TTL, expected lease generation, target command ID, recovery/finalization IDs and content digests. Unknown operations, malformed canonical fields, zero/overflow TTL, stale expected generation, unsafe token replacement and incomplete PAPER evidence fail closed.

### Durable state and fencing

A lease record binds issuer, token, agent/session identity, peer UID, exact Execution account/domain owner scope, expiry, lease generation, predecessor token/generation and fence state. Mutating operations serialize through the single owner and commit the encrypted lease store atomically before success is returned. Rotation never makes both predecessor and replacement authoritative: the predecessor becomes fenced and the replacement is accepted only at the next durable generation. Restart must reload and authenticate the store before serving requests; metadata, key, decrypt, parse or persistence uncertainty closes admission.

Expiry is evaluated against the authority-owned clock. A consumer-provided timestamp never extends a lease. Heartbeat or network liveness is diagnostic unless it completes a valid `Renew` transition against the current token and generation. A stale token, stale owner, expired record, changed account/domain scope or generation mismatch cannot be repaired by retrying with a new identity.

### Recovery and PAPER finalization

Recovery state is non-authorizing except for the explicitly registered recovery operation. PAPER finalization is a one-way state machine: `None -> FencePending -> FenceComplete -> AuditSealed -> purged acknowledgement`. Finalized records are non-authorizing tombstones and cannot be provisioned, renewed or rotated back into a Tool session. Group sealing and purge require exact recovery/finalization IDs, owner-set digest/count, acknowledging owner identity/generation and terminal receipt digests. Missing or conflicting broker/owner evidence keeps the mutation gate closed.

### Result semantics

`accepted=true` means the requested transition was durably admitted under the returned `leaseGeneration`; it is not Broker truth and grants no Execution authority beyond the separately validated capability. Results carry a stable reason code and, for recovery/finalization, explicit owner fencing, Execution fencing generation, authoritative command status, broker generation/barrier fields and terminal evidence digests. Clients must treat transport failure after submission as uncertain and query authoritative supervisor state rather than replaying a mutation blindly.

### Compatibility and verification

The C++ wire DTO in `HeptaTrade/tool_host/session_supervisor_protocol.h`, the durable record and transition API in `session_supervisor_lease_store.h`, the contract registry, ModuleManifests and generated guides must change atomically. Removing or reinterpreting an operation or field is a major contract change. Additive fields require closed-world decoder tests, old/new golden vectors, malformed-input negatives, crash-before/after-persist tests, stale-generation/fencing tests and exact replay evidence. The current verification authority is `session-boundary` plus `protocol-contracts`; distributed consensus is not claimed by this same-host contract.
"""
    path.write_text(text.rstrip() + section + "\n", encoding="utf-8")


def update_gap_and_test_registries() -> None:
    gaps_path = "docs/program/gap-registry-v2.json"
    gaps_doc = load(gaps_path)
    gap_id = "G-SES-001"
    if any(item.get("id") == gap_id for item in gaps_doc["gaps"]):
        raise SystemExit(f"{gap_id} already exists")
    gaps_doc["gaps"].append(
        {
            "id": gap_id,
            "priority": "P0",
            "title": (
                "Durable Session authority lacks a registered inter-module "
                "supervisor contract"
            ),
            "workstream": "WS-MOD",
            "milestone": "M2",
            "state": "closed",
            "evidence": [
                "docs-control",
                "module-registry",
                "protocol-contracts",
                "session-boundary",
            ],
        }
    )
    write(gaps_path, gaps_doc)

    matrix_path = "docs/verification/test-matrix-v2.json"
    matrix = load(matrix_path)
    session_check = next(
        item for item in matrix["checks"] if item["id"] == "session-boundary"
    )
    session_check["evidence"] = (
        "session supervisor protocol/lease-store contract, peer boundary, "
        "generation fencing, migration and hostile-negative tests"
    )
    write(matrix_path, matrix)


def main() -> int:
    update_contract_registry()
    update_manifests()
    update_normative_contract()
    update_gap_and_test_registries()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
