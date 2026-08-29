#!/usr/bin/env python3

"""Unprivileged client for the bounded root-owned IB PAPER campaign gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
from typing import Any, Optional


REQUEST_SCHEMA = "hepta.ib-paper-campaign-request.v1"
RESPONSE_SCHEMA = "hepta.ib-paper-campaign-response.v1"
DOMAIN = re.compile(r"[a-z][a-z0-9-]{0,31}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REASON = re.compile(r"[A-Z][A-Z0-9_]{2,95}")
OUTCOMES = {
    "PREVIEW_REJECTED", "PLACE_REJECTED", "PLACE_ACCEPTED",
    "PLACE_UNCERTAIN", "OPERATOR_ABORT",
}
MAX_BYTES = 64 * 1024


class ClientError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ClientError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ClientError(f"{label} root is not an object")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, ensure_ascii=True, separators=(",", ":"),
            sort_keys=True, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as error:
        raise ClientError("request is not canonical JSON") from error


def _stable_read(path: Path) -> bytes:
    before = os.lstat(path)
    if (
            stat.S_ISLNK(before.st_mode) or
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
            before.st_uid != os.geteuid() or
            stat.S_IMODE(before.st_mode) & 0o022 or
            before.st_size < 2 or before.st_size > MAX_BYTES):
        raise ClientError("input file metadata is unsafe")
    descriptor = os.open(
        path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
        item.st_uid, item.st_gid, item.st_size, item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if (
            len(raw) > MAX_BYTES or identity(before) != identity(opened) or
            identity(opened) != identity(after)):
        raise ClientError("input file changed while reading")
    return bytes(raw)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _identifier(value: str, label: str) -> str:
    if IDENTIFIER.fullmatch(value) is None:
        raise ClientError(f"{label} is invalid")
    return value


def _request(arguments: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "version": 1,
        "action": arguments.action,
        "request_id": _identifier(arguments.request_id, "request id"),
        "domain_id": arguments.domain,
        "campaign_id": _identifier(arguments.campaign_id, "campaign id"),
    }
    if arguments.action == "open_cycle":
        intent_raw = _stable_read(arguments.intent_file)
        intent = _strict_json(intent_raw, "intent")
        canonical_intent = _canonical_json(intent)
        preflight_raw = _stable_read(arguments.preflight_file)
        request.update({
            "cycle_id": _identifier(arguments.cycle_id, "cycle id"),
            "intent": intent,
            "intent_sha256": _sha256(canonical_intent),
            "preflight_sha256": _sha256(preflight_raw),
        })
    elif arguments.action == "close_cycle":
        if DIGEST.fullmatch(arguments.intent_sha256) is None:
            raise ClientError("intent digest is invalid")
        request.update({
            "cycle_id": _identifier(arguments.cycle_id, "cycle id"),
            "intent_sha256": arguments.intent_sha256,
            "outcome": arguments.outcome,
        })
    elif arguments.action == "halt":
        if REASON.fullmatch(arguments.reason_code) is None:
            raise ClientError("reason code is invalid")
        request["reason_code"] = arguments.reason_code
    return request


def _exchange(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    payload = _canonical_json(request)
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        channel.settimeout(30)
        channel.connect(str(socket_path))
        channel.sendall(payload)
        channel.shutdown(socket.SHUT_WR)
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = channel.recv(min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
    except OSError as error:
        raise ClientError("campaign socket exchange failed") from error
    finally:
        channel.close()
    if len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
        raise ClientError("campaign response frame is invalid")
    response = _strict_json(bytes(raw), "campaign response")
    if (
            response.get("schema") != RESPONSE_SCHEMA or
            response.get("version") != 1 or
            response.get("action") != request["action"] or
            response.get("request_id") != request["request_id"] or
            response.get("domain_id") != request["domain_id"] or
            response.get("campaign_id") != request["campaign_id"] or
            response.get("status") not in {
                "ok", "rejected", "recovery_required"} or
            not isinstance(response.get("reason_code"), str) or
            response.get("detail") != ""):
        raise ClientError("campaign response contract is invalid")
    return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--socket", type=Path)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    opened = actions.add_parser("open_cycle")
    opened.add_argument("--cycle-id", required=True)
    opened.add_argument("--intent-file", type=Path, required=True)
    opened.add_argument("--preflight-file", type=Path, required=True)
    closed = actions.add_parser("close_cycle")
    closed.add_argument("--cycle-id", required=True)
    closed.add_argument("--intent-sha256", required=True)
    closed.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    halted = actions.add_parser("halt")
    halted.add_argument("--reason-code", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if DOMAIN.fullmatch(arguments.domain) is None:
            raise ClientError("domain is invalid")
        socket_path = arguments.socket or Path(
            f"/run/hepta-agent-{arguments.domain}/campaign.sock")
        response = _exchange(socket_path, _request(arguments))
        sys.stdout.buffer.write(_canonical_json(response))
        return 0 if response["status"] == "ok" else 1
    except (ClientError, OSError) as error:
        print(f"hepta-campaignctl: FAIL {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
