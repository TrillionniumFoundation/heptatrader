"""Prospective registration only: preserve trials, source receipts and trained artifacts.

No torch, model scores, broker calls, trading authority or evaluation rescheduling.
The scheduler's flock is retained; SQLite additionally binds an allocated trial
and its complete immutable plan in the same durable transaction.
"""
from __future__ import annotations
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from .integrity import ContractError, canonical, digest, read_json, sha_file
from .resident_store import get_meta, put_meta, atomic
from .resident_queue import inspect as inspect_queue
from .resident_plan_calendar import calendar_admission
from .resident_source_policy import read_for_future_plan
from .resident_data import forecast_input
from .shadow_source import valid_quote, strict

PROFILE = "REGULAR_FX_FULL_WINDOW_ORIGINAL_SOURCE_V1"
SCHEMA = "heptatrader.forward-plan-registration.v1"
KEY_PREFIX = "forward_plan_registration:"
RUNTIME_PIN = ("learn/laya_cell/resident_plan_admission.py",
               "learn/laya_cell/resident_plan_calendar.py",
               "learn/laya_cell/market_session.py")
MAX_RECEIPT_SEARCH_BYTES = 64 * 1024 * 1024


def check_config(config):
    profile = config.get("future_plan_admission_profile")
    if profile is None:
        return
    if profile != PROFILE:
        raise ContractError("UNREVIEWED_FORWARD_PLAN_ADMISSION_PROFILE")
    effective = config.get("future_plan_admission_effective_at_ms")
    if type(effective) is not int or effective <= 0:
        raise ContractError("FORWARD_PLAN_POLICY_EFFECTIVE_TIME_REQUIRED")
    pins = config.get("runtime_sha256", {})
    if any(not isinstance(pins.get(name), str) or len(pins[name]) != 64
           for name in RUNTIME_PIN):
        raise ContractError("FORWARD_PLAN_POLICY_RUNTIME_NOT_PINNED")


