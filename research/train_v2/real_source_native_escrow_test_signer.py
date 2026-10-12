"""Test-only, per-machine Ed25519 signer; private key NEVER crosses SSH.

Cross-host native qualification laboratory, not user/product credential
issuance, market information or production signer enrollment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat, PublicFormat,
)

ROOT = Path.home() / ".local" / "state" / "hepta-escrow-recovery-signer-20261012"
EVIDENCE_DOMAIN = b"hepta.learning-ledger.signed-evidence.v1"
ROOT_DOMAIN = b"hepta.learning-ledger.trust-distribution-signature.v1"
PRINCIPALS = {
    "generator": ("lab.generator", 0),
    "observer": ("lab.observer", 1),
    "evaluator": ("lab.evaluator", 2),
    "privacy": ("lab.privacy", 4),
}


def _directory() -> Path:
    ROOT.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    state = ROOT.lstat()
    if (not stat.S_ISDIR(state.st_mode) or state.st_uid != os.getuid()
            or stat.S_IMODE(state.st_mode) != 0o700):
        raise ValueError("PRIVATE_SIGNER_DIRECTORY_REQUIRED")
    return ROOT


def _key(role: str) -> Path:
    if role not in ("root", *PRINCIPALS):
        raise ValueError("SIGNER_ROLE_UNRECOGNIZED")
    return _directory() / (role + ".test-only-key")


def _load(role: str) -> Ed25519PrivateKey:
    path = _key(role)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size != 32):
            raise ValueError("TEST_ONLY_KEY_UNSAFE")
        raw = os.read(fd, 33)
        if len(raw) != 32:
            raise ValueError("TEST_ONLY_KEY_BYTES")
        return Ed25519PrivateKey.from_private_bytes(raw)
    finally:
        os.close(fd)


def _public(role: str) -> bytes:
    return _load(role).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)


def _read_native_id(raw: bytes, pos: int) -> tuple[str,int]:
    if pos + 8 > len(raw):
        raise ValueError("NATIVE_ID_SHORT")
    n = int.from_bytes(raw[pos:pos+8], "big")
    pos += 8
    if not 1 <= n <= 128 or pos+n > len(raw):
        raise ValueError("NATIVE_ID_BOUNDS")
    return raw[pos:pos+n].decode("ascii"),pos+n


def _scope(role: str, raw: bytes) -> None:
    if not 0 < len(raw) <= 4096:
        raise ValueError("TEST_ONLY_SIGNING_MESSAGE_BOUNDS")
    if role == "root":
        if not raw.startswith(ROOT_DOMAIN):
            raise ValueError("NOT_CANONICAL_NATIVE_DISTRIBUTION")
        root, _ = _read_native_id(raw, len(ROOT_DOMAIN))
        if root != "lab.root":
            raise ValueError("CROSSHOST_TEST_ROOT_ID_REQUIRED")
        return
    if not raw.startswith(EVIDENCE_DOMAIN):
        raise ValueError("NOT_CANONICAL_NATIVE_EVIDENCE")
    evidence_id, pos = _read_native_id(raw,len(EVIDENCE_DOMAIN))
    principal, pos = _read_native_id(raw,pos)
    expected, tag = PRINCIPALS[role]
    if (principal != expected or not evidence_id.startswith("lab.")
            or pos >= len(raw) or raw[pos] != tag):
        raise ValueError("TEST_SIGNER_ROLE_PRINCIPAL_MISMATCH")


def _stamp(role: str) -> dict:
    return {
        "role":role,
        "actual_signing_host":socket.gethostname(),
        "host_name_sha256":hashlib.sha256(socket.gethostname().encode()).hexdigest(),
        "public_key_hex":_public(role).hex(),
        "test_only":True,
        "private_key_exported":False,
    }


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("init","pub","sign","destroy"))
    parser.add_argument("role",choices=("root","generator","observer","evaluator","privacy"))
    args=parser.parse_args()
    path=_key(args.role)
    if args.mode == "init":
        if path.exists() or path.is_symlink():
            raise FileExistsError("TEST_SIGNER_KEY_ALREADY_EXISTS")
        key=Ed25519PrivateKey.generate()
        raw=key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())
        fd=os.open(path,os.O_WRONLY|os.O_EXCL|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        with os.fdopen(fd,"wb") as f:
            f.write(raw);f.flush();os.fsync(f.fileno())
        d=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(d)
        finally:os.close(d)
        print(json.dumps({**_stamp(args.role),"created":True},sort_keys=True))
    elif args.mode == "pub":
        print(json.dumps(_stamp(args.role),sort_keys=True))
    elif args.mode == "sign":
        line=sys.stdin.buffer.readline(8193).strip()
        if len(line) > 8192:
            raise ValueError("TEST_MESSAGE_HEX_BOUNDS")
        raw=bytes.fromhex(line.decode("ascii"))
        _scope(args.role,raw)
        signing=_load(args.role)
        sig=signing.sign(raw)
        print(json.dumps({
            **_stamp(args.role),
            "signature_hex":sig.hex(),
            "native_signing_message_sha256":hashlib.sha256(raw).hexdigest(),
            "signed_at_utc":datetime.now(timezone.utc).isoformat(),
        },sort_keys=True))
    else:
        _load(args.role)  # refuse removal of any unexpectedly different key
        path.unlink()
        fd=os.open(ROOT,os.O_DIRECTORY|os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        print(json.dumps({"role":args.role,"test_only_key_destroyed":True},sort_keys=True))


if __name__=="__main__":
    main()
