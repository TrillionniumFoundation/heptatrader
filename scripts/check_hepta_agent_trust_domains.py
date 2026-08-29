#!/usr/bin/env python3
"""Validate and optionally stage Agent trust-domain provisioning contracts.

The default operation is read-only.  The explicit staging operation writes
declarative files only below an operator-selected empty directory.  It never
creates users, sockets, credentials, sessions, units, or trading authority.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "systemd/hepta-agent-trust-domain-policy-v1.json"
DEFAULT_FIXTURE = ROOT / "tests/fixtures/hepta-agent-trust-domains-v1.json"
IDENTITIES = ROOT / "systemd/hepta-service-identities-v1.json"
DOMAIN_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
POLICY_FIELDS = {
    "schema", "version", "trust_boundary", "isolation_requirement",
    "unique_fields", "multi_domain_templates", "single_domain_compatibility",
    "shared_connect_group", "one_execution_authority_per_domain",
    "paper_authorized", "live_authorized",
}
DOMAIN_FIELDS = {
    "domain_id",
    "gateway_name", "gateway_uid", "gateway_group", "gateway_gid",
    "agent_name", "agent_uid", "agent_group", "agent_gid",
    "execution_name", "execution_uid", "execution_group", "execution_gid",
    "connect_group", "connect_group_gid",
    "socket_path", "token_directory", "supervisor_socket",
    "lease_credential_path", "gateway_state_directory",
    "execution_socket", "execution_event_socket",
    "execution_fence_credential_path", "execution_state_directory",
    "execution_gateway_uid", "execution_gateway_agent_id",
}
COMPATIBILITY_FIELDS = {
    "domain_id", "agent_uid", "agent_gid", "socket_path", "token_directory",
}
TEMPLATE_FIELDS = {
    "gateway_name", "gateway_group", "agent_name", "agent_group",
    "execution_name", "execution_group", "socket_path", "token_directory",
    "supervisor_socket", "lease_credential_path", "gateway_state_directory",
    "execution_socket", "execution_event_socket",
    "execution_fence_credential_path", "execution_state_directory",
}
SHARED_CONNECT_GROUP_FIELDS = {"name", "gid", "roles"}
SHARED_CONNECT_GROUP_ROLES = [
    "explicit-single-domain-compatibility",
]
STAGING_MANIFEST = "usr/share/heptatrader/trust-domain-provisioning-v1.json"
STAGING_SYSUSERS = (
    "usr/lib/sysusers.d/heptatrader-agent-trust-domains.conf")


class TrustDomainError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TrustDomainError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> dict[str, Any]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise TrustDomainError(f"{label} must be a regular non-symlink file")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustDomainError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise TrustDomainError(f"{label} root must be an object")
    return document


def _absolute_path(
        value: Any, label: str, prefixes: tuple[str, ...],
        socket: bool = False) -> str:
    if not isinstance(value, str) or "\\" in value or "\0" in value:
        raise TrustDomainError(f"{label} is invalid")
    path = PurePosixPath(value)
    if (not path.is_absolute() or path.as_posix() != value or
            any(part in {"", ".", ".."} for part in path.parts) or
            not any(value.startswith(prefix) for prefix in prefixes)):
        raise TrustDomainError(f"{label} is outside its canonical path scope")
    if socket and len(value.encode("utf-8")) > 107:
        raise TrustDomainError(f"{label} exceeds the AF_UNIX path limit")
    return value


def _positive_uid(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrustDomainError(f"{label} must be an integer")
    if value < 1 or value > 4_294_967_295:
        raise TrustDomainError(f"{label} is outside the UID/GID range")
    return value


def load_policy(path: Path) -> dict[str, Any]:
    policy = _read_json(path, "trust-domain policy")
    if set(policy) != POLICY_FIELDS:
        raise TrustDomainError("trust-domain policy fields do not match schema")
    if (policy["schema"] != "hepta.agent-trust-domain-policy.v1" or
            policy["version"] != 1):
        raise TrustDomainError("unsupported trust-domain policy")
    if policy["trust_boundary"] != "one-agent-or-mutually-trusting-process-group":
        raise TrustDomainError("trust-domain boundary is not fail-closed")
    if (policy["isolation_requirement"] !=
            "untrusted-agents-must-use-distinct-trust-domains"):
        raise TrustDomainError("untrusted Agent isolation is not mandatory")
    if policy["paper_authorized"] is not False or policy["live_authorized"] is not False:
        raise TrustDomainError("trust-domain policy cannot authorize PAPER or LIVE")
    if policy["unique_fields"] != [
            "gateway_name", "gateway_uid", "gateway_group", "gateway_gid",
            "connect_group", "connect_group_gid",
            "agent_name", "agent_uid", "agent_group", "agent_gid",
            "execution_name", "execution_uid", "execution_group",
            "execution_gid", "socket_path", "token_directory",
            "supervisor_socket", "lease_credential_path",
            "gateway_state_directory", "execution_socket",
            "execution_event_socket", "execution_fence_credential_path",
            "execution_state_directory"]:
        raise TrustDomainError("trust-domain unique fields drifted")
    templates = policy["multi_domain_templates"]
    if not isinstance(templates, dict) or set(templates) != TEMPLATE_FIELDS:
        raise TrustDomainError("multi-domain templates are invalid")
    expected_templates = {
        "gateway_name": "hepta-gw-{domain_id}",
        "gateway_group": "hepta-gw-{domain_id}",
        "agent_name": "hepta-agent-{domain_id}",
        "agent_group": "hepta-agent-{domain_id}",
        "execution_name": "hepta-exec-{domain_id}",
        "execution_group": "hepta-exec-{domain_id}",
        "socket_path": "/run/hepta-agent-{domain_id}/tools.sock",
        "token_directory": "/run/hepta-agent-{domain_id}/sessions",
        "supervisor_socket":
            "/run/hepta-tool-gateway-{domain_id}/session-supervisor.sock",
        "lease_credential_path":
            "/etc/heptatrader/credentials/trust-domains/{domain_id}/"
            "hepta-supervisor-lease.key",
        "gateway_state_directory":
            "/var/lib/hepta-tool-gateway-{domain_id}",
        "execution_socket":
            "/run/hepta-execution-{domain_id}/execution.sock",
        "execution_event_socket":
            "/run/hepta-execution-{domain_id}/events.sock",
        "execution_fence_credential_path":
            "/etc/heptatrader/credentials/trust-domains/{domain_id}/"
            "hepta-execution-simulator-fence",
        "execution_state_directory":
            "/var/lib/hepta-execution-{domain_id}",
    }
    if templates != expected_templates:
        raise TrustDomainError("multi-domain template contract drifted")
    shared = policy["shared_connect_group"]
    if (
            not isinstance(shared, dict) or
            set(shared) != SHARED_CONNECT_GROUP_FIELDS or
            shared.get("name") != "hepta-gateway" or
            shared.get("gid") != 2001 or
            shared.get("roles") != SHARED_CONNECT_GROUP_ROLES):
        raise TrustDomainError("shared connect group contract drifted")
    if policy["one_execution_authority_per_domain"] is not True:
        raise TrustDomainError(
            "one Execution authority per trust-domain is mandatory")
    compatibility = policy["single_domain_compatibility"]
    if (not isinstance(compatibility, dict) or
            set(compatibility) != COMPATIBILITY_FIELDS):
        raise TrustDomainError("single-domain compatibility record is invalid")
    _validate_compatibility(compatibility)
    if (compatibility["domain_id"] != "default" or
            compatibility["socket_path"] != "/run/hepta-agent/tools.sock" or
            compatibility["token_directory"] != "/run/hepta-agent"):
        raise TrustDomainError("single-domain compatibility path drifted")
    return policy


def _validate_compatibility(compatibility: dict[str, Any]) -> None:
    domain_id = compatibility["domain_id"]
    if not isinstance(domain_id, str) or DOMAIN_ID.fullmatch(domain_id) is None:
        raise TrustDomainError("single-domain compatibility domain_id is invalid")
    _positive_uid(
        compatibility["agent_uid"], "single-domain compatibility agent_uid")
    _positive_uid(
        compatibility["agent_gid"], "single-domain compatibility agent_gid")
    socket_path = _absolute_path(
        compatibility["socket_path"], "single-domain compatibility socket_path",
        ("/run/hepta-agent/",), socket=True)
    token_directory = _absolute_path(
        compatibility["token_directory"],
        "single-domain compatibility token_directory",
        ("/run/hepta-agent",))
    if (
            domain_id != "default" or
            socket_path != "/run/hepta-agent/tools.sock" or
            token_directory != "/run/hepta-agent"):
        raise TrustDomainError("single-domain compatibility path drifted")


def _validate_domain(
        domain: Any, templates: dict[str, str] | None, label: str) -> dict[str, Any]:
    if not isinstance(domain, dict) or set(domain) != DOMAIN_FIELDS:
        raise TrustDomainError(f"{label} fields do not match schema")
    domain_id = domain["domain_id"]
    if not isinstance(domain_id, str) or DOMAIN_ID.fullmatch(domain_id) is None:
        raise TrustDomainError(f"{label} domain_id is invalid")
    for field in (
            "gateway_uid", "gateway_gid", "agent_uid", "agent_gid",
            "execution_uid", "execution_gid", "connect_group_gid",
            "execution_gateway_uid"):
        _positive_uid(domain[field], f"{label} {field}")
    for field in (
            "gateway_name", "gateway_group", "agent_name", "agent_group",
            "execution_name", "execution_group", "connect_group"):
        value = domain[field]
        if (
                not isinstance(value, str) or
                re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", value) is None):
            raise TrustDomainError(f"{label} {field} is invalid")
    socket_path = _absolute_path(
        domain["socket_path"], f"{label} socket_path",
        ("/run/hepta-agent-",), socket=True)
    token_directory = _absolute_path(
        domain["token_directory"], f"{label} token_directory",
        ("/run/hepta-agent-",))
    supervisor_socket = _absolute_path(
        domain["supervisor_socket"], f"{label} supervisor_socket",
        ("/run/hepta-tool-gateway-",), socket=True)
    lease_credential_path = _absolute_path(
        domain["lease_credential_path"], f"{label} lease_credential_path",
        ("/etc/heptatrader/credentials/trust-domains/",))
    gateway_state_directory = _absolute_path(
        domain["gateway_state_directory"], f"{label} gateway_state_directory",
        ("/var/lib/hepta-tool-gateway-",))
    execution_socket = _absolute_path(
        domain["execution_socket"], f"{label} execution_socket",
        ("/run/hepta-execution-",), socket=True)
    execution_event_socket = _absolute_path(
        domain["execution_event_socket"], f"{label} execution_event_socket",
        ("/run/hepta-execution-",), socket=True)
    execution_fence_credential_path = _absolute_path(
        domain["execution_fence_credential_path"],
        f"{label} execution_fence_credential_path",
        ("/etc/heptatrader/credentials/trust-domains/",))
    execution_state_directory = _absolute_path(
        domain["execution_state_directory"],
        f"{label} execution_state_directory",
        ("/var/lib/hepta-execution-",))
    if (
            not isinstance(domain["execution_gateway_agent_id"], str) or
            DOMAIN_ID.fullmatch(domain["execution_gateway_agent_id"]) is None):
        raise TrustDomainError(
            f"{label} execution_gateway_agent_id is invalid")
    if (
            len({
                domain["gateway_uid"], domain["agent_uid"],
                domain["execution_uid"],
            }) != 3 or
            len({
                domain["gateway_gid"], domain["agent_gid"],
                domain["execution_gid"],
            }) != 3):
        raise TrustDomainError(f"{label} reuses a UID/GID trust boundary")
    if (
            domain["connect_group"] != domain["gateway_group"] or
            domain["connect_group_gid"] != domain["gateway_gid"]):
        raise TrustDomainError(
            f"{label} connect group is not domain-private")
    if (
            domain["execution_gateway_uid"] != domain["gateway_uid"] or
            domain["execution_gateway_agent_id"] != domain_id):
        raise TrustDomainError(
            f"{label} Execution Gateway UID/Agent ID binding drifted")
    if (
            socket_path == token_directory or
            socket_path.startswith(token_directory + "/")):
        raise TrustDomainError(f"{label} socket and token scopes overlap")
    if templates is not None:
        observed = {
            "gateway_name": domain["gateway_name"],
            "gateway_group": domain["gateway_group"],
            "agent_name": domain["agent_name"],
            "agent_group": domain["agent_group"],
            "execution_name": domain["execution_name"],
            "execution_group": domain["execution_group"],
            "socket_path": socket_path,
            "token_directory": token_directory,
            "supervisor_socket": supervisor_socket,
            "lease_credential_path": lease_credential_path,
            "gateway_state_directory": gateway_state_directory,
            "execution_socket": execution_socket,
            "execution_event_socket": execution_event_socket,
            "execution_fence_credential_path":
                execution_fence_credential_path,
            "execution_state_directory": execution_state_directory,
        }
        expected = {
            field: template.format(domain_id=domain_id)
            for field, template in templates.items()
        }
        if observed != expected:
            raise TrustDomainError(
                f"{label} identity/path does not match its domain")
    return domain


def validate(policy_path: Path, fixture_path: Path, identities_path: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    identities = _read_json(identities_path, "service identity manifest")
    try:
        agent_identity = identities["identities"]["hepta-agent"]
        gateway_identity = identities["identities"]["hepta-gateway"]
    except (KeyError, TypeError) as error:
        raise TrustDomainError(
            "compatibility Agent/Gateway identity is missing") from error
    compatibility = policy["single_domain_compatibility"]
    if (compatibility["agent_uid"] != agent_identity.get("uid") or
            compatibility["agent_gid"] != agent_identity.get("gid")):
        raise TrustDomainError("single-domain compatibility identity drifted")
    shared = policy["shared_connect_group"]
    if (
            shared["name"] != "hepta-gateway" or
            shared["gid"] != gateway_identity.get("gid")):
        raise TrustDomainError("shared connect group identity drifted")

    fixture = _read_json(fixture_path, "trust-domain fixture")
    if set(fixture) != {
            "schema", "version", "domains", "paper_authorized", "live_authorized"}:
        raise TrustDomainError("trust-domain fixture fields do not match schema")
    if (fixture["schema"] != "hepta.agent-trust-domain-fixture.v1" or
            fixture["version"] != 1 or fixture["paper_authorized"] is not False or
            fixture["live_authorized"] is not False):
        raise TrustDomainError("trust-domain fixture is unsupported or authorizing")
    domains = fixture["domains"]
    if not isinstance(domains, list) or len(domains) < 2 or len(domains) > 64:
        raise TrustDomainError("multi-domain fixture must contain 2 to 64 domains")
    validated = [
        _validate_domain(item, policy["multi_domain_templates"], f"domain[{index}]")
        for index, item in enumerate(domains)
    ]
    for field in policy["unique_fields"]:
        values = [item[field] for item in validated]
        if len(values) != len(set(values)):
            raise TrustDomainError(f"untrusted domains share {field}")
    all_uids = [
        item[field]
        for item in validated
        for field in ("gateway_uid", "agent_uid", "execution_uid")
    ]
    all_gids = [
        item[field]
        for item in validated
        for field in ("gateway_gid", "agent_gid", "execution_gid")
    ]
    all_names = [
        item[field]
        for item in validated
        for field in ("gateway_name", "agent_name", "execution_name")
    ]
    if len(all_uids) != len(set(all_uids)):
        raise TrustDomainError("trust-domain service UIDs are reused")
    if len(all_gids) != len(set(all_gids)):
        raise TrustDomainError("trust-domain service GIDs are reused")
    if len(all_names) != len(set(all_names)):
        raise TrustDomainError("trust-domain service names are reused")
    fixed_ids = {
        identity.get(key)
        for identity in identities.get("identities", {}).values()
        if isinstance(identity, dict)
        for key in ("uid", "gid")
    }
    if set(all_uids) & fixed_ids or set(all_gids) & fixed_ids:
        raise TrustDomainError(
            "trust-domain service UID/GID collides with a fixed identity")
    return {
        "passed": True,
        "domain_count": len(validated),
        "isolated_fields": list(policy["unique_fields"]),
        "domains": validated,
        "paper_authorized": False,
        "live_authorized": False,
    }


def validate_provisioned_identities(
        domains: list[dict[str, Any]], passwd_text: str,
        group_text: str) -> None:
    """Validate already-created identities without mutating the host."""
    passwd: dict[str, tuple[int, int, str, str]] = {}
    uid_owners: dict[int, list[str]] = {}
    for number, line in enumerate(passwd_text.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) != 7 or not fields[0] or fields[0] in passwd:
            raise TrustDomainError(
                f"provisioned passwd line {number} is malformed")
        try:
            uid = int(fields[2], 10)
            gid = int(fields[3], 10)
        except ValueError as error:
            raise TrustDomainError(
                f"provisioned passwd line {number} has invalid IDs") from error
        passwd[fields[0]] = (uid, gid, fields[5], fields[6])
        uid_owners.setdefault(uid, []).append(fields[0])

    groups: dict[str, tuple[int, tuple[str, ...]]] = {}
    gid_owners: dict[int, list[str]] = {}
    memberships: dict[str, set[str]] = {}
    for number, line in enumerate(group_text.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) != 4 or not fields[0] or fields[0] in groups:
            raise TrustDomainError(
                f"provisioned group line {number} is malformed")
        try:
            gid = int(fields[2], 10)
        except ValueError as error:
            raise TrustDomainError(
                f"provisioned group line {number} has invalid GID") from error
        members = tuple(fields[3].split(",")) if fields[3] else ()
        if len(members) != len(set(members)) or any(not item for item in members):
            raise TrustDomainError(
                f"provisioned group line {number} has invalid members")
        groups[fields[0]] = (gid, members)
        gid_owners.setdefault(gid, []).append(fields[0])
        for member in members:
            memberships.setdefault(member, set()).add(fields[0])

    expected: dict[str, tuple[int, int, set[str]]] = {}
    for item in domains:
        expected[item["gateway_name"]] = (
            item["gateway_uid"], item["gateway_gid"], set())
        expected[item["agent_name"]] = (
            item["agent_uid"], item["agent_gid"], set())
        expected[item["execution_name"]] = (
            item["execution_uid"], item["execution_gid"], set())
        if (
                item["connect_group"] != item["gateway_group"] or
                item["connect_group_gid"] != item["gateway_gid"]):
            raise TrustDomainError(
                "domain-private connect group binding drifted")

    for name, (uid, gid, supplementary) in expected.items():
        account = passwd.get(name)
        primary_group = groups.get(name)
        if account is None or primary_group is None:
            raise TrustDomainError(
                f"provisioned trust-domain identity is missing: {name}")
        if (
                account[0] != uid or account[1] != gid or
                primary_group[0] != gid or primary_group[1] or
                account[2] != "/nonexistent" or
                not account[3].endswith("/nologin")):
            raise TrustDomainError(
                f"provisioned trust-domain UID/GID mismatch: {name}")
        if memberships.get(name, set()) != supplementary:
            raise TrustDomainError(
                f"provisioned trust-domain supplementary groups mismatch: {name}")
        if uid_owners.get(uid) != [name]:
            raise TrustDomainError(
                f"provisioned trust-domain UID is shared: {uid}")
        if gid_owners.get(gid) != [name]:
            raise TrustDomainError(
                f"provisioned trust-domain GID is shared: {gid}")


def _canonical_json(document: Any) -> bytes:
    return (
        json.dumps(
            document, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def _runtime_document(domain: dict[str, Any]) -> dict[str, Any]:
    return {
        **domain,
        "schema": "hepta.agent-trust-domain-runtime.v1",
        "version": 1,
        "single_domain_compatibility": False,
        "paper_authorized": False,
        "live_authorized": False,
    }


def _gateway_environment(domain: dict[str, Any]) -> bytes:
    values = (
        ("HEPTA_EXECUTION_REMOTE_MODE", "SIMULATOR"),
        ("HEPTA_EXECUTION_SOCKET", domain["execution_socket"]),
        ("HEPTA_EXECUTION_EVENT_SOCKET", domain["execution_event_socket"]),
        ("HEPTA_EXECUTION_SERVICE_UID", str(domain["execution_uid"])),
        ("HEPTA_EXECUTION_IO_TIMEOUT_MS", "2500"),
        ("HEPTA_EXECUTION_MAX_RESPONSE_BYTES", "32768"),
        ("HEPTA_TOOL_ACCOUNT", "SIM"),
        ("HEPTA_EXECUTION_DOMAIN_ID", f"SIM:{domain['domain_id']}"),
        ("HEPTA_TOOL_ALLOW_TRADE", "0"),
        ("HEPTA_TOOL_SESSION_TEMPLATES", "watch"),
        ("HEPTA_TOOL_CONTRACT_BINDINGS",
         "EUR.USD|EUR|CASH|IDEALPRO|USD"),
        ("HEPTA_TOOL_AGENT_UID", str(domain["agent_uid"])),
        ("HEPTA_TOOL_SUPERVISOR_UID", "0"),
        ("HEPTA_TOOL_SUPERVISOR_MAX_TTL_SEC", "86400"),
        ("HEPTA_TOOL_SERVER_WORKERS", "4"),
        ("HEPTA_TOOL_SERVER_MAX_PENDING", "32"),
        ("HEPTA_TOOL_SERVER_MAX_CONCURRENT_PER_OWNER", "1"),
        ("HEPTA_TOOL_SERVER_MAX_PENDING_PER_OWNER", "8"),
        ("HEPTA_TOOL_SERVER_INGRESS_WORKERS", "2"),
    )
    return "".join(f"{key}={value}\n" for key, value in values).encode("utf-8")


def _execution_environment(domain: dict[str, Any]) -> bytes:
    values = (
        ("HEPTA_EXECUTION_GATEWAY_UID",
         str(domain["execution_gateway_uid"])),
        ("HEPTA_EXECUTION_GATEWAY_AGENT_ID",
         domain["execution_gateway_agent_id"]),
        ("HEPTA_EXECUTION_MAX_REQUEST_BYTES", "16384"),
        ("HEPTA_EXECUTION_IO_TIMEOUT_MS", "2500"),
    )
    return "".join(f"{key}={value}\n" for key, value in values).encode("utf-8")


def _agent_host_dropin(domain: dict[str, Any]) -> bytes:
    """Return a reviewed identity/network drop-in for one external Agent host.

    The staging command deliberately does not install this fragment below a
    systemd unit name: Codex/OpenClaw service naming remains an operator
    decision.  The manifest records the exact fragment that must be applied to
    that service.
    """
    return (
        "# Apply to exactly one reviewed Codex/OpenClaw host service.\n"
        "# This fragment grants neither broker access nor trading authority.\n"
        "[Unit]\n"
        "BindsTo=hepta-broker-egress-policy.service\n"
        "After=hepta-broker-egress-policy.service\n"
        "\n"
        "[Service]\n"
        f"User={domain['agent_name']}\n"
        f"Group={domain['agent_group']}\n"
        "SupplementaryGroups=\n"
        "UMask=0077\n"
        "NoNewPrivileges=yes\n"
        "CapabilityBoundingSet=\n"
        "AmbientCapabilities=\n"
        "RestrictNamespaces=yes\n"
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        "Environment=HEPTA_AGENT_DOMAIN_CONFIG="
        f"/etc/heptatrader/trust-domains/uid-{domain['agent_uid']}.json\n"
    ).encode("utf-8")


def _sysusers(domains: list[dict[str, Any]]) -> bytes:
    lines = [
        "# Generated declarative identities; applying this file is a separate",
        "# privileged operator action. PAPER and LIVE remain unauthorized.",
    ]
    for domain in domains:
        for prefix in ("gateway", "agent", "execution"):
            name = domain[f"{prefix}_name"]
            gid = domain[f"{prefix}_gid"]
            uid = domain[f"{prefix}_uid"]
            lines.extend((
                f"g {name} {gid}",
                f"u {name} {uid}:{gid} \"HeptaTrader {prefix} "
                f"{domain['domain_id']}\" /nonexistent /usr/sbin/nologin",
            ))
    return ("\n".join(lines) + "\n").encode("utf-8")


def expected_staging_files(
        result: dict[str, Any]) -> dict[str, tuple[bytes, int]]:
    domains = result["domains"]
    files: dict[str, tuple[bytes, int]] = {
        STAGING_SYSUSERS: (_sysusers(domains), 0o644),
    }
    manifest_domains: list[dict[str, Any]] = []
    for domain in domains:
        domain_id = domain["domain_id"]
        runtime_path = f"etc/heptatrader/trust-domains/{domain_id}.json"
        uid_runtime_path = (
            f"etc/heptatrader/trust-domains/uid-{domain['agent_uid']}.json")
        gateway_env_path = f"etc/heptatrader/trust-domains/{domain_id}.env"
        execution_env_path = (
            f"etc/heptatrader/trust-domains/{domain_id}.execution.env")
        agent_host_dropin_path = (
            f"etc/heptatrader/trust-domains/{domain_id}.agent-host.conf")
        runtime_contents = _canonical_json(_runtime_document(domain))
        # These are intentionally two separately-created regular files.  A
        # symlink would defeat the launcher's no-follow path contract and a
        # hard link would violate the single-link metadata requirement.  The
        # UID profile remains root-owned mode 0640 with only the domain's
        # private Agent group able to read it; root-owned mode 0600 would make
        # the unprivileged Agent launcher unable to consume the profile.
        files[runtime_path] = (runtime_contents, 0o600)
        files[uid_runtime_path] = (runtime_contents, 0o640)
        files[gateway_env_path] = (_gateway_environment(domain), 0o644)
        files[execution_env_path] = (_execution_environment(domain), 0o644)
        files[agent_host_dropin_path] = (_agent_host_dropin(domain), 0o644)
        manifest_domains.append({
            "domain_id": domain_id,
            "gateway_name": domain["gateway_name"],
            "gateway_uid": domain["gateway_uid"],
            "gateway_gid": domain["gateway_gid"],
            "agent_name": domain["agent_name"],
            "agent_uid": domain["agent_uid"],
            "agent_gid": domain["agent_gid"],
            "execution_name": domain["execution_name"],
            "execution_uid": domain["execution_uid"],
            "execution_gid": domain["execution_gid"],
            "connect_group": domain["connect_group"],
            "connect_group_gid": domain["connect_group_gid"],
            "runtime_config": "/" + runtime_path,
            "uid_runtime_config": "/" + uid_runtime_path,
            "agent_host_dropin": "/" + agent_host_dropin_path,
            "gateway_environment": "/" + gateway_env_path,
            "execution_environment": "/" + execution_env_path,
            "lease_credential_path": domain["lease_credential_path"],
            "gateway_state_directory": domain["gateway_state_directory"],
            "execution_socket": domain["execution_socket"],
            "execution_event_socket": domain["execution_event_socket"],
            "execution_fence_credential_path":
                domain["execution_fence_credential_path"],
            "execution_state_directory": domain["execution_state_directory"],
        })
    files[STAGING_MANIFEST] = (_canonical_json({
        "schema": "hepta.agent-trust-domain-provisioning.v1",
        "version": 1,
        "apply_required": True,
        "one_execution_authority_per_domain": True,
        "shared_connect_group_role": "single-domain-compatibility-only",
        "domain_runtime_config_metadata":
            "root:root regular non-symlink single-link mode-0600",
        "uid_runtime_config_metadata":
            "root:<domain-agent-group> regular non-symlink single-link "
            "mode-0640",
        "agent_host_dropin_metadata":
            "root:root regular non-symlink single-link mode-0644",
        "domains": manifest_domains,
        "credentials_generated": False,
        "units_enabled": False,
        "services_started": False,
        "paper_authorized": False,
        "live_authorized": False,
    }), 0o644)
    return files


def _staging_root(root: Path, *, require_empty: bool) -> Path:
    if not root.is_absolute() or root == Path("/"):
        raise TrustDomainError("staging root must be an explicit absolute path")
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise TrustDomainError("staging root must be a real directory")
    resolved = root.resolve(strict=True)
    if resolved != root:
        raise TrustDomainError("staging root must not traverse symlinks")
    if require_empty and any(root.iterdir()):
        raise TrustDomainError("staging root must be empty")
    return root


def materialize_staging_root(root: Path, result: dict[str, Any]) -> None:
    root = _staging_root(root, require_empty=True)
    for relative, (contents, mode) in expected_staging_files(result).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0),
            mode)
        try:
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(contents):
                offset += os.write(descriptor, contents[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def validate_staging_root(root: Path, result: dict[str, Any]) -> None:
    root = _staging_root(root, require_empty=False)
    expected = expected_staging_files(result)
    expected_files = set(expected)
    expected_directories: set[str] = set()
    for relative in expected_files:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent

    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for path in root.rglob("*"):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(metadata.st_mode):
            raise TrustDomainError("staging root contains a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            observed_directories.add(relative)
        elif stat.S_ISREG(metadata.st_mode):
            observed_files.add(relative)
        else:
            raise TrustDomainError(
                f"staging root contains a non-regular artifact: {relative}")
    if (
            observed_files != expected_files or
            observed_directories != expected_directories):
        raise TrustDomainError("staging root artifact allowlist mismatch")
    for relative, (contents, mode) in expected.items():
        path = root / relative
        metadata = path.lstat()
        if (
                not stat.S_ISREG(metadata.st_mode) or
                metadata.st_nlink != 1 or
                stat.S_IMODE(metadata.st_mode) != mode or
                path.read_bytes() != contents):
            raise TrustDomainError(
                f"staging root artifact mismatch: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--identities", type=Path, default=IDENTITIES)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--write-staging-root", type=Path)
    operation.add_argument("--check-staging-root", type=Path)
    arguments = parser.parse_args()
    result = validate(
        arguments.policy.resolve(strict=True),
        arguments.fixture.resolve(strict=True),
        arguments.identities.resolve(strict=True))
    if arguments.write_staging_root is not None:
        materialize_staging_root(arguments.write_staging_root, result)
    if arguments.check_staging_root is not None:
        validate_staging_root(
            arguments.check_staging_root.resolve(strict=True), result)
    print(
        "hepta_agent_trust_domain_check: PASS "
        f"domains={result['domain_count']} "
        f"isolated={','.join(result['isolated_fields'])} "
        f"staging_written={str(arguments.write_staging_root is not None).lower()} "
        f"staging_checked={str(arguments.check_staging_root is not None).lower()} "
        "paper_authorized=false live_authorized=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
