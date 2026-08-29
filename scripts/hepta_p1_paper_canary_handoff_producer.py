#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Root-owned canonical handoff producer for one external-P1 PAPER canary.

The producer joins already-finalized, non-secret evidence.  It grants no
session or trading authority, never creates a credential, and publishes with
O_EXCL to one deterministic campaign/cycle path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any, Mapping


SCHEMA = "hepta.p1-paper-canary-handoff-producer-input.v1"
HANDOFF_SCHEMA = "hepta.p1-paper-canary-execution-handoff.v1"
BACKEND_TRANSFORM_VERSION = "hepta.p1-paper-canary-backend-transform.v1"
INPUT_ROOT = Path("/var/lib/hepta/p1-paper-canary-input")
CONTROL_ROOT = Path("/var/lib/hepta/p1-paper-canary-control")
ARTIFACT_ROOT = Path("/var/lib/hepta/p1-paper-canary")
ROOT_FINALIZER_SOCKET = "/run/hepta-p1-paper-canary-finalizer.sock"
ROOT_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-request.v1")
ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-request.v1")
ROOT_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-cleanup-receipt.v4")
ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA = (
    "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1")
ROOT_CLEANUP_OPERATION = "FINALIZE_EXTERNAL_P1"
OWNER_TOKEN_PATH = Path("/run/hepta-agent-alpha/sessions/session.token")
OWNER_AUTHORITY_ROOT = Path(
    "/var/lib/hepta-local-ai-paper-agent/session-authority")
