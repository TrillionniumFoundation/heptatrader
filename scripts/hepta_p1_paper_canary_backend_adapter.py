#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Fixed production I/O adapter for the external-P1 PAPER canary.

This is the only production implementation of the executor's injected
backend.  It invokes the reviewed Unix Tool Gateway through the fixed native
client, invokes the root campaign gate through its fixed Unix socket, and
owns only the canary journal/artifact directory.  It never accepts a plugin
name, endpoint, credential value, broker identifier, or output path from the
command line.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time
from typing import Any, Mapping


ADAPTER_TRANSFORM_VERSION = "hepta.p1-paper-canary-backend-transform.v1"
BACKEND_RESPONSE_SCHEMA = "hepta.p1-paper-canary-backend-response.v1"
CAMPAIGN_REQUEST_SCHEMA = "hepta.ib-paper-campaign-request.v1"
CAMPAIGN_RESPONSE_SCHEMA = "hepta.ib-paper-campaign-response.v1"
HEPTACTL = Path("/usr/bin/heptactl")
MAX_BYTES = 1024 * 1024
TOOL_TIMEOUT_SECONDS = 20
CAMPAIGN_TIMEOUT_SECONDS = 30
ROOT_FINALIZER_TIMEOUT_SECONDS = 240
ROOT_EMERGENCY_FINALIZER_TIMEOUT_SECONDS = 45
ROOT_FINALIZER_SOCKET = Path("/run/hepta-p1-paper-canary-finalizer.sock")
PROFILE_REQUIRED = {
    "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY": "1",
    "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
    "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
}
PROFILE_KEYS = (
    "HEPTA_EXECUTION_EXTERNAL_P1_CANARY_LMT_DAY",
    "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL", "HEPTA_IB_EXECUTION_MODE",
    "HEPTA_IB_PAPER_ACCOUNT", "HEPTA_IB_PAPER_HOST",
    "HEPTA_IB_PAPER_PORT", "HEPTA_IB_PAPER_CLIENT_ID",
    "HEPTA_IB_PAPER_MAX_ORDER_QTY", "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL",
    "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE",
    "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS",
    "HEPTA_IB_PAPER_MAX_GROSS_POSITION", "HEPTA_IB_PAPER_QUOTE_CONTRACTS",
    "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT",
    "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS", "HEPTA_IB_EXECUTION_GATEWAY_UID",
    "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID", "HEPTA_IB_EXECUTION_DOMAIN_ID",
    "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES",
    "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS",
    "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS",
)
CANONICAL_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
PAPER_ACCOUNT = re.compile(r"DU[0-9]{1,16}")
PREVIEW_ENVELOPE_FIELDS = frozenset({
    "approved", "preview_permit", "command_id", "permit_expires_at_ms",
    "single_use", "service_epoch", "service_fencing_generation",
    "authoritative_preview",
})
ORDER_AUTHORITATIVE_PREVIEW_FIELDS = frozenset({
    "source", "authoritative", "subscription_id", "observed_at_ms",
    "stale_after_ms", "stale", "order_type", "tif", "limit_price",
    "reference_price", "quote_bid", "quote_ask", "risk_approved",
})
FLATTEN_AUTHORITATIVE_PREVIEW_FIELDS = frozenset({
    "source", "authoritative", "position_connection_epoch",
    "position_generation", "position_quantity", "side", "quantity",
    "order_type", "tif", "limit_price", "reference_price", "quote_bid",
    "quote_ask", "quote_subscription_id", "quote_observed_at_ms",
    "reduce_only", "atomic", "risk_approved",
})
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REASON = re.compile(r"[A-Z][A-Z0-9_]{1,95}")


class AdapterError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DecimalToken(Decimal):
    def __new__(cls, value: str) -> "_DecimalToken":
        result = Decimal.__new__(cls, value)
        result.lexeme = value
        return result


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite number")


