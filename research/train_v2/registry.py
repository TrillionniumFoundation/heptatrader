"""Local, durable prospective-plan registry for NEW Train V2 research trials.

No canonical owner enrollment, broker identity, model dispatch or trade control.
This verifies a local pre-event time fence; it cannot prevent privileged host
operators from changing clocks or modifying files and is NOT an external proof.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from historical_training.common import canonical, read, sha
from .contracts import CAUSAL, ContractError, feature_spec_id, validate_spec
from .orchestrator import verify_plan
from .handoff import ingest_original_event, verify_original_paper_handoff

RECEIPT_SCHEMA = "heptatrader.train-v2-local-prospective-registration.v1"
MIN_LEAD_MS = 30000
MAX_DECLARED_CLOCK_SKEW_MS = 3000
MAX_WINDOW_MS = 16 * 24 * 60 * 60 * 1000


def _private_directory(directory, *, create=False):
    path = Path(directory)
    if path.is_symlink():
        raise ContractError("REGISTRY_SYMLINK_DIRECTORY")
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = path.stat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid()
            or st.st_mode & 0o077):
        raise ContractError("REGISTRY_DIRECTORY_NOT_PRIVATE")
    return path


def _registry_connection(directory, *, create=False):
    if not create and not Path(directory).is_dir():
        raise ContractError("REGISTRY_UNREGISTERED_PLAN")
    root = _private_directory(directory, create=create)
    dbpath = root / "registrations.sqlite"
    if not create and not dbpath.is_file():
        raise ContractError("REGISTRY_UNREGISTERED_PLAN")
    if dbpath.is_symlink():
        raise ContractError("REGISTRY_DB_SYMLINK")
    fd = os.open(dbpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        raise ContractError("REGISTRY_DB_NOT_PRIVATE")
    os.close(fd)
    db = sqlite3.connect(dbpath, timeout=5, isolation_level=None)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS registrations (
          plan_id TEXT PRIMARY KEY,
          period_sha256 TEXT NOT NULL,
          instrument TEXT NOT NULL,
          feature_spec_id TEXT NOT NULL,
          policy_id TEXT NOT NULL,
          first_ms INTEGER NOT NULL,
          last_ms INTEGER NOT NULL,
          sealed_at_ms INTEGER NOT NULL,
          receipt_sha256 TEXT NOT NULL,
          registration_json TEXT NOT NULL
        )
    """)
    return root, db


def _read_seal(root, plan_id):
    directory = root / "sealed-plans"
    if directory.is_symlink():
        raise ContractError("REGISTRY_SEALS_SYMLINK")
    path = directory / (plan_id + ".json")
    if path.is_symlink() or not path.is_file():
        raise ContractError("REGISTRY_SEAL_MISSING")
    document = read(path, 4096)
    if document.get("schema") != RECEIPT_SCHEMA or document.get("plan_id") != plan_id:
        raise ContractError("REGISTRY_SEAL_WRONG_IDENTITY")
    return document, sha(path)


def _original_root(path):
    """Bind a private, existing original-source directory. Not a broker attestation."""
    source = _private_directory(path, create=False)
    # Store the exact absolute path and refuse links in every resolved component.
    if not source.is_absolute() or ".." in source.parts:
        raise ContractError("REGISTRY_ORIGINAL_SOURCE_ROOT")
    for ancestor in (source, *source.parents):
        if ancestor.is_symlink():
            raise ContractError("REGISTRY_SOURCE_ANCESTOR_SYMLINK")
    return source