OWNER_AUTHORITY_PATH = OWNER_AUTHORITY_ROOT / "session.token.authority.json"
OWNER_REVOKE_PATH = OWNER_AUTHORITY_ROOT / "session.token.revoke-token"
WINDOW_MS = 300_000
MAX_BYTES = 1024 * 1024
ROOT_CLEANUP_TIMEOUT_MS = 240_000
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REFERENCE_FIELDS = frozenset({
    "path", "file_sha256", "body_sha256", "size", "mode", "uid", "gid",
    "nlink",
})
PROFILE_FIELDS = frozenset({
    "path", "file_sha256", "size", "mode", "uid", "gid", "nlink",
})
INPUT_FIELDS = frozenset({
    "schema", "version", "campaign_id", "domain_id", "policy_reference",
    "source_baseline_reference", "p1_audit_reference",
    "watch_handoff_reference", "zero_exposure_reference",
    "admission_finalization_reference", "strategy_decision_reference",
    "tool_catalog_reference", "session_owner_reference",
    "runtime_profile_reference", "installed_images",
    "execution_service_epoch", "execution_service_fencing_generation",
    "paper_only", "live_authorized", "direct_broker_access",
    "authority_granted",
})
DECISION_FIELDS = frozenset({
    "schema", "campaign_id", "strategy_id", "strategy_version",
    "strategy_sha256", "decision_id", "cycle_id", "started_at_ms",
    "finished_at_ms", "paper_only", "live_authorized", "shadow_only",
    "information_packet_sha256", "catalog_sha256", "descriptor_sha256",
    "preflight_sha256", "regime", "setup_gates", "risk_challenges",
    "evidence_refs", "conflicts", "decision", "reason_codes",
    "trade_intent", "trade_intent_sha256", "campaign_open_request_id",
    "campaign_close_request_id", "mutation_attempted", "direct_broker_access",
    "final_outcome",
})
CATALOG_FIELDS = frozenset({
    "schema", "version", "catalog_sha256", "tools", "authority_granted",
    "body_sha256",
})
CATALOG_TOOL_FIELDS = frozenset({
    "call_role", "tool_name", "tool_descriptor_sha256", "effect", "phase",
})
OWNER_FIELDS = frozenset({
    "token_name", "token_path", "authority_path", "authority_file_sha256",
    "authority_body_sha256", "lease_generation", "session_id", "peer_uid",
    "peer_gid", "token_sha256", "revoke_bearer_path", "revoke_bearer_sha256",
    "owner_account", "owner_execution_domain",
})
IMAGE_FIELDS = frozenset({
    "role", "path", "file_sha256", "mode", "uid", "gid", "nlink",
})
IMAGE_PATHS = (
    ("executor", "/usr/libexec/hepta-p1-paper-canary-executor"),
    ("receipt-validator-v3", "/usr/libexec/hepta-paper-receipt-contracts"),
    ("receipt-validator-v2", "/usr/libexec/hepta-paper-receipt-contracts-v2-compat"),
    ("backend-adapter", "/usr/libexec/hepta-p1-paper-canary-backend-adapter"),
    ("handoff-producer", "/usr/libexec/hepta-p1-paper-canary-handoff-producer"),
    ("native-tool-client", "/usr/bin/heptactl"),
    ("campaign-operator", "/usr/libexec/hepta-ib-paper-campaign-operator"),
    ("root-finalizer", "/usr/libexec/hepta-p1-paper-canary-finalizer"),
    ("launch-joiner", "/usr/libexec/hepta-p1-paper-canary-launch-joiner"),
    ("owner-provisioner",
     "/usr/libexec/hepta-p1-paper-canary-owner-provisioner"),
    ("root-coordinator",
     "/usr/libexec/hepta-p1-paper-canary-root-coordinator"),
    ("crash-emergency-closer",
     "/usr/libexec/hepta-p1-paper-canary-crash-emergency-closer"),
    ("terminal-prover",
     "/usr/libexec/hepta-p1-paper-canary-terminal-prover"),
)
ROLE_PLAN = (
    ("preflight-health", "system.get_health", "READ_ONLY", "PREFLIGHT"),
    ("preflight-quote", "market.get_quote", "READ_ONLY", "PREFLIGHT"),
    ("preflight-account", "account.get_summary", "READ_ONLY", "PREFLIGHT"),
    ("preflight-positions", "portfolio.list_positions", "READ_ONLY", "PREFLIGHT"),
    ("preflight-orders", "orders.list", "READ_ONLY", "PREFLIGHT"),
    ("preflight-risk", "risk.get_limits", "READ_ONLY", "PREFLIGHT"),
    ("preflight-campaign", "campaign.status", "READ_ONLY", "PREFLIGHT"),
    ("open", "campaign.open_cycle", "CONTROL", "OPEN"),
    ("preview-order", "risk.preview_order", "READ_ONLY", "PREVIEW"),
    ("place", "trade.place_order", "MUTATION", "PLACE"),
    ("close", "campaign.close_cycle", "CONTROL", "CLOSE"),
    ("reconcile-orders", "orders.list", "READ_ONLY", "SNAPSHOT"),
    ("reconcile-positions", "portfolio.list_positions", "READ_ONLY", "SNAPSHOT"),
    ("cancel-order", "trade.cancel_order", "MUTATION", "CANCEL"),
    ("preview-flatten", "risk.preview_flatten", "READ_ONLY", "PREVIEW"),
    ("flatten-position", "trade.flatten_position", "MUTATION", "FLATTEN"),
    ("final-health", "system.get_health", "READ_ONLY", "RECONCILE"),
    ("final-account", "account.get_summary", "READ_ONLY", "RECONCILE"),
    ("final-positions", "portfolio.list_positions", "READ_ONLY", "RECONCILE"),
    ("final-orders", "orders.list", "READ_ONLY", "RECONCILE"),
    ("cleanup-risk", "risk.get_limits", "READ_ONLY", "CLEANUP"),
)
ROOT_CLEANUP_DESCRIPTOR = {
    "schema": "hepta.p1-paper-canary-root-cleanup-operation-descriptor.v1",
    "version": 1,
    "call_role": "cleanup-control",
    "tool_name": "host.finalize_external_p1",
    "operation": ROOT_CLEANUP_OPERATION,
    "effect": "CONTROL",
    "phase": "ROOT_CLEANUP",
    "socket_path": ROOT_FINALIZER_SOCKET,
    "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
    "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
    "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
    "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
    "max_request_bytes": MAX_BYTES,
    "max_response_bytes": MAX_BYTES,
    "timeout_ms": ROOT_CLEANUP_TIMEOUT_MS,
    "paper_only": True,
    "live_authorized": False,
    "authority_granted": False,
}


class ProducerError(RuntimeError):
    pass


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject(_value: str) -> None:
    raise ValueError("non-canonical number")


def canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ProducerError("canonical JSON failed") from error


def strict_canonical(raw: bytes, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise ProducerError(f"{label} size invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique,
            parse_float=_reject, parse_constant=_reject)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProducerError(f"{label} JSON invalid") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ProducerError(f"{label} is not canonical")
    return value


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def body_sha(document: dict[str, Any]) -> str:
    body = dict(document)
    claimed = body.pop("body_sha256", None)
    computed = sha(canonical_json(body))
    if claimed != computed:
        raise ProducerError("referenced body digest mismatch")
    return computed


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns)