def _strict_json(raw: bytes, label: str, *, decimals: bool = False) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise AdapterError(f"{label}_SIZE_INVALID")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique,
            parse_float=_DecimalToken if decimals else _reject_float,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AdapterError(f"{label}_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise AdapterError(f"{label}_ROOT_INVALID")
    return value


def _reject_float(_value: str) -> None:
    raise ValueError("floating JSON forbidden")


def _canonical(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise AdapterError("ADAPTER_CANONICAL_JSON_INVALID") from error


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _integer(value: Any, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError(code)
    if minimum is not None and value < minimum:
        raise AdapterError(code)
    return value


def _decimal_text(value: Any, code: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise AdapterError(code)
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise AdapterError(code) from error
    if not decimal.is_finite() or decimal <= 0:
        raise AdapterError(code)
    rendered = format(decimal, "f")
    whole, dot, fraction = rendered.partition(".")
    if dot:
        fraction = fraction.rstrip("0")
        rendered = whole if not fraction else whole + "." + fraction
    if len(rendered) > 32:
        raise AdapterError(code)
    return rendered


def _canonical_nonnegative_decimal(
        value: Any, code: str, *, expected: str | None = None) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise AdapterError(code)
    if isinstance(value, _DecimalToken):
        token = value.lexeme
        if CANONICAL_DECIMAL.fullmatch(token) is None:
            raise AdapterError(code)
    elif isinstance(value, int):
        token = str(value)
    else:
        token = format(value, "f")
        if CANONICAL_DECIMAL.fullmatch(token) is None:
            raise AdapterError(code)
    decimal = Decimal(token)
    if not decimal.is_finite() or decimal < 0 or (decimal == 0 and token.startswith("-")):
        raise AdapterError(code)
    rendered = format(decimal, "f")
    whole, dot, fraction = rendered.partition(".")
    if dot:
        fraction = fraction.rstrip("0")
        rendered = whole if not fraction else whole + "." + fraction
    if isinstance(value, _DecimalToken) and token != rendered:
        raise AdapterError(code)
    if expected is not None and rendered != expected:
        raise AdapterError(code)
    return rendered


def _quantity(value: Any, code: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise AdapterError(code)
    decimal = Decimal(value)
    if decimal != decimal.to_integral_value():
        raise AdapterError(code)
    result = int(decimal)
    allowed = {-1, 0, 1} if allow_zero else {-1, 1}
    if result not in allowed:
        raise AdapterError(code)
    return result


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns)


def _stable_read(
        path: Path, *, maximum: int = MAX_BYTES,
        expected: Mapping[str, Any] | None = None,
        private_uid: int | None = None, private_gid: int | None = None,
        private_mode: int = 0o600) -> bytes:
    try:
        before = os.lstat(path)
        if (
                stat.S_ISLNK(before.st_mode) or
                not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                stat.S_IMODE(before.st_mode) & 0o022 or
                before.st_size < 1 or before.st_size > maximum):
            raise AdapterError("ADAPTER_INPUT_METADATA_UNSAFE")
        if private_uid is not None and (
                before.st_uid != private_uid or
                (private_gid is not None and before.st_gid != private_gid) or
                stat.S_IMODE(before.st_mode) != private_mode):
            raise AdapterError("ADAPTER_PRIVATE_INPUT_UNSAFE")
        descriptor = os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    descriptor, min(65536, maximum + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise AdapterError("ADAPTER_INPUT_UNAVAILABLE") from error
    if (
            len(payload) > maximum or _identity(before) != _identity(opened) or
            _identity(opened) != _identity(after)):
        raise AdapterError("ADAPTER_INPUT_CHANGED")
    raw = bytes(payload)
    if expected is not None and (
            str(path) != expected["path"] or
            _sha(raw) != expected["file_sha256"] or
            len(raw) != expected["size"] or
            stat.S_IMODE(after.st_mode) != expected["mode"] or
            after.st_uid != expected["uid"] or after.st_gid != expected["gid"] or
            after.st_nlink != expected["nlink"]):
        raise AdapterError("ADAPTER_INPUT_REFERENCE_MISMATCH")
    return raw


def _profile_fields(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise AdapterError("ADAPTER_RUNTIME_PROFILE_INVALID") from error
    result: dict[str, str] = {}
    ordered_keys: list[str] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            raise AdapterError("ADAPTER_RUNTIME_PROFILE_INVALID")
        key, separator, value = line.partition("=")
        if (
                not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,95}", key) or
                key in result or not value or len(value) > 256):
            raise AdapterError("ADAPTER_RUNTIME_PROFILE_INVALID")
        result[key] = value
        ordered_keys.append(key)
    for key, expected in PROFILE_REQUIRED.items():
        if result.get(key) != expected:
            raise AdapterError("ADAPTER_EXTERNAL_P1_LMT_PROFILE_REQUIRED")
    exact = {
        "HEPTA_EXECUTION_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_EXECUTION_MODE": "PAPER",
        "HEPTA_IB_PAPER_HOST": "127.0.0.1",
        "HEPTA_IB_PAPER_PORT": "4002",
        "HEPTA_IB_PAPER_CLIENT_ID": "701",
        "HEPTA_IB_PAPER_MAX_ORDER_QTY": "1",
        "HEPTA_IB_PAPER_MAX_ORDER_NOTIONAL": "5000",
        "HEPTA_IB_PAPER_MAX_ORDERS_PER_MINUTE": "1",
        "HEPTA_IB_PAPER_MAX_ACTIVE_ORDERS": "1",
        "HEPTA_IB_PAPER_MAX_GROSS_POSITION": "1",
        "HEPTA_IB_PAPER_QUOTE_CONTRACTS":
            "EUR.USD|EUR|CASH|IDEALPRO|USD",
        "HEPTA_IB_PAPER_PRIMARY_QUOTE_INSTRUMENT": "EUR.USD",
        "HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS": "5000",
        "HEPTA_IB_EXECUTION_GATEWAY_UID": "2101",
        "HEPTA_IB_EXECUTION_GATEWAY_AGENT_ID": "alpha",
        "HEPTA_IB_EXECUTION_DOMAIN_ID": "alpha",
        "HEPTA_IB_EXECUTION_MAX_REQUEST_BYTES": "16384",
        "HEPTA_IB_EXECUTION_IO_TIMEOUT_MS": "2500",
        "HEPTA_IB_EXECUTION_READINESS_TIMEOUT_MS": "30000",
        "HEPTA_IB_EXECUTION_RECONNECT_TIMEOUT_MS": "180000",
    }
    if (
            tuple(ordered_keys) != PROFILE_KEYS or set(result) != set(PROFILE_KEYS) or
            any(result.get(key) != expected for key, expected in exact.items()) or
            PAPER_ACCOUNT.fullmatch(result.get("HEPTA_IB_PAPER_ACCOUNT", ""))
                is None):
        raise AdapterError("ADAPTER_RUNTIME_PROFILE_BOUNDARY_INVALID")
    return result


def _stable_image(expected: Mapping[str, Any]) -> bytes:
    path = Path(expected["path"])
    raw = _stable_read(path)
    metadata = os.lstat(path)
    if (
            set(expected) != {
                "role", "path", "file_sha256", "mode", "uid", "gid", "nlink"} or
            _sha(raw) != expected["file_sha256"] or
            stat.S_IMODE(metadata.st_mode) != expected["mode"] or
            metadata.st_uid != expected["uid"] or
            metadata.st_gid != expected["gid"] or
            metadata.st_nlink != expected["nlink"]):
        raise AdapterError("ADAPTER_INSTALLED_IMAGE_MISMATCH")
    return raw


def _ensure_private_directory(path: Path, uid: int, gid: int) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise AdapterError("ADAPTER_ARTIFACT_DIRECTORY_UNAVAILABLE") from error
    if (
            stat.S_ISLNK(metadata.st_mode) or
            not stat.S_ISDIR(metadata.st_mode) or
            metadata.st_uid != 0 or metadata.st_gid != gid or
            stat.S_IMODE(metadata.st_mode) != 0o1730 or os.geteuid() != uid or
            os.getegid() != gid):
        raise AdapterError("ADAPTER_ARTIFACT_DIRECTORY_UNSAFE")


class ProductionBackend:
    """Concrete one-shot backend; constructor reopens every pinned boundary."""

    def __init__(self, executor: Any, handoff: Any) -> None:
        self._executor = executor
        self._handoff = handoff
        document = handoff.document
        if document["backend_transform_version"] != ADAPTER_TRANSFORM_VERSION:
            raise AdapterError("ADAPTER_TRANSFORM_PIN_MISMATCH")
        self._raw_handoff = handoff.raw
        self._domain = document["domain_id"]
        self._campaign = document["campaign_id"]
        self._cycle = document["cycle_id"]
        self._owner = document["session_owner_reference"]
        if (
                os.geteuid() != self._owner["peer_uid"] or
                os.getegid() != self._owner["peer_gid"]):
            raise AdapterError("ADAPTER_SESSION_PEER_UID_MISMATCH")
        token = _stable_read(
            Path(self._owner["token_path"]), maximum=4096,
            private_uid=self._owner["peer_uid"],
            private_gid=self._owner["peer_gid"], private_mode=0o400)
        if _sha(token) != self._owner["token_sha256"]:
            raise AdapterError("ADAPTER_SESSION_TOKEN_DIGEST_MISMATCH")
        profile = _stable_read(
            Path(document["runtime_profile_reference"]["path"]),
            maximum=65536, expected=document["runtime_profile_reference"])
        self._profile = _profile_fields(profile)
        self._owner_account = self._profile["HEPTA_IB_PAPER_ACCOUNT"]
        self._owner_execution_domain = "PAPER:alpha"
        if (
                self._owner.get("owner_account") != self._owner_account or
                self._owner.get("owner_execution_domain") !=
                    self._owner_execution_domain):
            raise AdapterError("ADAPTER_SESSION_OWNER_SCOPE_MISMATCH")
        self._quote_max_age_ms = int(
            self._profile["HEPTA_IB_PAPER_QUOTE_MAX_AGE_MS"])
        self._tool_socket = Path(
            f"/run/hepta-agent-{self._domain}/tools.sock")
        self._campaign_socket = Path(
            f"/run/hepta-agent-{self._domain}/campaign.sock")
        self._calls = {
            call["tool_call_id"]: call for call in document["tool_calls"]}
        self._call_roles = {
            call["call_role"]: call for call in document["tool_calls"]}
        self._epoch = document["execution_service_epoch"]
        self._fence = document["execution_service_fencing_generation"]
        self._adapter_sha = handoff.images["backend-adapter"]["file_sha256"]
        self._images = handoff.images
        for role in (
                "backend-adapter", "native-tool-client", "campaign-operator",
                "root-finalizer"):
            _stable_image(self._images[role])
        self._preview_permit: str | None = None
        self._flatten_permit: str | None = None
        self._quote_subscription_id: str | None = None
        self._ib_connection_epoch: int | None = None
        self._last_order_generation: int | None = None
        self._last_position_generation: int | None = None
        self._last_fx_cash_generation: int | None = None
        self._raw_order_ids: dict[str, int] = {}
        self._artifact_directory = Path(
            executor.ARTIFACT_ROOT.as_posix()) / self._campaign / self._cycle
        _ensure_private_directory(
            self._artifact_directory, self._owner["peer_uid"],
            self._owner["peer_gid"])
        self._journal = self._artifact_directory / "execution-journal.v1.jsonl"
        self._checkpoint_payloads: dict[str, bytes] = {}
        self._root_cleanup_receipt_raw: bytes | None = None

    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def read_handoff(self) -> bytes:
        return self._raw_handoff

    def append_journal(self, record: bytes) -> None:
        if (
                not isinstance(record, bytes) or not record.endswith(b"\n") or
                len(record) > MAX_BYTES):
            raise AdapterError("ADAPTER_JOURNAL_RECORD_INVALID")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._journal, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if (
                        not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
                        metadata.st_uid != self._owner["peer_uid"] or
                        stat.S_IMODE(metadata.st_mode) != 0o600):
                    raise AdapterError("ADAPTER_JOURNAL_METADATA_UNSAFE")
                offset = 0
                while offset < len(record):
                    offset += os.write(descriptor, record[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise AdapterError("ADAPTER_JOURNAL_APPEND_FAILED") from error

    def reopen_journal(self) -> Mapping[str, Any]:
        if not self._journal.exists():
            return {
                "path": str(self._journal), "raw": b"",
                "secure_reopen": True, "mode": 0o600, "nlink": 1,
            }
        raw = _stable_read(
            self._journal, maximum=MAX_BYTES,
            private_uid=self._owner["peer_uid"])
        metadata = os.lstat(self._journal)
        return {
            "path": str(self._journal), "raw": raw,
            "secure_reopen": True,
            "mode": stat.S_IMODE(metadata.st_mode), "nlink": metadata.st_nlink,
        }

    def _publish_peer_artifacts(
            self, artifacts: Mapping[str, bytes], *, checkpoint: bool
    ) -> None:
        allowed = re.compile(r"[a-z0-9][a-z0-9.-]{0,95}\.json")
        for name in sorted(artifacts):
            payload = artifacts[name]
            if (
                    allowed.fullmatch(name) is None or not isinstance(payload, bytes) or
                    not payload.endswith(b"\n") or len(payload) > MAX_BYTES):
                raise AdapterError("ADAPTER_ARTIFACT_INVALID")
            if (
                    name in {
                        "root-cleanup-receipt.v4.json",
                        "root-emergency-cleanup-receipt.v1.json"} and
                    self._root_cleanup_receipt_raw == payload):
                # Root artifacts live below CONTROL_ROOT.  The returned bytes
                # are used in-memory for receipt validation only and are never
                # copied into the peer-writable artifact tree.
                continue
            path = self._artifact_directory / name
            if path.exists():
                if (
                        name in {
                            "root-cleanup-receipt.v4.json",
                            "root-emergency-cleanup-receipt.v1.json"} and
                        self._root_cleanup_receipt_raw == payload):
                    metadata = os.lstat(path)
                    if (
                            not stat.S_ISREG(metadata.st_mode) or
                            metadata.st_uid != 0 or metadata.st_gid != 0 or
                            stat.S_IMODE(metadata.st_mode) != 0o600 or
                            metadata.st_nlink != 1 or
                            metadata.st_size != len(payload)):
                        raise AdapterError(
                            "ADAPTER_ROOT_RECEIPT_METADATA_UNSAFE")
                    continue
                prior = self._checkpoint_payloads.get(name)
                if prior != payload:
                    raise AdapterError("ADAPTER_ARTIFACT_ALREADY_EXISTS")
                reopened = _stable_read(
                    path, maximum=MAX_BYTES,
                    private_uid=self._owner["peer_uid"])
                if reopened != payload:
                    raise AdapterError("ADAPTER_CHECKPOINT_CHANGED")
                continue
            flags = os.O_WRONLY | os.O_CLOEXEC | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path, flags, 0o600)
                try:
                    offset = 0
                    while offset < len(payload):
                        offset += os.write(descriptor, payload[offset:])
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            except OSError as error:
                raise AdapterError("ADAPTER_ARTIFACT_PUBLISH_FAILED") from error
            if checkpoint:
                self._checkpoint_payloads[name] = payload

    def publish_checkpoint(self, artifacts: Mapping[str, bytes]) -> None:
        normal = {
            "pre-cleanup-response-bundle.v1.json",
            "pre-cleanup-flat-evidence.v1.json",
            "root-cleanup-request.v1.json",
        }
        emergency = {
            "root-emergency-cleanup-evidence.v1.json",
            "root-emergency-cleanup-request.v1.json",
        }
        if frozenset(artifacts) not in {frozenset(normal), frozenset(emergency)}:
            raise AdapterError("ADAPTER_CHECKPOINT_ARTIFACT_SET_INVALID")
        self._publish_peer_artifacts(artifacts, checkpoint=True)

    def publish_artifacts(self, artifacts: Mapping[str, bytes]) -> None:
        self._publish_peer_artifacts(artifacts, checkpoint=False)

    def finalize_root_cleanup(self, request: bytes) -> bytes:
        if self._root_cleanup_receipt_raw is not None:
            raise AdapterError("ADAPTER_ROOT_CLEANUP_REPLAY_FORBIDDEN")
        document = _strict_json(request, "ADAPTER_ROOT_CLEANUP_REQUEST")
        variants = {
            "hepta.p1-paper-canary-root-cleanup-request.v1": (
                "hepta.p1-paper-canary-root-cleanup-receipt.v4", 4,
                "ROOT_CLEANUP_COMPLETE_DENY_ALL",
                "root-cleanup-receipt.v4.json",
                ROOT_FINALIZER_TIMEOUT_SECONDS),
            "hepta.p1-paper-canary-root-emergency-cleanup-request.v1": (
                "hepta.p1-paper-canary-root-emergency-cleanup-receipt.v1", 1,
                "ROOT_EMERGENCY_CLEANUP_COMPLETE_DENY_ALL",
                "root-emergency-cleanup-receipt.v1.json",
                ROOT_EMERGENCY_FINALIZER_TIMEOUT_SECONDS),
        }
        variant = variants.get(document.get("schema"))
        if (
                _canonical(document) != request or
                variant is None or
                document.get("cleanup_tool_call_id") !=
                    self._handoff.document["root_cleanup_call"]["tool_call_id"] or
                document.get("cleanup_command_id") !=
                    self._handoff.document["root_cleanup_call"]["command_id"]):
            raise AdapterError("ADAPTER_ROOT_CLEANUP_REQUEST_INVALID")
        _stable_image(self._images["root-finalizer"])
        assert variant is not None
        (_response_schema, _response_version, _response_status,
         _response_name, timeout_seconds) = variant
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        response = bytearray()
        try:
            channel.settimeout(timeout_seconds)
            channel.connect(str(ROOT_FINALIZER_SOCKET))
            channel.sendall(request)
            channel.shutdown(socket.SHUT_WR)
            while len(response) <= MAX_BYTES:
                chunk = channel.recv(min(65536, MAX_BYTES + 1 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
        except OSError as error:
            raise AdapterError("ADAPTER_ROOT_CLEANUP_EXCHANGE_UNCERTAIN") from error
        finally:
            channel.close()
        response_raw = bytes(response)
        receipt = _strict_json(
            response_raw, "ADAPTER_ROOT_CLEANUP_RECEIPT")
        (response_schema, response_version, response_status,
         _response_name, _timeout_seconds) = variant
        if (
                _canonical(receipt) != response_raw or
                receipt.get("schema") != response_schema or
                receipt.get("version") != response_version or
                receipt.get("status") != response_status):
            raise AdapterError("ADAPTER_ROOT_CLEANUP_RECEIPT_INVALID")
        self._root_cleanup_receipt_raw = response_raw
        return response_raw

    def _campaign_exchange(
            self, request: dict[str, Any]) -> tuple[bytes, bytes, dict[str, Any]]:
        _stable_image(self._images["campaign-operator"])
        request_raw = _canonical(request)
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        response = bytearray()
        try:
            channel.settimeout(CAMPAIGN_TIMEOUT_SECONDS)
            channel.connect(str(self._campaign_socket))
            channel.sendall(request_raw)
            channel.shutdown(socket.SHUT_WR)
            while len(response) <= MAX_BYTES:
                chunk = channel.recv(min(65536, MAX_BYTES + 1 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
        except OSError as error:
            raise AdapterError("ADAPTER_CAMPAIGN_EXCHANGE_UNCERTAIN") from error
        finally:
            channel.close()
        response_raw = bytes(response)
        parsed = _strict_json(response_raw, "ADAPTER_CAMPAIGN_RESPONSE")
        if (
                set(parsed) != {
                    "schema", "version", "status", "action", "request_id",
                    "domain_id", "campaign_id", "reason_code", "detail", "state"} or
                parsed["schema"] != CAMPAIGN_RESPONSE_SCHEMA or
                parsed["version"] != 1 or parsed["action"] != request["action"] or
                parsed["request_id"] != request["request_id"] or
                parsed["domain_id"] != self._domain or
                parsed["campaign_id"] != self._campaign or
                parsed["status"] not in {"ok", "rejected", "recovery_required"} or
                parsed["detail"] != "" or not isinstance(parsed["state"], dict)):
            raise AdapterError("ADAPTER_CAMPAIGN_RESPONSE_INVALID")
        return request_raw, response_raw, parsed

    def _gateway_exchange(
            self, call: dict[str, Any], arguments: dict[str, Any]
    ) -> tuple[bytes, bytes, dict[str, Any]]:
        _stable_image(self._images["native-tool-client"])
        argv = [
            str(HEPTACTL), "--socket", str(self._tool_socket),
            "--token-file", self._owner["token_path"],
            "--call-id", call["tool_call_id"], "--protocol-min", "1",
            "--protocol-max", "1", "--schema-hash",
            call["tool_descriptor_sha256"], "--io-timeout-ms", "16000",
            "call", call["tool_name"],
        ]
        for key, value in self._gateway_arguments(call["call_role"], arguments):
            argv.append(f"{key}={value}")
        logical_argv = list(argv)
        logical_argv[2] = "<fixed-socket>"
        logical_argv[4] = "<pinned-token-file>"
        request_raw = _canonical({
            "schema": "hepta.p1-paper-canary-native-invocation.v1",
            "argv": logical_argv,
            "tool_call_id": call["tool_call_id"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
        })
        try:
            completed = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
                close_fds=True, check=False, timeout=TOOL_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdapterError("ADAPTER_TOOL_EXCHANGE_UNCERTAIN") from error
        if len(completed.stdout) > MAX_BYTES or len(completed.stderr) > 65536:
            raise AdapterError("ADAPTER_TOOL_RESPONSE_SIZE_INVALID")
        parsed = _strict_json(
            completed.stdout, "ADAPTER_TOOL_RESPONSE", decimals=True)
        if (
                set(parsed) != {
                    "status", "tool", "reason_code", "detail", "order_id", "payload"} or
                parsed["tool"] != call["tool_name"] or
                parsed["status"] not in {
                    "ok", "permission_denied", "invalid_tool", "rejected",
                    "duplicate", "uncertain", "error"} or
                not isinstance(parsed["reason_code"], str) or
                not isinstance(parsed["detail"], str) or
                (parsed["payload"] is not None and
                 not isinstance(parsed["payload"], dict))):
            raise AdapterError("ADAPTER_TOOL_RESPONSE_INVALID")
        expected_exit = {
            "ok": 0, "permission_denied": 3, "invalid_tool": 5,
            "rejected": 6, "duplicate": 7, "uncertain": 8, "error": 9,
        }[parsed["status"]]
        if completed.returncode != expected_exit:
            raise AdapterError("ADAPTER_TOOL_EXIT_STATUS_MISMATCH")
        return request_raw, completed.stdout, parsed

    def _gateway_arguments(
            self, role: str, arguments: dict[str, Any]
    ) -> list[tuple[str, str]]:
        if role == "preflight-quote":
            return [("instrument", "EUR.USD")]
        if role in {"preview-order", "place"}:
            intent = self._handoff.document["intent"]
            result = [
                ("instrument", "EUR.USD"), ("symbol", "EUR"),
                ("currency", "USD"), ("sec_type", "CASH"),
                ("exchange", "IDEALPRO"), ("side", intent["side"]),
                ("quantity", "1"), ("order_type", "LMT"),
                ("tif", "DAY"), ("limit_price", intent["limit_price"]),
                ("reference_price", intent["limit_price"]),
                ("expires_at_ms", str(intent["expires_at_ms"])),
            ]
            if role == "place":
                if self._preview_permit is None:
                    raise AdapterError("ADAPTER_PLACE_PREVIEW_PERMIT_MISSING")
                result.append(("preview_permit", self._preview_permit))
            return result
        if role == "cancel-order":
            digest = arguments.get("order_id_sha256")
            raw_id = self._raw_order_ids.get(digest)
            if raw_id is None:
                raise AdapterError("ADAPTER_CANCEL_ORDER_BINDING_UNKNOWN")
            return [("order_id", str(raw_id))]
        if role in {"preview-flatten", "flatten-position"}:
            result = [
                ("instrument", "EUR.USD"), ("symbol", "EUR"),
                ("currency", "USD"), ("sec_type", "CASH"),
                ("exchange", "IDEALPRO"),
            ]
            if role == "flatten-position":
                if self._flatten_permit is None:
                    raise AdapterError("ADAPTER_FLATTEN_PREVIEW_PERMIT_MISSING")
                result.append(("preview_permit", self._flatten_permit))
            return result
        return []

    def invoke(self, tool_name: str, tool_call_id: str, request: bytes) -> bytes:
        core_request = _strict_json(request, "ADAPTER_CORE_REQUEST")
        call = self._calls.get(tool_call_id)
        if (
                call is None or tool_name != call["tool_name"] or
                core_request.get("tool_call_id") != tool_call_id or
                core_request.get("tool_name") != tool_name or
                core_request.get("tool_descriptor_sha256") !=
                    call["tool_descriptor_sha256"] or
                core_request.get("tool_catalog_sha256") !=
                    self._handoff.document["tool_catalog_sha256"] or
                core_request.get("paper_only") is not True or
                core_request.get("live_authorized") is not False or
                core_request.get("direct_broker_access") is not False or
                core_request.get("authority_granted") is not False or
                not isinstance(core_request.get("arguments"), dict)):
            raise AdapterError("ADAPTER_CORE_REQUEST_BINDING_INVALID")
        try:
            if tool_name.startswith("campaign."):
                raw_request, raw_response, envelope = self._invoke_campaign(
                    call, core_request["arguments"])
            else:
                raw_request, raw_response, envelope = self._gateway_exchange(
                    call, core_request["arguments"])
            status = self._status(envelope["status"])
            reason = envelope["reason_code"] or ("OK" if status == "OK" else
                                                   "ADAPTER_UPSTREAM_REJECTED")
            payload = (
                self._normalize(call, core_request["arguments"], envelope)
                if status == "OK" else {})
        except AdapterError as error:
            raw_request = request
            raw_response = _canonical({"reason_code": error.code})
            status = "UNCERTAIN"
            reason = error.code if REASON.fullmatch(error.code) else \
                "ADAPTER_CALL_UNCERTAIN"
            payload = {}
        response = {
            "schema": BACKEND_RESPONSE_SCHEMA, "version": 1,
            "tool_call_id": tool_call_id, "tool_name": tool_name,
            "command_id": call["command_id"],
            "tool_catalog_sha256": self._handoff.document[
                "tool_catalog_sha256"],
            "tool_descriptor_sha256": call["tool_descriptor_sha256"],
            "status": status, "reason_code": reason,
            "service_epoch": self._epoch,
            "fencing_generation": self._fence,
            "adapter_image_sha256": self._adapter_sha,
            "adapter_transform_version": ADAPTER_TRANSFORM_VERSION,
            "raw_request_sha256": _sha(raw_request),
            "raw_response_sha256": _sha(raw_response),
            "normalized_payload_sha256": self._executor.canonical_sha256(payload),
            "payload": payload,
        }
        return self._executor.canonical_json(response)

    def _invoke_campaign(
            self, call: dict[str, Any], arguments: dict[str, Any]
    ) -> tuple[bytes, bytes, dict[str, Any]]:
        action = call["tool_name"].split(".", 1)[1]
        request: dict[str, Any] = {
            "schema": CAMPAIGN_REQUEST_SCHEMA, "version": 1,
            "action": action, "request_id": call["tool_call_id"],
            "domain_id": self._domain, "campaign_id": self._campaign,
        }
        if action == "open_cycle":
            request.update({
                "cycle_id": self._cycle,
                "intent": self._handoff.document["intent"],
                "intent_sha256": self._handoff.document["intent_sha256"],
                "preflight_sha256": arguments["preflight_sha256"],
            })
        elif action == "close_cycle":
            outcome = arguments.get("outcome")
            if outcome not in {
                    "PREVIEW_REJECTED", "PLACE_REJECTED", "PLACE_ACCEPTED",
                    "PLACE_UNCERTAIN", "OPERATOR_ABORT"}:
                raise AdapterError("ADAPTER_CAMPAIGN_OUTCOME_INVALID")
            request.update({
                "cycle_id": self._cycle,
                "intent_sha256": self._handoff.document["intent_sha256"],
                "outcome": outcome,
            })
        elif action != "status":
            raise AdapterError("ADAPTER_CAMPAIGN_ACTION_INVALID")
        raw_request, raw_response, response = self._campaign_exchange(request)
        response["status"] = (
            "ok" if response["status"] == "ok" else
            "uncertain" if response["status"] == "recovery_required" else
            "rejected")
        response["payload"] = response["state"]
        return raw_request, raw_response, response

    @staticmethod
    def _status(value: str) -> str:
        return {
            "ok": "OK", "permission_denied": "PERMISSION_DENIED",
            "invalid_tool": "INVALID_TOOL", "rejected": "REJECTED",
            "duplicate": "DUPLICATE", "uncertain": "UNCERTAIN",
            "error": "ERROR",
        }[value]

    def _normalize(
            self, call: dict[str, Any], arguments: dict[str, Any],
            envelope: dict[str, Any]) -> dict[str, Any]:
        role = call["call_role"]
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise AdapterError("ADAPTER_TOOL_PAYLOAD_INVALID")
        if role in {"preflight-health", "final-health"}:
            required = {
                "gateway_ready", "remote_execution", "remote_execution_configured",
                "remote_execution_ready", "execution_mode", "execution_service_epoch",
                "execution_service_fencing_generation", "paper_template_enabled",
                "authorized_connector_count"}
            connector_count = _integer(
                payload.get("authorized_connector_count"),
                "ADAPTER_HEALTH_IDENTITY_INVALID", minimum=0)
            if (
                    not required.issubset(payload) or
                    payload["gateway_ready"] is not True or
                    payload["remote_execution"] is not True or
                    payload["remote_execution_configured"] is not True or
                    payload["remote_execution_ready"] is not True or
                    payload["execution_mode"] != "PAPER" or
                    payload["paper_template_enabled"] is not True or
                    connector_count != 1 or
                    payload["execution_service_epoch"] != self._epoch or
                    payload["execution_service_fencing_generation"] != self._fence):
                raise AdapterError("ADAPTER_HEALTH_IDENTITY_INVALID")
            return {
                "execution_mode": "PAPER", "paper_account": True,
                "connected": True, "authorized_connector_count": 1,
                "complete": True,
            }
        if role == "preflight-quote":
            expected_fields = {
                "source", "authoritative", "instrument", "subscription_id",
                "subscription_state", "observed_at_ms", "stale_after_ms",
                "stale", "bid", "ask",
            }
            if (
                    set(payload) != expected_fields or
                    payload.get("source") != "IB" or
                    payload.get("authoritative") is not True or
                    payload.get("instrument") != "EUR.USD" or
                    payload.get("subscription_state") != "active" or
                    payload.get("stale") is not False):
                raise AdapterError("ADAPTER_QUOTE_INVALID")
            subscription = payload.get("subscription_id")
            if (
                    not isinstance(subscription, str) or
                    re.fullmatch(r"IB:[1-9][0-9]*:[1-9][0-9]*:[1-9][0-9]*",
                                 subscription) is None):
                raise AdapterError("ADAPTER_QUOTE_SUBSCRIPTION_INVALID")
            _prefix, connection_epoch, _generation, _request = \
                subscription.split(":")
            observed_at_ms = _integer(
                payload.get("observed_at_ms"), "ADAPTER_QUOTE_INVALID",
                minimum=1)
            stale_after_ms = _integer(
                payload.get("stale_after_ms"), "ADAPTER_QUOTE_INVALID",
                minimum=1)
            now_ms = self.now_ms()
            if (
                    observed_at_ms > now_ms + 1_000 or
                    now_ms - observed_at_ms > self._quote_max_age_ms or
                    stale_after_ms <= now_ms or
                    stale_after_ms - observed_at_ms > self._quote_max_age_ms):
                raise AdapterError("ADAPTER_QUOTE_STALE")
            bid = _decimal_text(payload.get("bid"), "ADAPTER_QUOTE_INVALID")
            ask = _decimal_text(payload.get("ask"), "ADAPTER_QUOTE_INVALID")
            if Decimal(bid) > Decimal(ask):
                raise AdapterError("ADAPTER_QUOTE_INVALID")
            self._quote_subscription_id = subscription
            self._ib_connection_epoch = int(connection_epoch)
            return {
                "instrument": "EUR.USD", "symbol": "EUR", "currency": "USD",
                "sec_type": "CASH", "exchange": "IDEALPRO",
                "bid": bid, "ask": ask,
                "observed_at_ms": observed_at_ms,
                "authoritative": True, "complete": True,
            }
        if role in {"preflight-account", "final-account"}:
            exposures = payload.get("fx_cash_exposures")
            if (
                    set(payload) != {
                        "source", "authoritative", "account_complete",
                        "fx_cash_complete", "fx_cash_generation",
                        "reason_code", "position_scope", "fx_cash_exposures"} or
                    payload.get("source") != "IB" or
                    payload.get("authoritative") is not True or
                    payload.get("account_complete") is not True or
                    payload.get("fx_cash_complete") is not True or
                    payload.get("reason_code") != "" or
                    payload.get("position_scope") != "PAPER_BASELINE_DELTA" or
                    not isinstance(exposures, list)):
                raise AdapterError("ADAPTER_ACCOUNT_INVALID")
            fx_generation = _integer(
                payload.get("fx_cash_generation"),
                "ADAPTER_ACCOUNT_INVALID", minimum=1)
            self._last_fx_cash_generation = fx_generation
            gross = 0
            for exposure in exposures:
                if not isinstance(exposure, dict) or set(exposure) != {
                        "instrument", "base_currency", "quote_currency",
                        "current_cash_balance", "baseline_cash_balance",
                        "campaign_owned_quantity"}:
                    raise AdapterError("ADAPTER_ACCOUNT_INVALID")
                quantity = _quantity(
                    exposure.get("campaign_owned_quantity"),
                    "ADAPTER_ACCOUNT_INVALID", allow_zero=True)
                if (exposure.get("instrument") != "EUR.USD" and quantity != 0):
                    raise AdapterError("ADAPTER_ACCOUNT_SCOPE_INVALID")
                gross += abs(quantity)
            if gross > 1:
                raise AdapterError("ADAPTER_ACCOUNT_SCOPE_INVALID")
            return {
                "account_id_sha256": _sha(
                    self._owner_account.encode("ascii")),
                "account_kind": "PAPER", "authoritative": True,
                "account_complete": True, "gross_absolute_position": gross,
                "fx_cash_generation": fx_generation,
                "owner_account": self._owner_account,
                "owner_execution_domain": self._owner_execution_domain,
            }
        if role in {
                "preflight-positions", "reconcile-positions", "final-positions"}:
            values = payload.get("positions")
            if (
                    set(payload) != {
                        "source", "authoritative", "position_generation",
                        "fx_cash_generation", "reason_code", "position_scope",
                        "positions"} or
                    payload.get("source") != "IB" or
                    payload.get("authoritative") is not True or
                    payload.get("reason_code") != "" or
                    payload.get("position_scope") != "PAPER_BASELINE_DELTA" or
                    not isinstance(values, list)):
                raise AdapterError("ADAPTER_POSITIONS_INVALID")
            position_generation = _integer(
                payload.get("position_generation"),
                "ADAPTER_POSITIONS_INVALID", minimum=1)
            fx_generation = _integer(
                payload.get("fx_cash_generation"),
                "ADAPTER_POSITIONS_INVALID", minimum=1)
            positions: list[dict[str, Any]] = []
            for value in values:
                if not isinstance(value, dict):
                    raise AdapterError("ADAPTER_POSITIONS_INVALID")
                if set(value) != {"instrument", "quantity"}:
                    raise AdapterError("ADAPTER_POSITIONS_INVALID")
                quantity = _quantity(
                    value.get("quantity"), "ADAPTER_POSITIONS_INVALID",
                    allow_zero=True)
                if value.get("instrument") != "EUR.USD" and quantity != 0:
                    raise AdapterError("ADAPTER_POSITION_SCOPE_INVALID")
                if value.get("instrument") == "EUR.USD" and quantity != 0:
                    positions.append({"instrument": "EUR.USD", "quantity": quantity})
            if len(positions) > 1:
                raise AdapterError("ADAPTER_POSITION_SCOPE_INVALID")
            self._last_position_generation = position_generation
            self._last_fx_cash_generation = fx_generation
            return {
                "authoritative": True, "complete": True,
                "snapshot_sha256": self._payload_digest(payload),
                "positions": positions,
                "gross_absolute_position": sum(
                    abs(item["quantity"]) for item in positions),
                "position_generation": position_generation,
                "fx_cash_generation": fx_generation,
                "owner_account": self._owner_account,
                "owner_execution_domain": self._owner_execution_domain,
            }
        if role in {
                "preflight-orders", "reconcile-orders", "final-orders"}:
            active = payload.get("active_order_ids")
            owned = payload.get("owned_active_order_ids")
            unmapped = payload.get("unmapped_active_order_ids")
            if (
                    set(payload) != {
                        "source", "authoritative", "active_orders_source",
                        "active_orders_connection_epoch",
                        "active_orders_generation",
                        "global_active_orders_complete",
                        "owner_projection_source",
                        "owner_projection_connection_epoch",
                        "owner_projection_generation",
                        "owner_projection_complete",
                        "owned_active_order_ids_authoritative", "owner_scope",
                        "reason_code", "active_order_ids",
                        "owned_active_order_ids", "unmapped_active_order_ids",
                        "recent_orders"} or
                    payload.get("source") != "IB" or
                    payload.get("authoritative") is not True or
                    payload.get("global_active_orders_complete") is not True or
                    payload.get("owner_projection_complete") is not True or
                    payload.get("owned_active_order_ids_authoritative") is not True or
                    not isinstance(active, list) or not isinstance(owned, list) or
                    payload.get("active_orders_source") != "IB_OPEN_ORDERS" or
                    payload.get("owner_projection_source") !=
                        "EXECUTION_COORDINATOR_ORDER_OWNERS" or
                    payload.get("reason_code") != "" or
                    unmapped != [] or active != owned or len(owned) > 1):
                raise AdapterError("ADAPTER_ORDER_OWNER_SCOPE_INVALID")
            connection_epoch = _integer(
                payload.get("active_orders_connection_epoch"),
                "ADAPTER_ORDER_OWNER_SCOPE_INVALID", minimum=1)
            generation = _integer(
                payload.get("active_orders_generation"),
                "ADAPTER_ORDER_OWNER_SCOPE_INVALID", minimum=1)
            if (
                    payload.get("owner_projection_connection_epoch") !=
                        connection_epoch or
                    payload.get("owner_projection_generation") != generation or
                    payload.get("owner_scope") != {
                        "agent_id": "hepta-agent-alpha",
                        "session_id": self._owner["session_id"],
                        "execution_domain": self._owner_execution_domain,
                        "account": self._owner_account} or
                    (self._ib_connection_epoch is not None and
                     connection_epoch != self._ib_connection_epoch)):
                raise AdapterError("ADAPTER_ORDER_OWNER_SCOPE_INVALID")
            orders = []
            for value in owned:
                raw_id = _integer(value, "ADAPTER_ORDER_ID_INVALID", minimum=1)
                digest = _sha(str(raw_id).encode("ascii"))
                self._raw_order_ids[digest] = raw_id
                orders.append({
                    "order_id_sha256": digest, "instrument": "EUR.USD",
                    "owned": True, "active": True,
                })
            self._last_order_generation = generation
            return {
                "authoritative": True, "complete": True,
                "snapshot_sha256": self._payload_digest(payload), "orders": orders,
                "connection_epoch": connection_epoch,
                "generation": generation,
                "owner_account": self._owner_account,
                "owner_execution_domain": self._owner_execution_domain,
            }
        if role in {"preflight-risk", "cleanup-risk"}:
            expected_fields = {
                "source", "authoritative", "max_order_quantity",
                "max_order_notional", "max_orders_per_minute",
                "max_active_orders", "max_gross_position",
                "gross_absolute_position", "reason_code", "gross_scope"}
            if (
                    set(payload) != expected_fields or
                    payload.get("source") != "IB" or
                    payload.get("authoritative") is not True or
                    payload.get("reason_code") != "" or
                    payload.get("gross_scope") != "PAPER_BASELINE_DELTA"):
                raise AdapterError("ADAPTER_RISK_INVALID")
            quantity = _canonical_nonnegative_decimal(
                payload.get("max_order_quantity"), "ADAPTER_RISK_INVALID",
                expected="1")
            notional = _canonical_nonnegative_decimal(
                payload.get("max_order_notional"), "ADAPTER_RISK_INVALID",
                expected="5000")
            maximum_gross = _canonical_nonnegative_decimal(
                payload.get("max_gross_position"), "ADAPTER_RISK_INVALID",
                expected="1")
            current_gross = _canonical_nonnegative_decimal(
                payload.get("gross_absolute_position"),
                "ADAPTER_RISK_INVALID", expected="0")
            if (
                    _integer(payload.get("max_orders_per_minute"),
                             "ADAPTER_RISK_INVALID", minimum=1) != 1 or
                    _integer(payload.get("max_active_orders"),
                             "ADAPTER_RISK_INVALID", minimum=1) != 1):
                raise AdapterError("ADAPTER_RISK_INVALID")
            if any(value is None for value in (
                    self._ib_connection_epoch, self._last_order_generation,
                    self._last_position_generation,
                    self._last_fx_cash_generation)):
                raise AdapterError("ADAPTER_RISK_SNAPSHOT_UNBOUND")
            return {
                "paper_only": True, "live_authorized": False,
                "max_order_quantity": quantity,
                "max_order_notional": notional,
                "max_orders_per_minute": 1, "max_active_orders": 1,
                "max_gross_position": maximum_gross,
                "gross_absolute_position": current_gross,
                "gross_scope": "PAPER_BASELINE_DELTA",
                "connection_epoch": self._ib_connection_epoch,
                "orders_generation": self._last_order_generation,
                "position_generation": self._last_position_generation,
                "fx_cash_generation": self._last_fx_cash_generation,
                "owner_account": self._owner_account,
                "owner_execution_domain": self._owner_execution_domain,
                "allowed_instruments": ["EUR.USD"],
                "order_types": ["LMT"], "tifs": ["DAY"], "complete": True,
            }
        if role == "preflight-campaign":
            if (
                    payload.get("status") != "idle" or
                    payload.get("cycles_opened") != 0 or
                    payload.get("cycles_closed") != 0 or
                    payload.get("active_cycle_id") is not None):
                raise AdapterError("ADAPTER_CAMPAIGN_NOT_IDLE")
            return {
                "state": "IDLE", "cycle_id": None, "remaining_cycles": 1,
                "authority_granted": False,
            }
        if role == "open":
            if (
                    payload.get("status") != "open" or
                    payload.get("active_cycle_id") != self._cycle):
                raise AdapterError("ADAPTER_CAMPAIGN_OPEN_INVALID")
            return {
                "opened": True, "cycle_id": self._cycle,
                "intent_sha256": self._handoff.document["intent_sha256"],
                "deadline_at_ms": _integer(
                    payload.get("active_deadline_at_ms"),
                    "ADAPTER_CAMPAIGN_OPEN_INVALID"),
                "authority_granted": False,
            }
        if role == "preview-order":
            permit, command_id, _permit_expiry, preview = \
                self._preview_identity(payload, flatten=False)
            if command_id != self._call_roles["place"]["tool_call_id"]:
                raise AdapterError("ADAPTER_PLACE_COMMAND_ID_NOT_PREBOUND")
            if set(preview) != ORDER_AUTHORITATIVE_PREVIEW_FIELDS:
                raise AdapterError("ADAPTER_ORDER_PREVIEW_FIELDS_INVALID")
            intent = self._handoff.document["intent"]
            bid = _decimal_text(
                preview.get("quote_bid"), "ADAPTER_ORDER_PREVIEW_INVALID")
            ask = _decimal_text(
                preview.get("quote_ask"), "ADAPTER_ORDER_PREVIEW_INVALID")
            limit_price = _decimal_text(
                preview.get("limit_price"), "ADAPTER_ORDER_PREVIEW_INVALID")
            reference_price = _decimal_text(
                preview.get("reference_price"),
                "ADAPTER_ORDER_PREVIEW_INVALID")
            observed_at_ms = _integer(
                preview.get("observed_at_ms"),
                "ADAPTER_ORDER_PREVIEW_INVALID", minimum=1)
            stale_after_ms = _integer(
                preview.get("stale_after_ms"),
                "ADAPTER_ORDER_PREVIEW_INVALID", minimum=1)
            now_ms = self.now_ms()
            expected_price = ask if intent["side"] == "BUY" else bid
            if (
                    preview.get("source") != "IB" or
                    preview.get("authoritative") is not True or
                    preview.get("subscription_id") !=
                        self._quote_subscription_id or
                    preview.get("stale") is not False or
                    preview.get("order_type") != "LMT" or
                    preview.get("tif") != "DAY" or
                    preview.get("risk_approved") is not True or
                    Decimal(bid) > Decimal(ask) or
                    bid != intent["observed_bid"] or
                    ask != intent["observed_ask"] or
                    observed_at_ms != intent["observed_at_ms"] or
                    observed_at_ms > now_ms + 1_000 or
                    now_ms - observed_at_ms > self._quote_max_age_ms or
                    stale_after_ms <= now_ms or
                    stale_after_ms - observed_at_ms >
                        self._quote_max_age_ms or
                    limit_price != intent["limit_price"] or
                    reference_price != intent["limit_price"] or
                    limit_price != expected_price):
                raise AdapterError("ADAPTER_ORDER_PREVIEW_INVALID")
            self._preview_permit = permit
            return {
                "approved": True, "cycle_id": self._cycle,
                "intent_sha256": self._handoff.document["intent_sha256"],
                "order_request_sha256": self._executor.canonical_sha256(
                    self._handoff.document["intent"]),
                "authority_granted": False,
            }
        if role == "place":
            raw_id = _integer(
                envelope.get("order_id"), "ADAPTER_PLACE_ORDER_ID_INVALID",
                minimum=1)
            digest = _sha(str(raw_id).encode("ascii"))
            self._raw_order_ids[digest] = raw_id
            self._preview_permit = None
            return {
                "accepted": True, "cycle_id": self._cycle,
                "intent_sha256": self._handoff.document["intent_sha256"],
                "order_id_sha256": digest, "owned": True,
                "authority_granted": False,
            }
        if role == "close":
            if (
                    payload.get("status") not in {"idle", "halted"} or
                    payload.get("active_cycle_id") is not None or
                    payload.get("last_outcome") != arguments.get("outcome")):
                raise AdapterError("ADAPTER_CAMPAIGN_CLOSE_INVALID")
            return {
                "closed": True, "cycle_id": self._cycle,
                "intent_sha256": self._handoff.document["intent_sha256"],
                "outcome": arguments["outcome"],
                "authority_granted": False,
            }
        if role == "cancel-order":
            digest = arguments["order_id_sha256"]
            if digest not in self._raw_order_ids:
                raise AdapterError("ADAPTER_CANCEL_ORDER_BINDING_UNKNOWN")
            return {
                "cancelled": True, "order_id_sha256": digest,
                "stable_cancel": True, "authority_granted": False,
            }
        if role == "preview-flatten":
            permit, command_id, permit_expiry, preview = \
                self._preview_identity(payload, flatten=True)
            if command_id != self._call_roles["flatten-position"]["tool_call_id"]:
                raise AdapterError("ADAPTER_FLATTEN_COMMAND_ID_NOT_PREBOUND")
            if set(preview) != FLATTEN_AUTHORITATIVE_PREVIEW_FIELDS:
                raise AdapterError("ADAPTER_FLATTEN_PREVIEW_FIELDS_INVALID")
            if (
                    preview.get("source") != "IB" or
                    preview.get("authoritative") is not True or
                    preview.get("reduce_only") is not True or
                    preview.get("atomic") is not True or
                    preview.get("risk_approved") is not True or
                    preview.get("order_type") != "LMT" or
                    preview.get("tif") != "DAY"):
                raise AdapterError("ADAPTER_FLATTEN_PREVIEW_INVALID")
            side = arguments["side"]
            bid = _decimal_text(
                preview.get("quote_bid"), "ADAPTER_FLATTEN_QUOTE_INVALID")
            ask = _decimal_text(
                preview.get("quote_ask"), "ADAPTER_FLATTEN_QUOTE_INVALID")
            limit_price = _decimal_text(
                preview.get("limit_price"), "ADAPTER_FLATTEN_QUOTE_INVALID")
            reference_price = _decimal_text(
                preview.get("reference_price"),
                "ADAPTER_FLATTEN_QUOTE_INVALID")
            position_quantity = _quantity(
                preview.get("position_quantity"),
                "ADAPTER_FLATTEN_PREVIEW_INVALID")
            quantity = _quantity(
                preview.get("quantity"), "ADAPTER_FLATTEN_PREVIEW_INVALID")
            connection_epoch = _integer(
                preview.get("position_connection_epoch"),
                "ADAPTER_FLATTEN_PREVIEW_INVALID", minimum=1)
            position_generation = _integer(
                preview.get("position_generation"),
                "ADAPTER_FLATTEN_PREVIEW_INVALID", minimum=1)
            observed_at_ms = _integer(
                preview.get("quote_observed_at_ms"),
                "ADAPTER_FLATTEN_QUOTE_INVALID", minimum=1)
            now_ms = self.now_ms()
            if (
                    Decimal(bid) > Decimal(ask) or
                    limit_price != (bid if side == "SELL" else ask) or
                    reference_price != limit_price or
                    position_quantity != arguments["position_quantity"] or
                    preview.get("side") != side or quantity != 1 or
                    preview.get("quote_subscription_id") !=
                        self._quote_subscription_id or
                    connection_epoch != self._ib_connection_epoch or
                    position_generation != self._last_position_generation or
                    observed_at_ms > now_ms + 1_000 or
                    now_ms - observed_at_ms > self._quote_max_age_ms):
                raise AdapterError("ADAPTER_FLATTEN_QUOTE_INVALID")
            self._flatten_permit = permit
            return {
                "approved": True, "instrument": "EUR.USD",
                "position_quantity": arguments["position_quantity"],
                "side": side, "quantity": 1, "order_type": "LMT", "tif": "DAY",
                "limit_price": limit_price, "observed_bid": bid,
                "observed_ask": ask,
                "quote_observed_at_ms": observed_at_ms,
                "expires_at_ms": permit_expiry,
                "reduce_only": True, "atomic": True,
                "authority_granted": False,
            }
        if role == "flatten-position":
            self._flatten_permit = None
            return {
                "flattened": True, "instrument": "EUR.USD",
                "position_quantity": arguments["position_quantity"],
                "side": arguments["side"], "quantity": 1,
                "order_type": "LMT", "tif": "DAY",
                "limit_price": arguments["limit_price"],
                "quote_observed_at_ms": arguments["quote_observed_at_ms"],
                "expires_at_ms": arguments["expires_at_ms"],
                "reduce_only": True, "atomic": True,
                "authority_granted": False,
            }
        raise AdapterError("ADAPTER_ROLE_NORMALIZATION_MISSING")

    def _preview_identity(
            self, payload: dict[str, Any], *, flatten: bool
    ) -> tuple[str, str, int, dict[str, Any]]:
        if (
                set(payload) != PREVIEW_ENVELOPE_FIELDS or
                payload.get("approved") is not True or
                payload.get("single_use") is not True or
                payload.get("service_epoch") != self._epoch or
                payload.get("service_fencing_generation") != self._fence or
                not isinstance(payload.get("preview_permit"), str) or
                not payload["preview_permit"] or
                not isinstance(payload.get("command_id"), str) or
                not isinstance(payload.get("authoritative_preview"), dict)):
            raise AdapterError("ADAPTER_PREVIEW_IDENTITY_INVALID")
        permit_expiry = _integer(
            payload.get("permit_expires_at_ms"),
            "ADAPTER_PREVIEW_IDENTITY_INVALID", minimum=1)
        now_ms = self.now_ms()
        horizon = (
            self._handoff.document["expires_at_ms"] if flatten else
            self._handoff.document["intent"]["expires_at_ms"])
        if not now_ms < permit_expiry <= horizon:
            raise AdapterError("ADAPTER_PREVIEW_PERMIT_EXPIRY_INVALID")
        return (
            payload["preview_permit"], payload["command_id"], permit_expiry,
            payload["authoritative_preview"])

    def _payload_digest(self, payload: dict[str, Any]) -> str:
        # Decimal-bearing raw envelopes are deterministically projected before
        # hashing; no binary broker identifier is exposed to the core.
        return _sha(_canonical(_decimal_projection(payload)))


def _decimal_projection(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value, "ADAPTER_DECIMAL_INVALID")
    if isinstance(value, dict):
        return {key: _decimal_projection(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decimal_projection(item) for item in value]
    return value


def create_hepta_p1_paper_canary_backend(*, executor_module: Any, handoff: Any):
    """Exact fixed factory used by the executor; no ambient configuration."""

    return ProductionBackend(executor_module, handoff)
