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
from .resident_queue import inspect as inspect_queue, registration_policy, OVERLAP_PROFILE
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
POLICY_KEYS = ('profile', 'policy_effective_at_ms', 'policy_runtime_sha256')


def registration_key(job_id, attempt=None):
    if attempt is None:
        return KEY_PREFIX + job_id
    from .resident_forward_attempt import validate_shape
    validate_shape(attempt)
    if attempt['job_id'] != job_id:
        raise ContractError('FORWARD_ATTEMPT_JOB_BINDING')
    return KEY_PREFIX + job_id + ':attempt:' + attempt['attempt_id']


def registration_folder(config, job_id, attempt=None):
    registration_key(job_id, attempt)
    name = job_id if attempt is None else 'attempt-' + attempt['attempt_id']
    return Path(config['evaluation_directory']) / name


def policy_identity(config):
    return {'profile': config.get('future_plan_admission_profile'),
            'policy_effective_at_ms': config.get('future_plan_admission_effective_at_ms'),
            'policy_runtime_sha256': {n: config['runtime_sha256'][n] for n in RUNTIME_PIN}}


def retained_config(service, record):
    """Resolve the original policy; this grants no new decision authority."""
    for entry in service.c.get('retained_registration_configs', []):
        path = Path(entry['path'])
        if sha_file(path) != entry['sha256']:
            raise ContractError('RETAINED_REGISTRATION_CONFIG_CHANGED')
        old = read_json(path)
        check_config(old)
        if any(old.get(k) != service.c.get(k) for k in
               ('period_sha256', 'evaluation_directory', 'control_directory')):
            raise ContractError('RETAINED_REGISTRATION_SCOPE_CHANGED')
        if not original_policy_required(old, record):
            return entry, old
    raise ContractError('FROZEN_REGISTRATION_POLICY_CONFIG_REQUIRED')


def original_policy_required(config, record):
    if any(record.get(k) != policy_identity(config)[k] for k in POLICY_KEYS):
        return True
    if record.get('evaluation_attempt') is not None:
        from .resident_forward_attempt import RUNTIME_PIN as ATTEMPT_RUNTIME_PIN
        return record.get('attempt_runtime_sha256') != config['runtime_sha256'].get(ATTEMPT_RUNTIME_PIN)
    return False


def resume_retained_deferred(service, job_id, candidate, job, operation):
    """New decisions for old DEFERRED jobs execute only their pinned code."""
    previous = get_meta(service.db, KEY_PREFIX + job_id)
    if (not isinstance(previous, dict) or previous.get('state') != 'DEFERRED'
            or not original_policy_required(service.c, previous)):
        return None
    if service.db.in_transaction:
        raise ContractError('RETAINED_REGISTRATION_TRANSACTION_SCOPE')
    _check_record(previous, _identity(service, job_id, candidate, job))
    entry, _ = retained_config(service, previous)
    from .resident_retained_registration import run_original
    result = run_original(service.c, entry, job_id, previous['registration_id'], operation)
    waits = getattr(service, '_retained_registration_wait_reasons', None)
    if waits is None:
        service._retained_registration_wait_reasons = waits = {}
    if result['state'] == 'ORIGINAL_FORWARD_RESOURCE_GUARD_REQUIRED':
        waits[job_id] = result['state']
    elif operation == 'register':
        waits.pop(job_id, None)
    record = get_meta(service.db, KEY_PREFIX + job_id)
    _check_record(record, _identity(service, job_id, candidate, job))
    if record['state'] != 'DEFERRED':
        waits.pop(job_id, None)
    return record


def check_config(config):
    profile = config.get("future_plan_admission_profile")
    if profile is None:
        return
    if profile != PROFILE:
        raise ContractError("UNREVIEWED_FORWARD_PLAN_ADMISSION_PROFILE")
    effective = config.get("future_plan_admission_effective_at_ms")
    if type(effective) is not int or effective <= 0:
        raise ContractError("FORWARD_PLAN_POLICY_EFFECTIVE_TIME_REQUIRED")
    retained = config.get('retained_registration_configs', [])
    if (type(retained) is not list or len(retained) > 32
            or any(type(e) is not dict or set(e) != {'path', 'sha256'}
                   or type(e['path']) is not str or not Path(e['path']).is_absolute()
                   or type(e['sha256']) is not str or len(e['sha256']) != 64
                   for e in retained)):
        raise ContractError('RETAINED_REGISTRATION_CONFIG_LIST')
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


