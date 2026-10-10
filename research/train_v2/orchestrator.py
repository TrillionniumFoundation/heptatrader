"""Event-triggered, non-executing research admission for Train V2.

A source event does not launch a model; an outcome event only makes a bounded
candidate-training REVIEW possible. No timers, threads, broker API or promotion.
Existing signed producer/Agentd owner trust is NOT inferred from local hashes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

from .contracts import (
    CAUSAL, DATASET_SCHEMA, ContractError, digest, feature_spec_id, is_digest,
)

PLAN_SCHEMA = "heptatrader.train-v2-event-plan.v1"
EVENT_SCHEMA = "heptatrader.train-v2-evidence-event.v1"
SOURCE_EVENT = "SOURCE_SEALED"
OUTCOME_EVENT = "OUTCOME_SEALED"
CORRECTION_EVENT = "CORRECTION_REVOKES"
KINDS = (SOURCE_EVENT, OUTCOME_EVENT, CORRECTION_EVENT)


def register_plan(*, spec, registered_at_ms, first_decision_ms,
                  last_decision_ms, minimum_mature_rows=16):
    spec_id = feature_spec_id(spec)
    if (spec["mode"] != CAUSAL
            or type(registered_at_ms) is not int or type(first_decision_ms) is not int
            or type(last_decision_ms) is not int
            or not registered_at_ms < first_decision_ms <= last_decision_ms
            or type(minimum_mature_rows) is not int or not 1 <= minimum_mature_rows <= 64):
        raise ContractError("TRAIN_PLAN_WINDOW_OR_CAUSAL_SCOPE")
    body = {
        "schema": PLAN_SCHEMA,
        "feature_spec_id": spec_id,
        "period_sha256": spec["period_sha256"],
        "behavior_policy_id": spec["behavior_policy_id"],
        "registered_at_ms": registered_at_ms,
        "first_decision_ms": first_decision_ms,
        "last_decision_ms": last_decision_ms,
        "minimum_mature_rows": minimum_mature_rows,
        "executor": "NONE_RESEARCH_REVIEW_ONLY",
        "automatic_model_training": False,
        "broker_orders_sent": 0,
        "trade_authority": False,
        "actor_update": False,
        "promotion_allowed": False,
    }
    return {**body, "plan_id": digest(body)}


def evidence_event(plan, example, *, kind, at_ms, evidence_sha256):
    if kind not in KINDS or not is_digest(evidence_sha256) or type(at_ms) is not int:
        raise ContractError("TRAIN_EVENT_SHAPE")
    body = {
        "schema": EVENT_SCHEMA,
        "plan_id": plan["plan_id"],
        "feature_id": example["feature_id"],
        "kind": kind,
        "at_ms": at_ms,
        "evidence_sha256": evidence_sha256,
        "trade_authority": False,
        "broker_orders_sent": 0,
    }
    return {**body, "event_id": digest(body)}


def verify_plan(plan):
    if (not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA
            or not is_digest(plan.get("plan_id"))
            or digest({k: v for k, v in plan.items() if k != "plan_id"}) != plan["plan_id"]
            or not is_digest(plan.get("feature_spec_id"))
            or not is_digest(plan.get("period_sha256"))
            or plan.get("executor") != "NONE_RESEARCH_REVIEW_ONLY"
            or any(plan.get(k) is not False for k in
                   ("automatic_model_training", "trade_authority", "actor_update", "promotion_allowed"))
            or plan.get("broker_orders_sent") != 0):
        raise ContractError("TRAIN_PLAN_NOT_RESEARCH_ONLY")
    return plan


def verify_event(event):
    if (not isinstance(event, dict) or event.get("schema") != EVENT_SCHEMA
            or event.get("kind") not in KINDS
            or not is_digest(event.get("event_id"))
            or not is_digest(event.get("feature_id"))
            or not is_digest(event.get("evidence_sha256"))
            or type(event.get("at_ms")) is not int
            or event.get("trade_authority") is not False
            or event.get("broker_orders_sent") != 0
            or digest({k: v for k, v in event.items() if k != "event_id"}) != event["event_id"]):
        raise ContractError("TRAIN_EVENT_IDENTITY")
    return event


def _private_storage(db_file):
    path = Path(db_file)
    parent = path.parent
    if parent.is_symlink():
        raise ContractError("TRAIN_STORAGE_SYMLINK")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = parent.stat()
    if (not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid()
            or mode.st_mode & 0o077):
        raise ContractError("TRAIN_STORAGE_NOT_PRIVATE")
    if path.is_symlink():
        raise ContractError("TRAIN_DB_SYMLINK")
    # O_CREAT without O_EXCL allows simultaneous event writers to converge on
    # one ledger; SQLite BEGIN IMMEDIATE then serializes actual admission.
    fd = os.open(path, os.O_CREAT | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    os.close(fd)
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ContractError("TRAIN_DB_NOT_PRIVATE")
    return path


def database(db_file):
    path = _private_storage(db_file)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS plans (
        plan_id TEXT PRIMARY KEY, body TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, feature_id TEXT NOT NULL,
        kind TEXT NOT NULL, at_ms INTEGER NOT NULL, body TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS samples (
        plan_id TEXT NOT NULL, feature_id TEXT NOT NULL,
        input_digest TEXT NOT NULL, state TEXT NOT NULL, source_event_id TEXT NOT NULL,
        outcome_event_id TEXT, PRIMARY KEY(plan_id, feature_id)
    );
    CREATE TABLE IF NOT EXISTS review_batches (
        fingerprint TEXT PRIMARY KEY, plan_id TEXT NOT NULL, sample_count INTEGER NOT NULL,
        state TEXT NOT NULL
    );
    """)
    return db


