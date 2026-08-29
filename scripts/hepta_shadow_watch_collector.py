#!/usr/bin/python3

"""Identity-bound, read-only WATCH snapshot collector."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

from hepta_agent_trust_domain import load_runtime_config


HEPTACTL = "/usr/bin/heptactl"
MAX_COMMAND_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024
PROCESS_ATTEMPTS = 3
READ_SET_ATTEMPTS = 2
READ_SET_DEADLINE_SECONDS = 8.5
READ_SET_IO_TIMEOUT_MS = 5000
PROCESS_RETRY_SECONDS = 0.05
QUOTE_RETRY_SECONDS = 1.0
IDENTITY_RETRY_SECONDS = 0.1
EVENT_READY_RETRY_SECONDS = 0.25
EVENT_TRANSITION_CODES = frozenset({
    "EXECUTION_EVENT_SERVICE_NOT_READY",
    "EXECUTION_EVENT_CONNECT_FAILED",
    "EXECUTION_EVENT_CONNECT_TIMEOUT",
    "EXECUTION_EVENT_RESPONSE_READ_FAILED",
})
TRANSPORT_PROCESS_CODES = {
    "SOCKET_CONNECT_FAILED": "WATCH_TOOL_SOCKET_CONNECT_FAILED",
    "FRAME_WRITE_TIMEOUT": "WATCH_TOOL_FRAME_WRITE_TIMEOUT",
    "FRAME_HEADER_TIMEOUT": "WATCH_TOOL_FRAME_HEADER_TIMEOUT",
    "FRAME_BODY_TIMEOUT": "WATCH_TOOL_FRAME_BODY_TIMEOUT",
}
TERMINAL_SESSION_CODES = {
    "SESSION_NOT_FOUND": "WATCH_SESSION_AUTHORITY_NOT_FOUND",
    "SESSION_REVOKED": "WATCH_SESSION_AUTHORITY_REVOKED",
    "SESSION_EXPIRED": "WATCH_SESSION_AUTHORITY_EXPIRED",
    "SESSION_ALREADY_EXPIRED": "WATCH_SESSION_AUTHORITY_EXPIRED",
    "SESSION_DISABLED": "WATCH_SESSION_AUTHORITY_DISABLED",
    "SESSION_LEASE_GENERATION_CHANGED": "WATCH_SESSION_GENERATION_CHANGED",
    "SESSION_OWNER_FENCED": "WATCH_SESSION_OWNER_FENCED",
    "SESSION_OWNER_FENCE_PENDING": "WATCH_SESSION_FENCE_PENDING",
    "SESSION_REMOTE_FENCE_PENDING": "WATCH_SESSION_FENCE_PENDING",
}
REQUIRED_TOOLS = (
    "system.get_health",
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
)
WATCH_SNAPSHOT_TOOL = "watch.get_snapshot"
READ_ORDER = (
    "account.get_summary",
    "portfolio.list_positions",
    "orders.list",
    "risk.get_limits",
    "market.get_quote",
    "system.get_health",
)
FORBIDDEN_TOOLS = frozenset({
    "risk.preview_order", "risk.preview_flatten", "trade.place_order",
    "trade.cancel_order", "trade.flatten_position",
})
SAFE_FIELDS = frozenset({
    "source", "authoritative", "connected", "complete", "stale",
    "gateway_ready", "remote_execution", "remote_execution_configured",
    "remote_execution_ready", "execution_mode", "execution_service_epoch",
    "execution_service_fencing_generation", "remote_execution_reason",
    "read_model", "paper_template_enabled",
    "instrument", "bid", "ask", "observed_at_ms", "stale_after_ms",
    "subscription_state", "account_complete", "positions", "quantity",
    "active_order_ids", "max_order_quantity", "max_order_notional",
    "max_orders_per_minute", "max_active_orders", "max_gross_position",
    "gross_absolute_position", "snapshot_version", "catalog",
    "revision", "sessions", "tool_server", "pending", "active",
    "ready_owners", "queue_backpressure_rejections",
    "owner_backpressure_rejections", "execution_gateway",
    "remote_enabled", "mode", "recovery", "connection_epoch",
    "generation", "domains", "name", "required", "in_flight",
    "retry_scheduled", "exhausted", "active_generation",
    "next_retry_at_ms", "dispatch_attempts", "consecutive_failures",
    "quotes", "desired_revision", "primary", "contracts",
    "session_references", "request_id", "dispatch_accepted", "has_bid",
    "has_ask", "freshness",
})


class CollectorError(RuntimeError):
    pass


def _transport_process_reason(stderr: str) -> str:
    # heptactl emits one fixed reason code followed by one newline. Preserve
    # only an allowlisted, non-secret classification; never surface arbitrary
    # child-process output in campaign evidence.
    if stderr.endswith("\n") and "\n" not in stderr[:-1] and "\r" not in stderr:
        return TRANSPORT_PROCESS_CODES.get(
            stderr[:-1], "WATCH_TOOL_PROCESS_REJECTED")
    return "WATCH_TOOL_PROCESS_REJECTED"


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CollectorError("WATCH_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _strict_json(value: str, reason: str) -> object:
    try:
        return json.loads(
            value, object_pairs_hook=_strict_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                CollectorError("WATCH_JSON_NON_FINITE")))
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise CollectorError(reason) from error


def _regular(path: Path, mode: int, uid: int, gid: int) -> None:
    metadata = os.lstat(path)
    if (not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or
            metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != mode or
            metadata.st_uid != uid or metadata.st_gid != gid):
        raise CollectorError("WATCH_FILE_METADATA_INVALID")


def _identity(config: dict[str, object]) -> None:
    if (os.geteuid() != config["agent_uid"] or
            os.getegid() != config["agent_gid"] or
            set(os.getgroups()) - {config["agent_gid"]}):
        raise CollectorError("WATCH_COLLECTOR_IDENTITY_INVALID")


def _response_document(stdout: str, expected_tool: str) -> dict[str, object]:
    if len(stdout.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise CollectorError("WATCH_TOOL_OUTPUT_TOO_LARGE")
    document = _strict_json(stdout, "WATCH_TOOL_OUTPUT_INVALID")
    if (not isinstance(document, dict) or set(document) != {
            "status", "tool", "reason_code", "detail", "order_id", "payload"} or
            document.get("tool") != expected_tool or
            not isinstance(document.get("status"), str) or
            not isinstance(document.get("reason_code"), str) or
            not isinstance(document.get("detail"), str) or
            type(document.get("order_id")) is not int or
            (document.get("payload") is not None and
             not isinstance(document.get("payload"), dict)) or
            (document.get("status") == "ok" and
             not isinstance(document.get("payload"), dict))):
        raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
    return document


def _envelope(stdout: str, expected_tool: str) -> dict[str, object]:
    document = _response_document(stdout, expected_tool)
    if (document["status"] != "ok" or document["reason_code"] != "" or
            document["detail"] != "" or document["order_id"] != -1):
        raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
    payload = document["payload"]
    if not isinstance(payload, dict):
        raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
    return payload


def _call(socket_path: str, token_path: Path, tool: str,
          arguments: tuple[str, ...] = ()) -> dict[str, object]:
    if tool in FORBIDDEN_TOOLS or tool not in REQUIRED_TOOLS + (
            "system.tools.list", "system.tools.describe"):
        raise CollectorError("WATCH_TOOL_FORBIDDEN")
    command = [
        HEPTACTL, "--socket", socket_path, "--token-file", str(token_path),
        "--io-timeout-ms", "5000",
    ]
    if tool == "system.tools.list":
        command += ["tools", "list"]
    elif tool == "system.tools.describe":
        command += ["tools", "describe", arguments[0]]
    else:
        command += ["call", tool, *arguments]
    last_error: BaseException | None = None
    for attempt in range(PROCESS_ATTEMPTS):
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="strict", timeout=8,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        except (OSError, subprocess.SubprocessError, UnicodeError) as error:
            last_error = error
        else:
            if not completed.stderr:
                document = _response_document(completed.stdout, tool)
                if (tool == "market.get_quote" and
                        document["reason_code"] == "AUTHORITATIVE_QUOTE_STALE"):
                    last_error = CollectorError("WATCH_QUOTE_STALE")
                elif document["reason_code"] in TERMINAL_SESSION_CODES:
                    # These codes are terminal authority outcomes, not malformed
                    # envelopes or transient tool failures. Preserve only a
                    # fixed, non-secret collector reason so campaign cleanup
                    # can distinguish restart/revoke/expiry fencing.
                    raise CollectorError(
                        TERMINAL_SESSION_CODES[document["reason_code"]])
                elif document["reason_code"] == (
                        "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH"):
                    last_error = CollectorError(
                        "WATCH_EXECUTION_IDENTITY_TRANSITION")
                elif document["reason_code"] in EVENT_TRANSITION_CODES:
                    last_error = CollectorError(
                        "WATCH_EXECUTION_EVENT_TRANSITION")
                elif completed.returncode == 0:
                    if (document["status"] != "ok" or
                            document["reason_code"] != "" or
                            document["detail"] != "" or
                            document["order_id"] != -1):
                        raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
                    payload = document["payload"]
                    if not isinstance(payload, dict):
                        raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
                    return payload
                else:
                    raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
            else:
                last_error = CollectorError("WATCH_TOOL_PROCESS_REJECTED")
        if attempt + 1 < PROCESS_ATTEMPTS:
            if (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_QUOTE_STALE"):
                delay = QUOTE_RETRY_SECONDS
            elif (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_EXECUTION_IDENTITY_TRANSITION"):
                delay = IDENTITY_RETRY_SECONDS
            elif (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_EXECUTION_EVENT_TRANSITION"):
                delay = EVENT_READY_RETRY_SECONDS
            else:
                delay = PROCESS_RETRY_SECONDS
            time.sleep(delay * (attempt + 1))
    if isinstance(last_error, CollectorError):
        if str(last_error) == "WATCH_EXECUTION_IDENTITY_TRANSITION":
            raise CollectorError("WATCH_EXECUTION_IDENTITY_MISMATCH")
        if str(last_error) == "WATCH_EXECUTION_EVENT_TRANSITION":
            raise CollectorError("WATCH_EXECUTION_EVENT_NOT_READY")
        raise last_error
    raise CollectorError("WATCH_TOOL_PROCESS_FAILED") from last_error


def _read_set(socket_path: str, token_path: Path,
              instrument: str) -> dict[str, object]:
    command = [
        HEPTACTL, "--socket", socket_path, "--token-file", str(token_path),
        "--io-timeout-ms", str(READ_SET_IO_TIMEOUT_MS),
        "watch", "snapshot", instrument,
    ]
    deadline = time.monotonic() + READ_SET_DEADLINE_SECONDS
    last_error: BaseException | None = None
    for attempt in range(READ_SET_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectorError("WATCH_READ_SET_TIMEOUT")
        try:
            completed = subprocess.run(
                command, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="strict",
                timeout=remaining, check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        except subprocess.TimeoutExpired as error:
            raise CollectorError("WATCH_READ_SET_TIMEOUT") from error
        except OSError as error:
            last_error = error
        except (subprocess.SubprocessError, UnicodeError) as error:
            raise CollectorError("WATCH_TOOL_PROCESS_FAILED") from error
        else:
            if completed.stderr:
                reason = "WATCH_TOOL_PROCESS_REJECTED"
                if completed.returncode == 4 and completed.stdout == "":
                    reason = _transport_process_reason(completed.stderr)
                raise CollectorError(reason)
            elif completed.returncode != 0:
                failure = _strict_json(
                    completed.stdout, "WATCH_TOOL_OUTPUT_INVALID")
                if (not isinstance(failure, dict) or
                        not isinstance(failure.get("tool"), str) or
                        failure.get("tool") not in REQUIRED_TOOLS + (
                            "system.tools.list", "system.tools.describe",
                            WATCH_SNAPSHOT_TOOL) or
                        not isinstance(failure.get("reason_code"), str)):
                    raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
                reason_code = failure["reason_code"]
                if reason_code in TERMINAL_SESSION_CODES:
                    raise CollectorError(TERMINAL_SESSION_CODES[reason_code])
                if reason_code == "AUTHORITATIVE_QUOTE_STALE":
                    last_error = CollectorError("WATCH_QUOTE_STALE")
                elif reason_code == (
                        "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH"):
                    last_error = CollectorError(
                        "WATCH_EXECUTION_IDENTITY_TRANSITION")
                elif reason_code in EVENT_TRANSITION_CODES:
                    last_error = CollectorError(
                        "WATCH_EXECUTION_EVENT_TRANSITION")
                else:
                    raise CollectorError("WATCH_TOOL_ENVELOPE_REJECTED")
            else:
                document = _strict_json(
                    completed.stdout, "WATCH_READ_SET_OUTPUT_INVALID")
                if (not isinstance(document, dict) or set(document) != {
                        "schema", "catalog", "descriptors", "reads",
                        "read_finished_at_ms"} or
                        document.get("schema") != "hepta.watch-read-set.v1" or
                        not isinstance(document.get("catalog"), dict) or
                        not isinstance(document.get("descriptors"), dict) or
                        set(document["descriptors"]) != set(REQUIRED_TOOLS) or
                        not all(isinstance(value, dict) for value in
                                document["descriptors"].values()) or
                        not isinstance(document.get("reads"), dict) or
                        set(document["reads"]) != set(READ_ORDER) or
                        not all(isinstance(value, dict) for value in
                                document["reads"].values()) or
                        not isinstance(
                            document.get("read_finished_at_ms"), dict) or
                        set(document["read_finished_at_ms"]) != set(READ_ORDER) or
                        not all(type(value) is int and value > 0 for value in
                                document["read_finished_at_ms"].values())):
                    raise CollectorError("WATCH_READ_SET_OUTPUT_INVALID")
                return document
        if attempt + 1 < READ_SET_ATTEMPTS:
            if (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_QUOTE_STALE"):
                delay = QUOTE_RETRY_SECONDS
            elif (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_EXECUTION_IDENTITY_TRANSITION"):
                delay = IDENTITY_RETRY_SECONDS
            elif (isinstance(last_error, CollectorError) and
                    str(last_error) == "WATCH_EXECUTION_EVENT_TRANSITION"):
                delay = EVENT_READY_RETRY_SECONDS
            else:
                delay = PROCESS_RETRY_SECONDS
            if deadline - time.monotonic() <= delay:
                raise CollectorError("WATCH_READ_SET_TIMEOUT")
            time.sleep(delay * (attempt + 1))
    if isinstance(last_error, CollectorError):
        if str(last_error) == "WATCH_EXECUTION_IDENTITY_TRANSITION":
            raise CollectorError("WATCH_EXECUTION_IDENTITY_MISMATCH")
        if str(last_error) == "WATCH_EXECUTION_EVENT_TRANSITION":
            raise CollectorError("WATCH_EXECUTION_EVENT_NOT_READY")
        raise last_error
    raise CollectorError("WATCH_TOOL_PROCESS_FAILED") from last_error


def _safe(value: object, depth: int = 0) -> object:
    if depth > 8:
        raise CollectorError("WATCH_PAYLOAD_DEPTH_INVALID")
    if isinstance(value, dict):
        return {
            key: _safe(item, depth + 1) for key, item in value.items()
            if key in SAFE_FIELDS
        }
    if isinstance(value, list):
        if len(value) > 128:
            raise CollectorError("WATCH_PAYLOAD_ARRAY_TOO_LARGE")
        return [_safe(item, depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise CollectorError("WATCH_PAYLOAD_TYPE_INVALID")


def _canonical(document: object) -> bytes:
    encoded = json.dumps(
        document, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":")).encode("ascii") + b"\n"
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise CollectorError("WATCH_SNAPSHOT_TOO_LARGE")
    return encoded


def _validate_reads(reads: dict[str, object]) -> None:
    health = reads.get("system.get_health")
    account = reads.get("account.get_summary")
    positions = reads.get("portfolio.list_positions")
    orders = reads.get("orders.list")
    risk = reads.get("risk.get_limits")
    quote = reads.get("market.get_quote")
    if (not isinstance(health, dict) or
            health.get("gateway_ready") is not True or
            health.get("remote_execution") is not True or
            health.get("remote_execution_configured") is not True or
            health.get("remote_execution_ready") is not True or
            health.get("execution_mode") != "SIMULATOR" or
            health.get("remote_execution_reason") != ""):
        raise CollectorError("WATCH_HEALTH_NOT_AUTHORITATIVE")
    if (not isinstance(account, dict) or
            account.get("authoritative") is not True or
            account.get("account_complete") is not True):
        raise CollectorError("WATCH_ACCOUNT_NOT_AUTHORITATIVE")
    if (not isinstance(positions, dict) or
            positions.get("authoritative") is not True or
            not isinstance(positions.get("positions"), list)):
        raise CollectorError("WATCH_POSITIONS_NOT_AUTHORITATIVE")
    if (not isinstance(orders, dict) or
            orders.get("authoritative") is not True or
            not isinstance(orders.get("active_order_ids"), list)):
        raise CollectorError("WATCH_ORDERS_NOT_AUTHORITATIVE")
    if (not isinstance(risk, dict) or risk.get("authoritative") is not True or
            type(risk.get("gross_absolute_position")) not in (int, float)):
        raise CollectorError("WATCH_RISK_NOT_AUTHORITATIVE")
    if (not isinstance(quote, dict) or quote.get("authoritative") is not True or
            quote.get("stale") is not False or
            type(quote.get("observed_at_ms")) is not int or
            type(quote.get("stale_after_ms")) is not int or
            quote.get("stale_after_ms", 0) <= quote.get("observed_at_ms", 0)):
        raise CollectorError("WATCH_QUOTE_NOT_AUTHORITATIVE")


def _atomic_write(path: Path, contents: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".snapshot-", delete=False) as file:
        temporary = Path(file.name)
        os.fchmod(file.fileno(), 0o600)
        file.write(contents)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def collect(config_path: Path, output: Path, instrument: str) -> dict[str, object]:
    config = load_runtime_config(
        config_path, require_root_metadata=True,
        expected_agent_identity=(os.geteuid(), os.getegid()))
    _identity(config)
    if instrument != "EUR.USD":
        raise CollectorError("WATCH_INSTRUMENT_FORBIDDEN")
    token_path = Path(config["token_directory"]) / "session.token"
    try:
        _regular(
            token_path, 0o600,
            int(config["agent_uid"]), int(config["agent_gid"]))
    except FileNotFoundError as error:
        raise CollectorError(
            "WATCH_SESSION_AUTHORITY_NOT_FOUND") from error

    # The cadence boundary covers the complete authority read, including
    # catalog and descriptor verification.  Recording it after discovery makes
    # variable discovery latency look like a missed market-history sample even
    # when the collector itself was launched on time.
    collection_started_at_ms = time.time_ns() // 1_000_000
    read_set = _read_set(str(config["socket_path"]), token_path, instrument)
    catalog = read_set["catalog"]
    tools = catalog.get("tools")
    if not isinstance(tools, list):
        raise CollectorError("WATCH_CATALOG_INVALID")
    names = {item.get("name") for item in tools if isinstance(item, dict)}
    if (not set(REQUIRED_TOOLS).issubset(names) or names & FORBIDDEN_TOOLS or
            len(names) != len(tools)):
        raise CollectorError("WATCH_CATALOG_AUTHORITY_INVALID")

    descriptors = {
        tool: "sha256:" + hashlib.sha256(
            _canonical(read_set["descriptors"][tool])).hexdigest()
        for tool in REQUIRED_TOOLS
    }

    reads = {tool: _safe(read_set["reads"][tool]) for tool in READ_ORDER}
    read_finished_at_ms = read_set["read_finished_at_ms"]
    _validate_reads(reads)

    collection_finished_at_ms = time.time_ns() // 1_000_000
    generated_at_ms = time.time_ns() // 1_000_000
    body = {
        "schema": "hepta.shadow-watch-snapshot.v2",
        "version": 2,
        "domain_id": config["domain_id"],
        "agent_uid": config["agent_uid"],
        "collection_started_at_ms": collection_started_at_ms,
        "collection_finished_at_ms": collection_finished_at_ms,
        "read_finished_at_ms": read_finished_at_ms,
        "generated_at_ms": generated_at_ms,
        "instrument": instrument,
        "catalog_sha256": "sha256:" + hashlib.sha256(
            _canonical(catalog)).hexdigest(),
        "descriptor_sha256": descriptors,
        "reads": reads,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    body_bytes = _canonical(body)
    snapshot = {
        **body,
        "body_sha256": "sha256:" + hashlib.sha256(body_bytes).hexdigest(),
    }
    _atomic_write(output, _canonical(snapshot))
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instrument", default="EUR.USD")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        snapshot = collect(
            arguments.domain_config, arguments.output, arguments.instrument)
    except (CollectorError, OSError, ValueError) as error:
        print("hepta_shadow_watch_collector: FAIL: " + str(error), file=sys.stderr)
        return 78
    print(json.dumps({
        "status": "ok", "schema": snapshot["schema"],
        "body_sha256": snapshot["body_sha256"],
        "mutation_attempted": False, "live_authorized": False,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
