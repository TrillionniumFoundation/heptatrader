#!/usr/bin/env -S /usr/bin/python3.12 -I -S

"""Provision the one fixed durable PAPER owner after a validated TRADE."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Any, Optional


PEER_UID = 2104
PEER_GID = 2104
OWNER_ROOT = Path("/var/lib/hepta-local-ai-paper-agent/session-authority")
AUTHORITY_PATH = OWNER_ROOT / "session.token.authority.json"
REVOKE_PATH = OWNER_ROOT / "session.token.revoke-token"
PROVISIONING_PATH = OWNER_ROOT / ".session.token.provisioning"
INTENT_PATH = OWNER_ROOT / "session.token.owner-may-exist.v1.json"
TOKEN_ROOT = Path("/run/hepta-agent-alpha/sessions")
TOKEN_PATH = TOKEN_ROOT / "session.token"
SUPERVISOR_SOCKET = Path(
    "/run/hepta-tool-gateway-alpha/session-supervisor.sock")
SESSIONCTL = Path("/usr/bin/hepta-sessionctl")
SCHEMA = "hepta.p1-paper-canary-session-owner-authority.v1"
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
PAPER_ACCOUNT = re.compile(r"DU[0-9]{1,16}")


class OwnerError(RuntimeError):
    pass


class OwnerProcessDeath(BaseException):
    """Test-only process-death seam; production never raises this type."""


def canonical(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        allow_nan=False) + "\n").encode("ascii")


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "body_sha256": sha(canonical(body))}


def identifier(value: Any, reason: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise OwnerError(reason)
    return value


def _ensure_directories() -> None:
    OWNER_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(OWNER_ROOT, 0, 0)
    os.chmod(OWNER_ROOT, 0o700)
    owner = os.lstat(OWNER_ROOT)
    if (
            stat.S_ISLNK(owner.st_mode) or not stat.S_ISDIR(owner.st_mode) or
            owner.st_uid != 0 or owner.st_gid != 0 or
            stat.S_IMODE(owner.st_mode) != 0o700):
        raise OwnerError("OWNER_PARENT_INVALID")
    token_root = os.lstat(TOKEN_ROOT)
    if (
            stat.S_ISLNK(token_root.st_mode) or
            not stat.S_ISDIR(token_root.st_mode) or
            token_root.st_uid != PEER_UID or token_root.st_gid != PEER_GID or
            stat.S_IMODE(token_root.st_mode) & 0o022):
        raise OwnerError("OWNER_TOKEN_PARENT_INVALID")


def _exclusive(path: Path, raw: bytes, *, uid: int, gid: int, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, mode)
        try:
            os.fchown(descriptor, uid, gid)
            os.fchmod(descriptor, mode)
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise OwnerError("OWNER_PUBLISH_FAILED") from error
    try:
        descriptor = os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise OwnerError("OWNER_PUBLISH_FAILED") from error


def _sessionctl(arguments: list[str]) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(
            [str(SESSIONCTL), "--socket", str(SUPERVISOR_SOCKET), *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd="/", env={"LC_ALL": "C"},
            close_fds=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OwnerError("OWNER_SUPERVISOR_UNCERTAIN") from error
    if len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
        raise OwnerError("OWNER_SUPERVISOR_RESPONSE_INVALID")
    try:
        value = json.loads(completed.stdout.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise OwnerError("OWNER_SUPERVISOR_RESPONSE_INVALID") from error
    if not isinstance(value, dict):
        raise OwnerError("OWNER_SUPERVISOR_RESPONSE_INVALID")
    return completed.returncode, value


def _revoke(generation: int) -> None:
    arguments = [
        "revoke", "--token-file", str(PROVISIONING_PATH),
        "--generation", str(generation), "--token-owner-uid", "0",
    ]
    code, response = _sessionctl(arguments)
    if not (
            code == 0 and response == {
                "accepted": True, "reason_code": "OK",
                "lease_generation": generation}):
        raise OwnerError("OWNER_COMPENSATING_REVOKE_FAILED")
    audit_code, audit = _sessionctl(arguments)
    if not (
            audit_code == 4 and set(audit) == {
                "accepted", "reason_code", "lease_generation"} and
            audit.get("accepted") is False and
            audit.get("reason_code") in {
                "SESSION_NOT_FOUND", "SESSION_LEASE_NOT_FOUND"} and
            audit.get("lease_generation") in {0, generation}):
        raise OwnerError("OWNER_COMPENSATING_REVOKE_AUDIT_UNCERTAIN")


def provision(
        campaign: str, cycle: str, execution_service_epoch: str,
        fencing_generation: int, owner_account: str,
        owner_execution_domain: str, *, now_ms: Optional[int] = None,
        crash_hook: Any = lambda _phase: None,
) -> bytes:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise OwnerError("OWNER_ROOT_REQUIRED")
    campaign = identifier(campaign, "OWNER_CAMPAIGN_INVALID")
    cycle = identifier(cycle, "OWNER_CYCLE_INVALID")
    epoch = identifier(execution_service_epoch, "OWNER_EPOCH_INVALID")
    if (not isinstance(owner_account, str) or
            PAPER_ACCOUNT.fullmatch(owner_account) is None or
            owner_execution_domain != "PAPER:alpha"):
        raise OwnerError("OWNER_BROKER_SCOPE_INVALID")
    if (
            isinstance(fencing_generation, bool) or
            not isinstance(fencing_generation, int) or fencing_generation < 1):
        raise OwnerError("OWNER_FENCE_INVALID")
    _ensure_directories()
    if any(path.exists() or path.is_symlink() for path in (
            TOKEN_PATH, AUTHORITY_PATH, REVOKE_PATH, PROVISIONING_PATH,
            INTENT_PATH)):
        raise OwnerError("OWNER_ALREADY_EXISTS")
    token = os.urandom(32).hex().encode("ascii") + b"\n"
    _exclusive(PROVISIONING_PATH, token, uid=0, gid=0, mode=0o600)
    session_id = "p1-canary-" + hashlib.sha256(
        f"{campaign}\0{cycle}".encode("ascii")).hexdigest()[:32]
    created = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    intent = seal({
        "schema": "hepta.p1-paper-canary-owner-may-exist.v1",
        "version": 1, "created_at_ms": created,
        "campaign_id": campaign, "domain_id": "alpha", "cycle_id": cycle,
        "token_name": "session.token", "token_sha256": sha(token),
        "token_bearer_path": str(PROVISIONING_PATH),
        "expected_lease_generation": 1, "session_id": session_id,
        "peer_uid": PEER_UID, "peer_gid": PEER_GID,
        "owner_account": owner_account,
        "owner_execution_domain": owner_execution_domain,
        "paper_only": True, "live_authorized": False,
        "authority_granted": False,
    })
    _exclusive(
        INTENT_PATH, canonical(intent), uid=0, gid=0, mode=0o600)
    # This durable marker is deliberately committed before the supervisor
    # call.  ExecStopPost can now reconcile a committed HSL lease even when a
    # SIGKILL prevents the handoff and authority document from being written.
    crash_hook("OWNER_MAY_EXIST_DURABLE")
    accepted_generation = 0
    try:
        code, response = _sessionctl([
            "provision", "--template", "paper", "--token-file",
            str(PROVISIONING_PATH), "--agent-id", "hepta-agent-alpha",
            "--session-id", session_id, "--peer-uid", str(PEER_UID),
            "--ttl-sec", "300",
        ])
        if (
                code != 0 or set(response) != {
                    "accepted", "reason_code", "lease_generation"} or
                response["accepted"] is not True or
                response["reason_code"] != "OK" or
                isinstance(response["lease_generation"], bool) or
                not isinstance(response["lease_generation"], int) or
                response["lease_generation"] != 1):
            raise OwnerError("OWNER_PROVISION_REJECTED")
        accepted_generation = response["lease_generation"]
        crash_hook("SESSION_PROVISION_COMMITTED")
        _exclusive(TOKEN_PATH, token, uid=PEER_UID, gid=PEER_GID, mode=0o400)
        os.replace(PROVISIONING_PATH, REVOKE_PATH)
        os.chown(REVOKE_PATH, 0, 0)
        os.chmod(REVOKE_PATH, 0o600)
        body = {
            "schema": SCHEMA, "version": 1, "created_at_ms": created,
            "campaign_id": campaign, "domain_id": "alpha", "cycle_id": cycle,
            "token_name": "session.token",
            "lease_generation": accepted_generation,
            "session_id": session_id, "peer_uid": PEER_UID,
            "peer_gid": PEER_GID, "token_sha256": sha(token),
            "execution_service_epoch": epoch,
            "execution_service_fencing_generation": fencing_generation,
            "owner_account": owner_account,
            "owner_execution_domain": owner_execution_domain,
            "paper_only": True, "live_authorized": False,
            "authority_granted": False,
        }
        raw = canonical(seal(body))
        _exclusive(AUTHORITY_PATH, raw, uid=0, gid=0, mode=0o600)
        return raw
    except BaseException as error:
        if isinstance(error, OwnerProcessDeath):
            raise
        if accepted_generation > 0:
            try:
                # If the bearer was already renamed, temporarily use its fixed
                # path for the compensating revoke.
                if REVOKE_PATH.exists() and not PROVISIONING_PATH.exists():
                    os.link(REVOKE_PATH, PROVISIONING_PATH)
                _revoke(accepted_generation)
            except BaseException as revoke_error:
                raise OwnerError("OWNER_RECOVERY_REQUIRED") from revoke_error
        for path in (
                TOKEN_PATH, AUTHORITY_PATH, REVOKE_PATH, PROVISIONING_PATH,
                INTENT_PATH):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if isinstance(error, OwnerError):
            raise
        raise OwnerError("OWNER_PROVISION_FAILED") from error


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--cycle-id", required=True)
    parser.add_argument("--execution-service-epoch", required=True)
    parser.add_argument("--fencing-generation", required=True, type=int)
    parser.add_argument("--owner-account", required=True)
    parser.add_argument("--owner-execution-domain", required=True)
    arguments = parser.parse_args(argv)
    try:
        raw = provision(
            arguments.campaign_id, arguments.cycle_id,
            arguments.execution_service_epoch, arguments.fencing_generation,
            arguments.owner_account, arguments.owner_execution_domain)
    except OwnerError as error:
        print(f"hepta-p1-paper-canary-owner-provisioner: FAIL {error}")
        return 2
    print(canonical({
        "authority_file_sha256": sha(raw), "authority_granted": False,
        "status": "PROVISIONED",
    }).decode("ascii"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