def register_before_window(directory, plan, spec, original_source_root):
    """Seal one plan before the earliest original decision, never backdate.

    A caller cannot supply the registry timestamp. The receipt is fsynced
    before the SQLite registration commits; an orphaned seal after a crash is
    rejected, never interpreted as pre-registered evidence.
    """
    verify_plan(plan)
    validate_spec(spec)
    if (spec["mode"] != CAUSAL or spec["period_sha256"] != plan["period_sha256"]
            or spec["behavior_policy_id"] != plan["behavior_policy_id"]
            or feature_spec_id(spec) != plan["feature_spec_id"]):
        raise ContractError("REGISTRY_PLAN_SCOPE")
    source = _original_root(original_source_root)
    now = time.time_ns() // 1_000_000
    if (plan["last_decision_ms"] - plan["first_decision_ms"] > MAX_WINDOW_MS
            or plan["first_decision_ms"] - now > MAX_WINDOW_MS):
        raise ContractError("REGISTRY_WINDOW_TOO_WIDE_OR_DISTANT")
    declared = plan["registered_at_ms"]
    if (type(declared) is not int
            or not 0 <= now - declared <= MAX_DECLARED_CLOCK_SKEW_MS
            or plan["first_decision_ms"] < now + MIN_LEAD_MS):
        raise ContractError("REGISTRY_NOT_PROSPECTIVELY_DECLARED")
    root, db = _registry_connection(directory, create=True)
    fd = os.open(root / "registry.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM registrations WHERE plan_id=?", (plan["plan_id"],)).fetchone():
            raise ContractError("REGISTRY_PLAN_ALREADY_REGISTERED")
        if db.execute(
            """SELECT 1 FROM registrations WHERE period_sha256=? AND instrument=?
               AND NOT (last_ms < ? OR first_ms > ?) LIMIT 1""",
            (plan["period_sha256"], spec["instrument"],
             plan["first_decision_ms"], plan["last_decision_ms"])
        ).fetchone():
            raise ContractError("REGISTRY_TRIAL_WINDOW_ALREADY_USED")
        directory_seals = root / "sealed-plans"
        _private_directory(directory_seals, create=True)
        seal_path = directory_seals / (plan["plan_id"] + ".json")
        if seal_path.exists() or seal_path.is_symlink():
            raise ContractError("REGISTRY_UNKNOWN_ORPHAN_SEAL")
        now = time.time_ns() // 1_000_000
        if (not 0 <= now - declared <= MAX_DECLARED_CLOCK_SKEW_MS
                or plan["first_decision_ms"] < now + MIN_LEAD_MS):
            raise ContractError("REGISTRY_CLOCK_CHANGED_DURING_FSYNC")
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "plan_id": plan["plan_id"],
            "plan_sha256": __import__("hashlib").sha256(canonical(plan)).hexdigest(),
            "plan": plan,
            "feature_spec": spec,
            "feature_spec_id": feature_spec_id(spec),
            "period_sha256": plan["period_sha256"],
            "policy_id": plan["behavior_policy_id"],
            "instrument": spec["instrument"],
            "original_source_root": str(source),
            "original_source_root_owner_uid": os.getuid(),
            "first_decision_ms": plan["first_decision_ms"],
            "last_decision_ms": plan["last_decision_ms"],
            "sealed_at_ms": now,
            "independent_owner_signature": False,
            "canonical_agentd_owner_enrolled": False,
            "broker_market_data_signed": False,
            "can_relabel_previously_inspected_data_as_unseen": False,
            "future_automatic_training": False,
            "trade_authority": False,
            "broker_orders_sent": 0,
        }
        raw = canonical(receipt) + b"\n"
        w = os.open(seal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(w, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        dirfd = os.open(directory_seals, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        receipt_sha = sha(seal_path)
        db.execute(
            "INSERT INTO registrations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (plan["plan_id"], plan["period_sha256"], spec["instrument"],
             plan["feature_spec_id"], plan["behavior_policy_id"],
             plan["first_decision_ms"], plan["last_decision_ms"],
             now, receipt_sha, json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        )
        db.execute("COMMIT")
        return receipt
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        os.close(fd)
        db.close()


def verify_registration(directory, plan, spec, source_file):
    verify_plan(plan)
    validate_spec(spec)
    root, db = _registry_connection(directory)
    try:
        row = db.execute(
            "SELECT period_sha256,instrument,feature_spec_id,policy_id,first_ms,"
            "last_ms,sealed_at_ms,receipt_sha256,registration_json "
            "FROM registrations WHERE plan_id=?",
            (plan["plan_id"],)
        ).fetchone()
        if row is None:
            raise ContractError("REGISTRY_UNREGISTERED_PLAN")
        receipt, receipt_hash = _read_seal(root, plan["plan_id"])
        (period, instrument, spec_id, policy_id, start, end, sealed, sha_stored, document) = row
        if (receipt_hash != sha_stored
                or receipt != json.loads(document)
                or receipt["plan_sha256"] != __import__("hashlib").sha256(canonical(plan)).hexdigest()
                or receipt.get("plan") != plan or receipt.get("feature_spec") != spec
                or receipt.get("independent_owner_signature") is not False
                or receipt.get("canonical_agentd_owner_enrolled") is not False
                or receipt.get("broker_market_data_signed") is not False
                or receipt.get("future_automatic_training") is not False
                or receipt.get("trade_authority") is not False
                or receipt.get("broker_orders_sent") != 0
                or receipt.get("original_source_root_owner_uid") != os.getuid()
                or receipt["sealed_at_ms"] != sealed
                or period != spec["period_sha256"] or instrument != spec["instrument"]
                or spec_id != feature_spec_id(spec) or policy_id != spec["behavior_policy_id"]
                or (start, end) != (plan["first_decision_ms"], plan["last_decision_ms"])):
            raise ContractError("REGISTRY_PLAN_RECEIPT_OR_SCOPE_CHANGED")
        source_root = _original_root(Path(receipt["original_source_root"]))
        source_file = Path(source_file)
        if source_file.is_symlink() or not source_file.is_file():
            raise ContractError("REGISTRY_ORIGINAL_FILE_UNAVAILABLE")
        if not source_file.is_absolute() or ".." in source_file.parts:
            raise ContractError("REGISTRY_ORIGINAL_FILE_ESCAPE")
        try:
            relative = source_file.relative_to(source_root)
        except ValueError as exc:
            raise ContractError("REGISTRY_ORIGINAL_ROOT_MISMATCH") from exc
        if not relative.parts:
            raise ContractError("REGISTRY_ORIGINAL_FILE_ESCAPE")
        for parent in source_file.parents:
            if parent == source_root:
                break
            if parent.is_symlink():
                raise ContractError("REGISTRY_ORIGINAL_SUBDIRECTORY_SYMLINK")
        return receipt
    finally:
        db.close()


def ingest_registered_original_event(directory, ledger_file, plan, dataset, event, source_file):
    """Admit only matching pre-existing local plan + original source witness.

    Ledger must live under the private registry, blocking accidental mixing
    with an unregistered fixture ledger. No model or trade effect authority.
    """
    spec = dataset.get("feature_spec")
    if not isinstance(spec, dict):
        raise ContractError("REGISTRY_DATASET_SPEC_MISSING")
    receipt = verify_registration(directory, plan, spec, source_file)
    ledger = Path(ledger_file)
    root = _private_directory(directory)
    if ledger.parent != root or ledger.name != "registered-events.sqlite":
        raise ContractError("REGISTRY_EVENT_LEDGER_NOT_BOUND")
    if event.get("at_ms", 0) < receipt["sealed_at_ms"]:
        raise ContractError("REGISTRY_EVENT_PRECEDES_LOCAL_REGISTRATION")
    # Recheck original source immediately before SQLite event admission.
    proof = verify_original_paper_handoff(plan, dataset, event, source_file)
    status = ingest_original_event(ledger, plan, dataset, event, source_file)
    return {
        "schema": "heptatrader.train-v2-registered-local-ingest.v1",
        "plan_id": plan["plan_id"],
        "event_id": event["event_id"],
        "sealed_plan_at_ms": receipt["sealed_at_ms"],
        "local_chain_proof_sha256": __import__("hashlib").sha256(canonical(proof)).hexdigest(),
        "status": status["result"],
        "independent_owner_signature": False,
        "canonical_agentd_accepted": False,
        "automatic_training": False,
        "broker_orders_sent": 0,
    }