def _identity(service, job_id, candidate, job, *, attempt=None):
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
    identity = {
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
    if attempt is not None:
        from .resident_forward_attempt import check_request,RUNTIME_PIN as ATTEMPT_RUNTIME_PIN
        original_candidate, original_job, _ = check_request(service, attempt)
        if original_candidate != candidate or original_job != job or attempt['job_id'] != job_id:
            raise ContractError('FORWARD_ATTEMPT_ORIGINAL_ARTIFACT_BINDING')
        identity['evaluation_attempt'] = attempt
        identity['attempt_runtime_sha256'] = service.c['runtime_sha256'][ATTEMPT_RUNTIME_PIN]
    # Reading an old receipt preserves its policy identity. A DEFERRED record
    # can receive a new decision only through resume_retained_deferred, which
    # executes the actual original runtime instead of impersonating its pins.
    previous = get_meta(service.db, registration_key(job_id, attempt))
    if (isinstance(previous, dict)
            and previous.get('state') in ('DEFERRED', 'RESERVED', 'PUBLISHED', 'NO_FORWARD_TRIAL')
            and original_policy_required(service.c, previous)):
        _, old = retained_config(service, previous)
        identity.update(policy_identity(old))
        if attempt is not None:
            identity['attempt_runtime_sha256'] = old['runtime_sha256'][ATTEMPT_RUNTIME_PIN]
    from . import resident_training_v5 as training_v5
    from . import resident_training_v6 as training_v6
    from . import resident_training_v7 as training_v7
    from . import resident_training_v8 as training_v8
    policy = training_v8 if training_v8.enabled(candidate.get("objective")) else training_v7 if training_v7.enabled(candidate.get("objective")) else training_v6 if training_v6.enabled(candidate.get("objective")) else training_v5
    if policy.enabled(candidate.get("objective")):
        from .resident_price_calibration import validate
        calibration = validate(candidate.get("price_calibration"))
        if (calibration["output_profile"] != policy.PRICE_OUTPUT or
                candidate.get("price_output_profile") != policy.PRICE_OUTPUT or
                candidate.get("closed_action_source_id") != job.get("closed_action_source_id") or
                calibration["parent_id"] != job["parent_id"] or
                calibration["adapter_sha256"] != candidate["adapter_sha256"]):
            raise ContractError("FORWARD_V5_CANDIDATE_BINDING")
        identity.update(price_output_profile=policy.PRICE_OUTPUT,
                        price_calibration_profile_id=calibration["profile_id"],
                        price_calibration=calibration,
                        closed_action_source_id=candidate["closed_action_source_id"],
                        action_distribution_scope=candidate["action_training_profile"]["action_distribution_scope"])
    if training_v7.enabled(candidate.get('objective')) or training_v8.enabled(candidate.get('objective')):
        from .resident_price_input import check_bindings, bindings
        from .integrity import validate_candidate_artifact
        if check_bindings(candidate) != bindings(validate_candidate_artifact(Path(job['parent_directory'])),objective_schema=policy.SCHEMA):
            raise ContractError('V7_REGISTRATION_PARENT_INPUT_PROFILE')
        identity.update(check_bindings(candidate))
    from .resident_parent_calibration import plan_binding
    identity.update(plan_binding(job, candidate))
    return identity


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
                (record.get("state") in ("RESERVED", "PUBLISHED"))
            or record.get('evaluation_attempt') != identity.get('evaluation_attempt')):
        raise ContractError("FORWARD_REGISTRATION_RECEIPT_BINDING")
    if record.get('evaluation_attempt') is not None:
        from .resident_forward_attempt import validate_shape
        validate_shape(record['evaluation_attempt'])
        pin = record.get('attempt_runtime_sha256')
        if not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
            raise ContractError('FORWARD_ATTEMPT_RUNTIME_RECEIPT')
    elif record.get('attempt_runtime_sha256') is not None:
        raise ContractError('FORWARD_ATTEMPT_RUNTIME_RECEIPT')
    if record["trial_allocated"]:
        plan = record.get("plan", {})
        if (plan.get("plan_id") != digest(
                {k: v for k, v in plan.items() if k != "plan_id"})
                or plan.get("job_id") != identity["job_id"]
                or plan.get("candidate_id") != identity["candidate_id"]
                or plan.get("candidate_manifest_sha256") != identity["candidate_manifest_sha256"]
                or plan.get('evaluation_attempt') != identity.get('evaluation_attempt')
                or plan.get('attempt_runtime_sha256') != record.get('attempt_runtime_sha256')):
            raise ContractError("FORWARD_RESERVED_PLAN_BINDING")
        for name in ("price_output_profile", "price_calibration_profile_id", "price_calibration", "closed_action_source_id", "action_distribution_scope", "price_input_objective", "price_input_profile", "parent_price_input_profile", "action_input_profile", "price_input_spec", "parent_calibration_comparator_profile", "parent_calibration_comparator"):
            if name in identity and plan.get(name) != identity[name]:
                raise ContractError("FORWARD_V5_RESERVED_PROFILE_CHANGED")


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
    attempt = record.get('evaluation_attempt')
    identity = _identity(service, record["job_id"], candidate, job, attempt=attempt)
    _check_record(record, identity)
    if (key != registration_key(record['job_id'], attempt)
            or folder != registration_folder(service.c, record['job_id'], attempt)):
        raise ContractError('FORWARD_REGISTRATION_PUBLICATION_SCOPE')
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
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(folder / "registration-status.json", record)
    return record