def _validate_dataset(plan, dataset, event):
    if (not isinstance(dataset, dict) or dataset.get("schema") != DATASET_SCHEMA
            or dataset.get("dataset_id") != digest({k: v for k, v in dataset.items() if k != "dataset_id"})
            or dataset.get("dataset_role") != CAUSAL
            or dataset.get("feature_spec_id") != plan["feature_spec_id"]
            or dataset.get("feature_spec_id") != feature_spec_id(dataset.get("feature_spec"))
            or dataset["feature_spec"]["period_sha256"] != plan["period_sha256"]
            or dataset["feature_spec"]["behavior_policy_id"] != plan["behavior_policy_id"]
            or dataset.get("promotion_allowed") is not False
            or dataset.get("broker_orders_sent") != 0):
        raise ContractError("TRAIN_DATASET_SCOPE")
    found = [x for x in dataset.get("examples", [])
             if x.get("feature_id") == event["feature_id"]]
    if len(found) != 1:
        raise ContractError("TRAIN_EVENT_FEATURE_NOT_FOUND")
    example = found[0]
    if (example.get("locally_point_in_time") is not True
            or example.get("revised_historical") is not False
            or example.get("trade_authority") is not False
            or digest(example["features"]) != example["feature_id"]):
        raise ContractError("TRAIN_UNVERIFIED_CAUSAL_FEATURE")
    decision = example["features"]["decision_at_ms"]
    if not plan["first_decision_ms"] <= decision <= plan["last_decision_ms"]:
        raise ContractError("TRAIN_OUTSIDE_PREDECLARED_DECISION")
    inputs = example["features"].get("input_receipts")
    spec = dataset["feature_spec"]
    if not isinstance(inputs, list) or len(inputs) != spec["lookback"]:
        raise ContractError("TRAIN_INPUT_WITNESS_COUNT")
    previous = None
    for witness in inputs:
        if (not is_digest(witness.get("source_id"))
                or not is_digest(witness.get("source_sha256"))
                or type(witness.get("event_at_ms")) is not int
                or type(witness.get("available_at_ms")) is not int
                or not 0 < witness["event_at_ms"] <= witness["available_at_ms"] <= decision):
            raise ContractError("TRAIN_INPUT_WITNESS_NOT_CAUSAL")
        if previous is not None and not 0 < witness["event_at_ms"] - previous <= spec["max_gap_ms"]:
            raise ContractError("TRAIN_INPUT_WITNESS_GAP")
        previous = witness["event_at_ms"]
    if decision - previous > spec["max_age_ms"]:
        raise ContractError("TRAIN_INPUT_WITNESS_STALE")
    if example.get("label_due_ms") != decision + spec["horizon_ms"]:
        raise ContractError("TRAIN_LABEL_DUE_CHANGED")
    label = example.get("label_receipt")
    if example.get("label_status") == "OBSERVED":
        if (example.get("candidate_training_eligible") is not True
                or not isinstance(label, dict)
                or example.get("label_return_bps") is None
                or not is_digest(label.get("source_id"))
                or not is_digest(label.get("source_sha256"))
                or type(label.get("event_at_ms")) is not int
                or type(label.get("available_at_ms")) is not int
                or not example["label_due_ms"] <= label["event_at_ms"] <=
                    example["label_due_ms"] + spec["label_tolerance_ms"]
                or not label["event_at_ms"] <= label["available_at_ms"] <= dataset["evaluated_at_ms"]):
            raise ContractError("TRAIN_LABEL_WITNESS_NOT_MATURE")
    elif (example.get("candidate_training_eligible") is not False
          or label is not None or example.get("label_return_bps") is not None):
        raise ContractError("TRAIN_UNOBSERVED_LABEL_MUST_BE_EMPTY")
    return example