def stable_read(
        path: Path, *, reference: Mapping[str, Any] | None = None,
        root_private: bool = False) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) & 0o022 or
                before.st_size < 1 or before.st_size > MAX_BYTES):
            raise ProducerError("input metadata unsafe")
        if root_private and (
                before.st_uid != 0 or before.st_gid != 0 or
                stat.S_IMODE(before.st_mode) != 0o600):
            raise ProducerError("producer input is not root:root 0600")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            payload = bytearray()
            while len(payload) <= MAX_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ProducerError("input unavailable") from error
    if (
            len(payload) > MAX_BYTES or _identity(before) != _identity(opened) or
            _identity(opened) != _identity(after)):
        raise ProducerError("input changed while reading")
    raw = bytes(payload)
    if reference is not None and (
            set(reference) not in {REFERENCE_FIELDS, PROFILE_FIELDS} or
            str(path) != reference["path"] or sha(raw) != reference["file_sha256"] or
            len(raw) != reference["size"] or
            stat.S_IMODE(after.st_mode) != reference["mode"] or
            after.st_uid != reference["uid"] or after.st_gid != reference["gid"] or
            after.st_nlink != reference["nlink"]):
        raise ProducerError("input reference mismatch")
    return raw


def _reference_document(reference: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    raw = stable_read(Path(reference["path"]), reference=reference)
    document = strict_canonical(raw, "referenced artifact")
    if body_sha(document) != reference["body_sha256"]:
        raise ProducerError("referenced artifact body mismatch")
    return document, raw


def _identifier(value: Any, label: str, pattern: re.Pattern[str] = IDENTIFIER) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ProducerError(f"{label} invalid")
    return value


def _deterministic_call_id(campaign: str, cycle: str, role: str) -> str:
    digest = hashlib.sha256(
        (campaign + "\0" + cycle + "\0" + role).encode("ascii")).hexdigest()
    return "p1-canary-" + digest[:32]


def _validate_images(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(IMAGE_PATHS):
        raise ProducerError("installed images invalid")
    result: list[dict[str, Any]] = []
    for item, (role, path) in zip(value, IMAGE_PATHS):
        if (
                not isinstance(item, dict) or set(item) != IMAGE_FIELDS or
                item["role"] != role or item["path"] != path or
                DIGEST.fullmatch(item["file_sha256"]) is None or
                item["mode"] != 0o755 or item["uid"] != 0 or item["gid"] != 0 or
                item["nlink"] != 1):
            raise ProducerError("installed image reference invalid")
        raw = stable_read(Path(path))
        metadata = os.lstat(path)
        if (
                sha(raw) != item["file_sha256"] or
                stat.S_IMODE(metadata.st_mode) != 0o755 or
                metadata.st_uid != 0 or metadata.st_gid != 0 or
                metadata.st_nlink != 1):
            raise ProducerError("installed image changed")
        result.append(dict(item))
    return result


def _load_executor_validator(executor_reference: Mapping[str, Any]) -> Any:
    path = Path("/usr/libexec/hepta-p1-paper-canary-executor")
    raw = stable_read(path)
    metadata = os.lstat(path)
    if (
            executor_reference.get("path") != str(path) or
            sha(raw) != executor_reference.get("file_sha256") or
            stat.S_IMODE(metadata.st_mode) != 0o755 or
            metadata.st_uid != 0 or metadata.st_gid != 0 or
            metadata.st_nlink != 1):
        raise ProducerError("installed executor validator image mismatch")
    from types import ModuleType
    module = ModuleType("_hepta_p1_canary_handoff_validator")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
    except Exception as error:
        raise ProducerError("installed executor validator unavailable") from error
    return module


def build_handoff(
        manifest_raw: bytes, *, now_ms: int,
        verify_installed: bool = True, validator: Any | None = None
) -> bytes:
    manifest = strict_canonical(manifest_raw, "producer input")
    if set(manifest) != INPUT_FIELDS or manifest["schema"] != SCHEMA or \
            manifest["version"] != 1:
        raise ProducerError("producer input fields invalid")
    campaign = _identifier(manifest["campaign_id"], "campaign")
    domain = _identifier(manifest["domain_id"], "domain", DOMAIN)
    if (
            manifest["paper_only"] is not True or
            manifest["live_authorized"] is not False or
            manifest["direct_broker_access"] is not False or
            manifest["authority_granted"] is not False):
        raise ProducerError("producer authority boundary invalid")
    references: dict[str, tuple[dict[str, Any], bytes]] = {}
    for field in (
            "policy_reference", "source_baseline_reference",
            "p1_audit_reference", "watch_handoff_reference",
            "zero_exposure_reference", "admission_finalization_reference",
            "strategy_decision_reference", "tool_catalog_reference"):
        value = manifest[field]
        if not isinstance(value, dict):
            raise ProducerError("producer reference invalid")
        references[field] = _reference_document(value)
    decision = references["strategy_decision_reference"][0]
    if (
            set(decision) != DECISION_FIELDS or
            decision["schema"] != "hepta.autonomous-paper-decision-receipt.v1" or
            decision["campaign_id"] != campaign or decision["decision"] != "TRADE" or
            decision["paper_only"] is not True or
            decision["live_authorized"] is not False or
            decision["shadow_only"] is not True or
            decision["mutation_attempted"] is not False or
            decision["direct_broker_access"] is not False or
            not isinstance(decision["trade_intent"], dict) or
            sha(canonical_json(decision["trade_intent"])) !=
                decision["trade_intent_sha256"]):
        raise ProducerError("strategy decision is not a deterministic trade")
    cycle = _identifier(decision["cycle_id"], "cycle")
    catalog = references["tool_catalog_reference"][0]
    if (
            set(catalog) != CATALOG_FIELDS or
            catalog["schema"] != "hepta.p1-paper-canary-tool-catalog.v1" or
            catalog["version"] != 1 or catalog["authority_granted"] is not False or
            not isinstance(catalog["tools"], list) or
            len(catalog["tools"]) != len(ROLE_PLAN)):
        raise ProducerError("tool catalog invalid")
    body_sha(catalog)
    tool_calls: list[dict[str, Any]] = []
    for item, expected in zip(catalog["tools"], ROLE_PLAN):
        if not isinstance(item, dict) or set(item) != CATALOG_TOOL_FIELDS:
            raise ProducerError("tool descriptor plan invalid")
        role, name, effect, phase = expected
        if item != {
                "call_role": role, "tool_name": name,
                "tool_descriptor_sha256": item["tool_descriptor_sha256"],
                "effect": effect, "phase": phase} or \
                DIGEST.fullmatch(item["tool_descriptor_sha256"]) is None:
            raise ProducerError("tool descriptor plan drift")
        call_id = _deterministic_call_id(campaign, cycle, role)
        tool_calls.append({
            **item, "tool_call_id": call_id,
            "command_id": None if effect == "READ_ONLY" else call_id,
        })
    projection = [{
        key: call[key] for key in (
            "tool_call_id", "tool_name", "tool_descriptor_sha256", "effect")}
        for call in tool_calls]
    root_cleanup_call_id = _deterministic_call_id(
        campaign, cycle, "cleanup-control")
    root_cleanup_call = {
        "call_role": "cleanup-control",
        "tool_name": "host.finalize_external_p1",
        "operation": ROOT_CLEANUP_OPERATION,
        "effect": "CONTROL",
        "phase": "ROOT_CLEANUP",
        "socket_path": ROOT_FINALIZER_SOCKET,
        "request_schema": ROOT_CLEANUP_REQUEST_SCHEMA,
        "emergency_request_schema": ROOT_EMERGENCY_CLEANUP_REQUEST_SCHEMA,
        "response_schema": ROOT_CLEANUP_RECEIPT_SCHEMA,
        "emergency_response_schema": ROOT_EMERGENCY_CLEANUP_RECEIPT_SCHEMA,
        "tool_call_id": root_cleanup_call_id,
        "command_id": root_cleanup_call_id,
        "tool_descriptor_sha256": sha(canonical_json(
            ROOT_CLEANUP_DESCRIPTOR)),
    }
    if sha(canonical_json(catalog["tools"])) != catalog["catalog_sha256"]:
        raise ProducerError("tool catalog digest mismatch")
    owner = manifest["session_owner_reference"]
    if not isinstance(owner, dict) or set(owner) != OWNER_FIELDS:
        raise ProducerError("session owner reference invalid")
    if (
            isinstance(owner["peer_uid"], bool) or
            owner["peer_uid"] != 2104 or
            isinstance(owner["peer_gid"], bool) or
            owner["peer_gid"] != 2104 or
            owner["token_name"] != "session.token" or
            owner["token_path"] != str(OWNER_TOKEN_PATH) or
            owner["authority_path"] != str(OWNER_AUTHORITY_PATH) or
            owner["revoke_bearer_path"] != str(OWNER_REVOKE_PATH) or
            re.fullmatch(r"DU[0-9]{1,16}", str(owner["owner_account"])) is
                None or
            owner["owner_execution_domain"] != "PAPER:alpha"):
        raise ProducerError("session owner peer identity invalid")
    parent = os.lstat(OWNER_AUTHORITY_ROOT)
    if (
            stat.S_ISLNK(parent.st_mode) or
            not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or
            parent.st_gid != 0 or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ProducerError("session owner parent metadata invalid")
    token = stable_read(OWNER_TOKEN_PATH)
    revoke = stable_read(OWNER_REVOKE_PATH)
    authority = stable_read(OWNER_AUTHORITY_PATH)
    token_metadata = os.lstat(OWNER_TOKEN_PATH)
    revoke_metadata = os.lstat(OWNER_REVOKE_PATH)
    authority_metadata = os.lstat(OWNER_AUTHORITY_PATH)
    if (
            token_metadata.st_uid != 2104 or token_metadata.st_gid != 2104 or
            stat.S_IMODE(token_metadata.st_mode) != 0o400 or
            revoke_metadata.st_uid != 0 or revoke_metadata.st_gid != 0 or
            stat.S_IMODE(revoke_metadata.st_mode) != 0o600 or
            authority_metadata.st_uid != 0 or authority_metadata.st_gid != 0 or
            stat.S_IMODE(authority_metadata.st_mode) != 0o600 or
            re.fullmatch(rb"[0-9a-f]{64}\n", revoke) is None):
        raise ProducerError("session owner material metadata invalid")
    authority_document = strict_canonical(authority, "session owner authority")
    if (
            sha(token) != owner["token_sha256"] or token != revoke or
            sha(revoke) != owner["revoke_bearer_sha256"] or
            sha(authority) != owner["authority_file_sha256"] or
            body_sha(authority_document) != owner["authority_body_sha256"] or
            authority_document.get("owner_account") !=
                owner["owner_account"] or
            authority_document.get("owner_execution_domain") !=
                owner["owner_execution_domain"] or
            owner["token_path"] !=
                f"/run/hepta-agent-{domain}/sessions/{owner['token_name']}"):
        raise ProducerError("session owner binding changed")
    profile = manifest["runtime_profile_reference"]
    profile_raw = stable_read(Path(profile["path"]), reference=profile)
    text = profile_raw.decode("ascii", errors="strict")
    profile_values = dict(
        line.split("=", 1) for line in text.splitlines()
        if line and not line.startswith("#"))
    if (
            profile_values.get("HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY") != "1" or
            profile_values.get("HEPTA_EXECUTION_MAX_ORDER_NOTIONAL") != "5000" or
            profile_values.get("HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL") != "5000" or
            not profile_values.get("HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", "").isdigit() or
            int(profile_values["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"]) > 5000):
        raise ProducerError("external-P1 LMT/DAY runtime profile missing")
    images = _validate_images(manifest["installed_images"]) if verify_installed \
        else [dict(item) for item in manifest["installed_images"]]
    issued = (now_ms // 1000) * 1000
    handoff_body = {
        "schema": HANDOFF_SCHEMA, "version": 1,
        "issued_at_ms": issued, "expires_at_ms": issued + WINDOW_MS,
        "campaign_id": campaign, "domain_id": domain,
        "policy_sha256": manifest["policy_reference"]["file_sha256"],
        "source_baseline_sha256": manifest[
            "source_baseline_reference"]["file_sha256"],
        "p1_audit_receipt_sha256": manifest["p1_audit_reference"]["file_sha256"],
        "watch_handoff_receipt_file_sha256": manifest[
            "watch_handoff_reference"]["file_sha256"],
        "watch_handoff_receipt_body_sha256": manifest[
            "watch_handoff_reference"]["body_sha256"],
        "zero_exposure_attestation_sha256": manifest[
            "zero_exposure_reference"]["file_sha256"],
        "admission_finalization_receipt_sha256": manifest[
            "admission_finalization_reference"]["file_sha256"],
        "strategy_id": decision["strategy_id"],
        "strategy_version": decision["strategy_version"],
        "strategy_sha256": decision["strategy_sha256"],
        "decision_id": decision["decision_id"],
        "decision_sha256": manifest[
            "strategy_decision_reference"]["file_sha256"],
        "cycle_id": cycle, "intent": decision["trade_intent"],
        "intent_sha256": decision["trade_intent_sha256"],
        "tool_catalog_sha256": catalog["catalog_sha256"],
        "tool_descriptor_set_sha256": sha(canonical_json(projection)),
        "tool_calls": tool_calls, "root_cleanup_call": root_cleanup_call,
        "installed_images": images,
        "installed_images_sha256": sha(canonical_json(images)),
        "runtime_profile_reference": profile,
        "backend_transform_version": BACKEND_TRANSFORM_VERSION,
        "execution_service_epoch": manifest["execution_service_epoch"],
        "execution_service_fencing_generation": manifest[
            "execution_service_fencing_generation"],
        "session_owner_reference": owner,
        "paper_only": True, "live_authorized": False,
        "direct_broker_access": False, "authority_granted": False,
        "one_order_only": True, "end_flat_required": True,
    }
    handoff = {**handoff_body, "body_sha256": sha(canonical_json(handoff_body))}
    raw = canonical_json(handoff)
    checker = validator or _load_executor_validator(images[0])
    checker.validate_handoff(raw, now_ms=now_ms)
    return raw


def publish_handoff(
        raw: bytes, campaign: str, cycle: str, peer_gid: int) -> Path:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ProducerError("handoff producer requires root")
    control_campaign = CONTROL_ROOT / campaign
    control_campaign.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(control_campaign, 0, 0)
    os.chmod(control_campaign, 0o700)
    control_directory = control_campaign / cycle
    control_directory.mkdir(mode=0o700, exist_ok=True)
    os.chown(control_directory, 0, 0)
    os.chmod(control_directory, 0o700)
    control_metadata = os.lstat(control_directory)
    if (
            not stat.S_ISDIR(control_metadata.st_mode) or
            control_metadata.st_uid != 0 or control_metadata.st_gid != 0 or
            stat.S_IMODE(control_metadata.st_mode) != 0o700):
        raise ProducerError("handoff control directory unsafe")
    artifact_campaign = ARTIFACT_ROOT / campaign
    artifact_campaign.mkdir(mode=0o711, parents=True, exist_ok=True)
    os.chown(artifact_campaign, 0, 0)
    os.chmod(artifact_campaign, 0o711)
    artifact_directory = artifact_campaign / cycle
    artifact_directory.mkdir(mode=0o1730, exist_ok=True)
    os.chown(artifact_directory, 0, peer_gid)
    os.chmod(artifact_directory, 0o1730)
    metadata = os.lstat(artifact_directory)
    if (
            not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or
            metadata.st_gid != peer_gid or
            stat.S_IMODE(metadata.st_mode) != 0o1730):
        raise ProducerError("artifact output directory unsafe")
    path = control_directory / "execution-handoff.v1.json"
    flags = os.O_WRONLY | os.O_CLOEXEC | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ProducerError("handoff publish failed") from error
    return path


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    arguments = parser.parse_args()
    campaign = _identifier(arguments.campaign_id, "campaign")
    cycle = _identifier(arguments.cycle_id, "cycle")
    if os.geteuid() != 0 or os.getegid() != 0:
        raise ProducerError("handoff producer requires root")
    input_path = INPUT_ROOT / campaign / cycle / "producer-input.v1.json"
    manifest_raw = stable_read(input_path, root_private=True)
    raw = build_handoff(manifest_raw, now_ms=time.time_ns() // 1_000_000)
    document = strict_canonical(raw, "execution handoff")
    if document["campaign_id"] != campaign or document["cycle_id"] != cycle:
        raise ProducerError("producer input path binding mismatch")
    output = publish_handoff(
        raw, campaign, cycle,
        document["session_owner_reference"]["peer_gid"])
    print(canonical_json({
        "authority_granted": False, "handoff_file_sha256": sha(raw),
        "path": str(output), "status": "PUBLISHED",
    }).decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