def _exact_required_prior(candidate):
    """A prospective exact tie cannot meet an unchanged strict value floor."""
    from . import resident_training_v6 as v6
    from . import resident_training_v7 as v7
    from . import resident_training_v8 as v8
    policy = v8 if v8.enabled(candidate.get('objective')) else v7 if v7.enabled(candidate.get('objective')) else v6
    if not policy.enabled(candidate.get('objective')):
        return False
    policy.check_objective(candidate['objective'])
    if policy is v8:
        from . import resident_price_calibration_v3 as calibration
    else:
        from . import resident_price_calibration_v2 as calibration
    from . import resident_price_baselines as baselines
    profile = calibration.validate(candidate.get('price_calibration'))
    reference = baselines.validate(candidate.get('price_baselines'))
    return (profile['model_weight'] == 0
            and profile['price_baseline_profile_id'] == reference['profile_id']
            and profile['train_prior_probs'] == reference['train_prior_by_horizon']['300']['probs']
            and 'train_prior' in baselines.VALUE_RULE['comparators']
            and baselines.VALUE_RULE['minimum_mean_improvement'] > 0)


def register(service, job_id, candidate, job, *, clock=None, attempt=None):
    """Allocate once; replay a reserved original plan even after its time passes."""
    now = (lambda: int(time.time() * 1000)) if clock is None else clock
    folder = registration_folder(service.c, job_id, attempt)
    if attempt is None:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "plan.json"
    key = registration_key(job_id, attempt)
    retained = (resume_retained_deferred(service, job_id, candidate, job, 'register')
                if attempt is None else None)
    if retained is not None:
        return (retained if retained['state'] == 'DEFERRED'
                else _publish(service, key, retained, candidate, job, folder))
    if service.db.in_transaction:
        raise ContractError("FORWARD_REGISTRATION_TRANSACTION_SCOPE")
    service.db.execute("BEGIN IMMEDIATE")
    try:
        identity = _identity(service, job_id, candidate, job, attempt=attempt)
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
            if attempt is not None:
                raise ContractError('FORWARD_ATTEMPT_UNBOUND_PLAN')
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
            additional = None
            if attempt is not None:
                if previous is not None and original_policy_required(service.c, previous):
                    raise ContractError('FORWARD_ATTEMPT_FROZEN_REGISTRAR_REQUIRED')
                from .resident_forward_attempt import readiness
                additional = readiness(service, attempt, checked, job=job)
                if not additional['ready']:
                    # A prepared request is not a scheduled attempt. Before the
                    # prior cutoff/closeout, create no directory, record or debit.
                    service.db.commit()
                    return {'state': 'DEFERRED', 'trial_allocated': False, **additional}
                if getattr(service, '_guarded_forward_registration', False) is not True:
                    raise ContractError('FORWARD_ATTEMPT_ORIGINAL_GUARD_REQUIRED')
                from .resident_budget import inspect as inspect_storage
                storage = inspect_storage(service.c)
                service.storage = storage
                if not storage['admission_ready']:
                    service.db.commit()
                    return {'state': 'DEFERRED', 'trial_allocated': False,
                            'reason': storage['blocked_reason'], 'storage': storage}
            # A current-config rollback cannot lower a frozen V3 job's floor.
            # Existing plans and reservations were handled above; this check
            # applies only to a new admission decision and never reallocates them.
            from .promotion import protocol_for_job
            from .promotion_power import MATCHED_PARENT_PROTOCOL
            if protocol_for_job(service.c, job) == MATCHED_PARENT_PROTOCOL:
                service._check_power_trial_history(job)
            current = _unreserved_history(service)
            queue = inspect_queue(service.c)
            created = service.db.execute('SELECT created_ms FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
            admission_queue = registration_policy(queue, service.c, job, created)
            if _exact_required_prior(candidate):
                record = _sealed(identity, "NO_FORWARD_TRIAL", checked,
                                 reason="EXACT_REQUIRED_PRIOR_NO_POSSIBLE_STRICT_GAIN")
            elif admission_queue['blocking_trials']:
                record = _sealed(identity, "DEFERRED", checked,
                                 reason="PRIOR_ORIGINAL_TRIAL_PENDING")
            else:
                proposal = service._evaluation_plan(
                    job_id, candidate, job, current + 1, checked)
                if attempt is not None:
                    proposal['evaluation_attempt'] = attempt
                    proposal['attempt_runtime_sha256'] = identity['attempt_runtime_sha256']
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
                    if attempt is not None:
                        proposal['evaluation_attempt'] = attempt
                        proposal['attempt_runtime_sha256'] = identity['attempt_runtime_sha256']
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
                    if _identity(service, job_id, candidate, job, attempt=attempt) != identity:
                        raise ContractError("FORWARD_CANDIDATE_CHANGED_DURING_REGISTRATION")
                    proposal["registration_admission"] = {
                        "profile": PROFILE, "policy_effective_at_ms": identity["policy_effective_at_ms"],
                        "policy_runtime_sha256": identity["policy_runtime_sha256"],
                        "checked_at_ms": checked, "calendar": decision, "source": source,
                        "candidate_window_bound_is_new_admission_rule": True,
                        "future_data_availability_proven": False,
                    }
                    if admission_queue['profile'] == OVERLAP_PROFILE:
                        proposal['registration_admission']['trial_queue'] = admission_queue
                    if additional is not None:
                        proposal['registration_admission']['additional_attempt'] = additional
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
        if row["key"] != registration_key(str(record.get("job_id")), record.get('evaluation_attempt')):
            raise ContractError("FORWARD_RESERVATION_KEY_BINDING")
        records.append(record)
    if len(records) > 1:
        raise ContractError("FORWARD_MULTIPLE_UNPUBLISHED_RESERVATIONS")
    for record in records:
        row = service.db.execute(
            "SELECT * FROM jobs WHERE id=? AND status='COMPLETED'",
            (record["job_id"],)).fetchone()
        if row is None:
            raise ContractError("FORWARD_RESERVED_CANDIDATE_MISSING")
        attempt = record.get('evaluation_attempt')
        if attempt is None:
            if not service.candidate_done(row):
                raise ContractError("FORWARD_RESERVED_CANDIDATE_MISSING")
        else:
            from .resident_forward_attempt import check_request
            candidate, job, _ = check_request(service, attempt)
            service.register_evaluation(record['job_id'], candidate, job, attempt=attempt)
    return len(records)


def waiting_candidates(service, rows):
    """Only a fully written, bound terminal receipt releases a trained artifact."""
    waiting = []
    terminal = 0
    retained_waiting = 0
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
        if record['state'] == 'DEFERRED' and original_policy_required(service.c, record):
            # Keep visible and serial under its original runtime. It is not a
            # newly trained candidate occupying the prospective overlap slot.
            from .resident_queue import overlap_enrolled
            if not overlap_enrolled(service.c, job, row['created_ms']):
                retained_waiting += 1
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
        "retained_deferred_training_candidates": retained_waiting,
        "forward_registration_wait_reasons": sorted(set(reasons)),
        "forward_plan_admission_profile": PROFILE,
        "no_forward_trial_is_not_performance_rejection": True,
    }