def publish_immutable(path, value):
    """Publish complete fsynced bytes without overwriting any existing record."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = canonical(value) + b"\n"
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    temporary = ".registration-" + uuid.uuid4().hex + ".pending"
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Same-directory link is an atomic NOREPLACE publication. A crash
            # can leave a private staging name, never a partial authoritative file.
            os.link(temporary, path.name, src_dir_fd=directory,
                    dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            existing = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                               dir_fd=directory)
            with os.fdopen(existing, "rb") as stream:
                st = os.fstat(stream.fileno())
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                        or st.st_mode & 0o077 or st.st_size != len(raw)
                        or stream.read(len(raw) + 1) != raw):
                    raise ContractError("IMMUTABLE_FORWARD_RECORD_CONFLICT")
        os.unlink(temporary, dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


def _original_quote(config, prefixes, selected):
    """Resolve one already-verified quote to its original bounded receipt bytes."""
    feed = Path(config["feed_directory"])
    if not feed.is_absolute() or feed.resolve() != feed:
        raise ContractError("FORWARD_FEED_PATH_CHANGED")
    candidates = []
    for pin in prefixes:
        if not isinstance(pin, dict) or not isinstance(pin.get("path"), str):
            continue
        path = Path(pin["path"])
        if (path.parent.parent == feed and path.name == "quotes.jsonl"
                and path.parent.name.startswith("segment-")):
            if path.resolve() != path:
                raise ContractError("FORWARD_SOURCE_SYMLINK")
            # Search hint only. Receipt hash and original quote timestamps
            # determine identity/freshness; mtime never grants either.
            candidates.append((path.stat().st_mtime_ns, str(path), pin))
    if len(candidates) > 200:
        raise ContractError("FORWARD_RECEIPT_SEARCH_PATH_BOUND")
    consumed = 0
    for _, name, pin in sorted(candidates, reverse=True):
        length = pin.get("bytes")
        if type(length) is not int or length <= 0:
            raise ContractError("FORWARD_RECEIPT_PREFIX_BOUND")
        consumed += length
        if consumed > MAX_RECEIPT_SEARCH_BYTES:
            raise ContractError("FORWARD_RECEIPT_SEARCH_BYTE_BOUND")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                    or st.st_uid != os.geteuid() or st.st_mode & 0o077
                    or st.st_size < length):
                raise ContractError("FORWARD_RECEIPT_FILE_SCOPE")
            parts = []
            remaining = length
            while remaining:
                part = os.read(fd, min(remaining, 1024 * 1024))
                if not part:
                    raise ContractError("FORWARD_RECEIPT_SHORT_READ")
                parts.append(part)
                remaining -= len(part)
            raw = b"".join(parts)
        finally:
            os.close(fd)
        if hashlib.sha256(raw).hexdigest() != pin.get("sha256"):
            raise ContractError("FORWARD_RECEIPT_PREFIX_CHANGED")
        for line in reversed(raw.splitlines()):
            record = strict(line)
            if record.get("sha256") != selected["receipt"]:
                continue
            if record.get("kind") != "QUOTE":
                raise ContractError("FORWARD_RECEIPT_NOT_QUOTE")
            quote = record["data"]
            if (record["at_ms"] != selected["known_at_ms"]
                    or quote["observed_at_ms"] != selected["t"]
                    or any(Decimal(str(quote[k])) != Decimal(str(selected[k]))
                           for k in ("bid", "ask"))):
                raise ContractError("FORWARD_RECEIPT_QUOTE_CHANGED")
            return quote, pin
    raise ContractError("FORWARD_ORIGINAL_QUOTE_RECEIPT_NOT_FOUND")


def capture_source(config, proposal, now_ms):
    # A provisional hash permits the exact enrolled future reader to validate
    # its pins; this preview is not persisted and consumes no trial/alpha.
    preview = dict(proposal)
    preview["plan_id"] = digest(preview)
    quotes, prefixes, mode = read_for_future_plan(config, preview, now_ms)
    sample, row = forecast_input(config, quotes, now_ms)
    selected = next(q for q in quotes if q["t"] == sample["anchor_ms"])
    quote, pin = _original_quote(config, prefixes, selected)
    proof = {
        "schema": "heptatrader.forward-original-source-admission.v1",
        "period_sha256": config["period_sha256"],
        "instrument": "EUR.USD", "source_read_profile": mode,
        "read_asof_ms": now_ms, "quote": quote,
        "quote_known_at_ms": selected["known_at_ms"],
        "quote_receipt": selected["receipt"], "verified_prefix": pin,
        "source_prefix_digest": digest(prefixes),
        "input_id": digest(row["state"]), "source_ttl_ms": 5000,
        "future_data_availability_proven": False, "broker_queries_sent": 0,
    }
    check_source(config, proof, now_ms)
    return proof


def check_source(config, proof, now_ms):
    if (proof.get("schema") != "heptatrader.forward-original-source-admission.v1"
            or proof.get("period_sha256") != config["period_sha256"]
            or proof.get("instrument") != "EUR.USD"
            or proof.get("source_ttl_ms") != 5000
            or type(proof.get("quote_known_at_ms")) is not int
            or proof["quote_known_at_ms"] > now_ms):
        raise ContractError("FORWARD_SOURCE_SCOPE")
    quote = proof["quote"]
    # Recheck the ORIGINAL expiry at commitment time, including an expiry
    # shorter than 5 seconds. A fresh heartbeat or BTC quote is insufficient.
    valid_quote(quote, now_ms, "EUR.USD")
    if (quote.get("subscription_state") != "active"
            or not quote["observed_at_ms"] <= proof["quote_known_at_ms"]):
        raise ContractError("FORWARD_QUOTE_SUBSCRIPTION_OR_CLOCK")


def _identity(service, job_id, candidate, job):
    check_config(service.c)
    if service.c.get("future_plan_admission_profile") != PROFILE:
        raise ContractError("FORWARD_PLAN_POLICY_NOT_ENROLLED")
    row = service.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if (row is None or row["status"] != "COMPLETED"
            or row["candidate_id"] != candidate.get("candidate_id")
            or json.loads(row["payload"]) != job):
        raise ContractError("FORWARD_COMPLETED_JOB_BINDING")
    manifest = Path(job["output_directory"]) / "candidate.json"
    # candidate_done validated the artifact; bind that same snapshot again
    # before assigning an alpha-bearing identity.
    if (read_json(manifest) != candidate or candidate.get("candidate_id") != digest(
            {k: v for k, v in candidate.items() if k != "candidate_id"})):
        raise ContractError("FORWARD_CANDIDATE_CHANGED_DURING_REGISTRATION")
    return {
        "schema": SCHEMA, "job_id": job_id,
        "candidate_id": candidate["candidate_id"],
        "candidate_manifest_sha256": sha_file(manifest),
        "profile": PROFILE,
        "policy_effective_at_ms": service.c["future_plan_admission_effective_at_ms"],
        "policy_runtime_sha256": {n: service.c["runtime_sha256"][n] for n in RUNTIME_PIN},
        "candidate_window": candidate["window"],
        "training_state": "COMPLETED", "performance_assessment": False,
        "broker_orders_sent": 0,
    }


def _sealed(identity, state, now_ms, **extra):
    record = {**identity, "state": state, "checked_at_ms": now_ms,
              "trial_allocated": state in ("RESERVED", "PUBLISHED"), **extra}
    record["registration_id"] = digest(record)
    return record


def _check_record(record, identity):
    if (not isinstance(record, dict)
            or any(record.get(k) != v for k, v in identity.items())
            or record.get("registration_id") != digest(
                {k: v for k, v in record.items() if k != "registration_id"})
            or record.get("state") not in
                ("DEFERRED", "NO_FORWARD_TRIAL", "RESERVED", "PUBLISHED")
            or record.get("trial_allocated") is not
                (record.get("state") in ("RESERVED", "PUBLISHED"))):
        raise ContractError("FORWARD_REGISTRATION_RECEIPT_BINDING")
    if record["trial_allocated"]:
        plan = record.get("plan", {})
        if (plan.get("plan_id") != digest(
                {k: v for k, v in plan.items() if k != "plan_id"})
                or plan.get("job_id") != identity["job_id"]
                or plan.get("candidate_id") != identity["candidate_id"]
                or plan.get("candidate_manifest_sha256") != identity["candidate_manifest_sha256"]):
            raise ContractError("FORWARD_RESERVED_PLAN_BINDING")


def _unreserved_history(service):
    current = get_meta(service.db, "evaluation_trial", 0)
    trials = [read_json(p)["trial"] for p in
              Path(service.c["evaluation_directory"]).glob("*/plan.json")]
    if (type(current) is not int or current < 0
            or any(type(t) is not int or t < 1 for t in trials)
            or len(set(trials)) != len(trials) or len(trials) != current
            or (trials and (min(trials) != 1 or max(trials) != current))):
        # A missing plan may already have consumed alpha in an older release.
        # Never relabel an unmatched historical allocation "zero consumption".
        raise ContractError("FORWARD_UNMATCHED_HISTORICAL_TRIAL_ALLOCATION")
    for row in service.db.execute(
            "SELECT value FROM meta WHERE key LIKE ?", (KEY_PREFIX + "%",)):
        record = json.loads(row[0])
        if record.get("state") == "RESERVED":
            raise ContractError("FORWARD_UNPUBLISHED_TRIAL_RESERVATION")
    return current


def _publish(service, key, record, candidate, job, folder):
    identity = _identity(service, record["job_id"], candidate, job)
    _check_record(record, identity)
    if record["state"] in ("RESERVED", "PUBLISHED"):
        if get_meta(service.db, "evaluation_trial", 0) < record["plan"]["trial"]:
            raise ContractError("FORWARD_RESERVED_COUNTER_REGRESSION")
        publish_immutable(folder / "plan.json", record["plan"])
        service._write_evaluation_handoff(candidate, job, writer=publish_immutable)
        if record["state"] == "RESERVED":
            published = _sealed(identity, "PUBLISHED", record["checked_at_ms"],
                                plan=record["plan"])
            if service.db.in_transaction:
                raise ContractError("FORWARD_PUBLICATION_TRANSACTION_SCOPE")
            service.db.execute("BEGIN IMMEDIATE")
            try:
                # Acquire the write lock BEFORE comparing the reservation.
                current_record = get_meta(service.db, key)
                if current_record not in (record, published):
                    raise ContractError("FORWARD_RESERVATION_CHANGED")
                put_meta(service.db, key, published)
                service.db.commit()
            except BaseException:
                service.db.rollback()
                raise
            record = published
    elif record["state"] == "NO_FORWARD_TRIAL":
        publish_immutable(folder / "no-forward-trial.json", record)
    else:
        atomic(folder / "registration-status.json", record)
    return record


def register(service, job_id, candidate, job, *, clock=None):
    """Allocate once; replay a reserved original plan even after its time passes."""
    now = (lambda: int(time.time() * 1000)) if clock is None else clock
    folder = Path(service.c["evaluation_directory"]) / job_id
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "plan.json"
    key = KEY_PREFIX + job_id
    if service.db.in_transaction:
        raise ContractError("FORWARD_REGISTRATION_TRANSACTION_SCOPE")
    service.db.execute("BEGIN IMMEDIATE")
    try:
        identity = _identity(service, job_id, candidate, job)
        previous = get_meta(service.db, key)
        if path.exists():
            old = read_json(path)
            if old["candidate_id"] != candidate["candidate_id"]:
                raise ContractError("EVALUATION_CANDIDATE_CONFLICT")
            if previous is not None and previous.get("state") in ("RESERVED", "PUBLISHED"):
                _check_record(previous, identity)
                if old != previous["plan"]:
                    raise ContractError("FORWARD_RESERVED_PLAN_CONFLICT")
                service.db.commit()
                return _publish(service, key, previous, candidate, job, folder)
            service.db.commit()
            return {"state": "EXISTING_IMMUTABLE_PLAN", "plan_id": old["plan_id"]}
        if previous is not None:
            _check_record(previous, identity)
        elif (folder / "no-forward-trial.json").exists():
            # DB commits always precede publication; an orphan is not an
            # authorized zero-alpha decision and cannot be silently adopted.
            raise ContractError("FORWARD_UNBOUND_TERMINAL_RECEIPT")
        if previous is not None and previous["state"] != "DEFERRED":
            record = previous
        else:
            checked = now()
            if type(checked) is not int or checked < identity["policy_effective_at_ms"]:
                raise ContractError("FORWARD_PLAN_POLICY_CLOCK_REGRESSION")
            current = _unreserved_history(service)
            if inspect_queue(service.c)["pending_original_trials"]:
                record = _sealed(identity, "DEFERRED", checked,
                                 reason="PRIOR_ORIGINAL_TRIAL_PENDING")
            else:
                proposal = service._evaluation_plan(
                    job_id, candidate, job, current + 1, checked)
                decision = calendar_admission(candidate, proposal, checked)
                source = None
                if decision["ready"]:
                    try:
                        source = capture_source(service.c, proposal, checked)
                    except (ContractError, ValueError, OSError, KeyError, StopIteration) as exc:
                        decision = {**decision, "ready": False, "state": "DEFERRED",
                                    "reason": "FORWARD_ORIGINAL_SOURCE_UNREADY",
                                    "source_error": str(exc)[:160]}
                    # The source read may consume time or straddle a session edge.
                    # Recompute the actual frozen proposal and all calendar checks
                    # after it, then validate the original quote's current expiry.
                    after_read = now()
                    if type(after_read) is not int or after_read < checked:
                        raise ContractError("FORWARD_REGISTRATION_CLOCK_REGRESSION")
                    checked = after_read
                    proposal = service._evaluation_plan(
                        job_id, candidate, job, current + 1, checked)
                    fresh_decision = calendar_admission(candidate, proposal, checked)
                    if not fresh_decision["ready"]:
                        decision = fresh_decision
                    elif source is not None:
                        try:
                            check_source(service.c, source, checked)
                            decision = fresh_decision
                        except (ContractError, ValueError, KeyError) as exc:
                            decision = {**fresh_decision, "ready": False, "state": "DEFERRED",
                                        "reason": "FORWARD_ORIGINAL_SOURCE_UNREADY",
                                        "source_error": str(exc)[:160]}
                if decision["ready"]:
                    if source is None:
                        raise ContractError("FORWARD_ORIGINAL_SOURCE_PROOF_MISSING")
                    if _identity(service, job_id, candidate, job) != identity:
                        raise ContractError("FORWARD_CANDIDATE_CHANGED_DURING_REGISTRATION")
                    proposal["registration_admission"] = {
                        "profile": PROFILE, "policy_effective_at_ms": identity["policy_effective_at_ms"],
                        "policy_runtime_sha256": identity["policy_runtime_sha256"],
                        "checked_at_ms": checked, "calendar": decision, "source": source,
                        "candidate_window_bound_is_new_admission_rule": True,
                        "future_data_availability_proven": False,
                    }
                    proposal["plan_id"] = digest(proposal)
                    record = _sealed(identity, "RESERVED", checked, plan=proposal)
                    put_meta(service.db, "evaluation_trial", current + 1)
                else:
                    record = _sealed(identity, decision["state"], checked,
                                     reason=decision["reason"], calendar=decision)
            put_meta(service.db, key, record)
        service.db.commit()
    except BaseException:
        service.db.rollback()
        raise
    return _publish(service, key, record, candidate, job, folder)


def resume_reservations(service):
    """Finish DB-first publication before the queue can skip an existing plan."""
    records = []
    for row in service.db.execute(
            "SELECT key,value FROM meta WHERE substr(key,1,?)=?",
            (len(KEY_PREFIX), KEY_PREFIX)).fetchall():
        record = json.loads(row["value"])
        if not isinstance(record, dict):
            raise ContractError("FORWARD_REGISTRATION_RECORD_TYPE")
        if record.get("state") != "RESERVED":
            continue
        if row["key"] != KEY_PREFIX + str(record.get("job_id")):
            raise ContractError("FORWARD_RESERVATION_KEY_BINDING")
        records.append(record)
    if len(records) > 1:
        raise ContractError("FORWARD_MULTIPLE_UNPUBLISHED_RESERVATIONS")
    for record in records:
        row = service.db.execute(
            "SELECT * FROM jobs WHERE id=? AND status='COMPLETED'",
            (record["job_id"],)).fetchone()
        if row is None or not service.candidate_done(row):
            raise ContractError("FORWARD_RESERVED_CANDIDATE_MISSING")
    return len(records)


def waiting_candidates(service, rows):
    """Only a fully written, bound terminal receipt releases a trained artifact."""
    waiting = []
    terminal = 0
    reasons = []
    for row in rows:
        folder = Path(service.c["evaluation_directory"]) / row["id"]
        if (folder / "plan.json").exists():
            continue
        key = KEY_PREFIX + row["id"]
        record = get_meta(service.db, key)
        if record is None:
            waiting.append(row)
            continue
        job = json.loads(row["payload"])
        candidate = read_json(Path(job["output_directory"]) / "candidate.json")
        identity = _identity(service, row["id"], candidate, job)
        _check_record(record, identity)
        if record["state"] == "NO_FORWARD_TRIAL":
            receipt_path = folder / "no-forward-trial.json"
            if receipt_path.exists():
                if read_json(receipt_path) != record:
                    raise ContractError("FORWARD_TERMINAL_RECEIPT_CHANGED")
                terminal += 1
                continue
            # DB alone cannot hide a candidate; recover its immutable file first.
            reasons.append("TERMINAL_RECEIPT_PUBLICATION_PENDING")
        else:
            reasons.append(record.get("reason", "RESERVED_PLAN_PUBLICATION_PENDING"))
        waiting.append(row)
    return waiting, {
        "completed_candidates_without_forward_trial": terminal,
        "forward_registration_wait_reasons": sorted(set(reasons)),
        "forward_plan_admission_profile": PROFILE,
        "no_forward_trial_is_not_performance_rejection": True,
    }