def ingest(db_file, plan, dataset, event):
    """Persist exactly one event and change review state; no model dispatch.

    The caller must independently verify the actual source file / signed witness
    before invoking this local queue. Plain event hashes are NOT signatures.
    """
    verify_plan(plan)
    verify_event(event)
    if event["plan_id"] != plan["plan_id"]:
        raise ContractError("TRAIN_CROSS_PLAN_EVENT")
    example = _validate_dataset(plan, dataset, event)
    kind = event["kind"]
    now = event["at_ms"]
    decision = example["features"]["decision_at_ms"]
    if now <= plan["registered_at_ms"]:
        raise ContractError("TRAIN_RETROACTIVE_EVENT")
    if kind == SOURCE_EVENT:
        receipt_ids = example["features"]["input_receipts"]
        if event["evidence_sha256"] not in [x["source_sha256"] for x in receipt_ids]:
            raise ContractError("TRAIN_SOURCE_RECEIPT_MISMATCH")
        if not max(x["available_at_ms"] for x in receipt_ids) <= now <= decision:
            raise ContractError("TRAIN_SOURCE_NOT_EARLY_ENOUGH")
    elif kind == OUTCOME_EVENT:
        if (example.get("label_status") != "OBSERVED"
                or example.get("candidate_training_eligible") is not True
                or not example.get("label_receipt")
                or event["evidence_sha256"] != example["label_receipt"]["source_sha256"]
                or not example["label_due_ms"] <= now
                or not example["label_receipt"]["available_at_ms"] <= now):
            raise ContractError("TRAIN_LABEL_NOT_MATURE_OR_VERIFIED")
    else:
        if now < decision:
            raise ContractError("TRAIN_CORRECTION_BEFORE_DECISION")
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    db = database(db_file)
    try:
        db.execute("BEGIN IMMEDIATE")
        oldplan = db.execute("SELECT body FROM plans WHERE plan_id=?", (plan["plan_id"],)).fetchone()
        plan_payload = json.dumps(plan, sort_keys=True, separators=(",", ":"))
        if oldplan and oldplan[0] != plan_payload:
            raise ContractError("TRAIN_PLAN_MUTATED")
        db.execute("INSERT OR IGNORE INTO plans VALUES (?,?)", (plan["plan_id"], plan_payload))
        seen = db.execute("SELECT body FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
        if seen:
            if seen[0] != payload:
                raise ContractError("TRAIN_EVENT_CHANGED")
            db.execute("COMMIT")
            return {"state": "DUPLICATE_NO_EFFECT", "event_id": event["event_id"],
                    "automatic_training": False, "broker_orders_sent": 0}
        sample = db.execute(
            "SELECT input_digest,state FROM samples WHERE plan_id=? AND feature_id=?",
            (plan["plan_id"], event["feature_id"])
        ).fetchone()
        if sample and sample[0] != example["feature_id"]:
            raise ContractError("TRAIN_FEATURE_ID_CHANGED")
        if kind == SOURCE_EVENT:
            if sample:
                raise ContractError("TRAIN_SAMPLE_ALREADY_ADMITTED")
            db.execute("INSERT INTO samples VALUES (?,?,?,?,?,NULL)",
                       (plan["plan_id"], event["feature_id"], example["feature_id"],
                        "AWAITING_OUTCOME", event["event_id"]))
            state = "INPUT_RECORDED_SHADOW_ONLY"
        elif kind == OUTCOME_EVENT:
            if not sample or sample[1] != "AWAITING_OUTCOME":
                raise ContractError("TRAIN_ORIGINAL_CAUSAL_INPUT_NOT_SEALED")
            db.execute(
                "UPDATE samples SET state='MATURED_RESEARCH_ONLY',outcome_event_id=? "
                "WHERE plan_id=? AND feature_id=?",
                (event["event_id"], plan["plan_id"], event["feature_id"])
            )
            count = db.execute(
                "SELECT count(*) FROM samples WHERE plan_id=? AND state='MATURED_RESEARCH_ONLY'",
                (plan["plan_id"],)
            ).fetchone()[0]
            if count >= plan["minimum_mature_rows"]:
                rows = db.execute(
                    "SELECT feature_id,outcome_event_id FROM samples "
                    "WHERE plan_id=? AND state='MATURED_RESEARCH_ONLY' ORDER BY feature_id",
                    (plan["plan_id"],)
                ).fetchall()
                frozen = rows[:plan["minimum_mature_rows"]]
                fingerprint = digest({"plan": plan["plan_id"], "mature": frozen})
                db.execute(
                    "INSERT OR IGNORE INTO review_batches VALUES (?,?,?,?)",
                    (fingerprint, plan["plan_id"], len(frozen), "REVIEW_ONLY_NO_MODEL_DISPATCH")
                )
                state = "CANDIDATE_REVIEW_ONLY"
            else:
                state = "MATURED_BELOW_SAMPLE_FLOOR"
        else:
            if not sample:
                raise ContractError("TRAIN_CORRECTION_ORIGINAL_NOT_FOUND")
            db.execute(
                "UPDATE samples SET state='REVOKED',outcome_event_id=NULL "
                "WHERE plan_id=? AND feature_id=?",
                (plan["plan_id"], event["feature_id"])
            )
            # No already emitted batch may silently survive an input correction.
            db.execute(
                "UPDATE review_batches SET state='REVOKED_PENDING_INDEPENDENT_REVIEW' WHERE plan_id=?",
                (plan["plan_id"],)
            )
            state = "CORRECTION_REVOKED_ALL_REVIEW_BATCHES"
        db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (event["event_id"], plan["plan_id"], event["feature_id"], kind, now, payload)
        )
        db.execute("COMMIT")
        return {
            "state": state,
            "event_id": event["event_id"],
            "authorization_effect": "NONE",
            "automatic_training": False,
            "broker_orders_sent": 0,
        }
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
