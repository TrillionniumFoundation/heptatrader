"""One-shot, fail-closed prospective RECHALLENGE of an all-censored Laya trial.

This is NOT training, retroactive repair, or self-promotion. It may reuse the
identical previously committed adapter only after the original trial is
authoritatively REJECTED with zero observations. It spends the next trial
number/alpha, checks the same original data and time boundary, and uses a
separately SHA-pinned fast original source profile. No broker API is imported.
Stop the resident trainer/evaluator before running; their locks enforce this.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from laya_cell.integrity import ContractError, canonical, digest, read_json, sha_file
from laya_cell.promotion import rule_for_trial, validate_plan_rule
from laya_cell.promotion_power import PROTOCOL
from laya_cell.resident import config_check
from laya_cell.resident_source_policy import RECENT, RUNTIME_PIN
from laya_cell.resident_store import database, get_meta, put_meta, write_once

SRC_PIN_NAMES = (
    "learn/laya_cell/resident_evaluate.py",
    "learn/laya_cell/resident_predict.py",
    "learn/laya_cell/promotion.py",
    "learn/laya_cell/promotion_power.py",
    "learn/laya_cell/resident_data.py",
    "learn/laya_cell/shadow_source.py",
) + RUNTIME_PIN


def private_file(path, maximum=1024 * 1024):
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink()
            or path.resolve() != path or not path.is_file()):
        raise ContractError("RECHALLENGE_FILE_IDENTITY")
    st = path.stat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
            or st.st_mode & 0o077 or st.st_nlink != 1
            or st.st_size > maximum):
        raise ContractError("RECHALLENGE_FILE_SCOPE")
    return path


def fixed_doc(path, expected=None, maximum=1024 * 1024):
    p = private_file(path, maximum)
    if expected is not None and sha_file(p) != expected:
        raise ContractError("RECHALLENGE_FROZEN_SHA_MISMATCH")
    return read_json(p, maximum)


def validate_closed(original_plan_path, expected_plan_sha, expected_final_sha,
                    evaluation_root):
    path = private_file(original_plan_path)
    if path.name != "plan.json" or path.parent.parent != evaluation_root:
        raise ContractError("RECHALLENGE_ORIGINAL_PLAN_ROOT")
    original = fixed_doc(path, expected_plan_sha)
    if (original.get("schema") != "heptatrader.laya-resident-future-plan.v1"
            or original.get("plan_id") != digest(
                {k: v for k, v in original.items() if k != "plan_id"})):
        raise ContractError("RECHALLENGE_ORIGINAL_PLAN_CHANGED")
    rule = validate_plan_rule(original)
    final_path = path.parent / "qualification.json"
    final = fixed_doc(final_path, expected_final_sha)
    if (final.get("state") != "REJECTED"
            or final.get("plan_id") != original["plan_id"]
            or final.get("candidate_id") != original["candidate_id"]
            or final.get("observed") != 0
            or final.get("censored") != rule["slots"]
            or final.get("promotion_performed") is not False
            or final.get("canonical_owner_admitted") is not False
            or final.get("statistical_qualification") is not False
            or "EXCESS_CENSORED_WINDOWS" not in final.get("reasons", [])):
        raise ContractError("RECHALLENGE_NOT_FULLY_CENSORED_TERMINAL")
    originals = sorted(path.parent.glob("outcome-*.json"))
    if len(originals) != rule["slots"]:
        raise ContractError("RECHALLENGE_ORIGINAL_ROSTER_INCOMPLETE")
    outcome_hashes = []
    for slot, outcome_path in enumerate(originals):
        if outcome_path.name != f"outcome-{slot:03}.json":
            raise ContractError("RECHALLENGE_ORIGINAL_SLOT_ORDER")
        row = fixed_doc(outcome_path, maximum=16 * 1024 * 1024)
        if (row.get("slot") != slot or row.get("status") != "CENSORED"
                or row.get("candidate_id") != original["candidate_id"]
                or row.get("plan_id") != original["plan_id"]):
            raise ContractError("RECHALLENGE_ORIGINAL_SLOT_IDENTITY")
        outcome_hashes.append(sha_file(outcome_path))
    return original, final, digest(outcome_hashes)


def validate_candidate(original, expected_period, now_ms):
    candidate_dir = Path(original["candidate_directory"])
    parent_dir = Path(original["parent_directory"])
    candidate_path = private_file(candidate_dir / "candidate.json", 128 * 1024)
    candidate = read_json(candidate_path)
    parent_path = private_file(parent_dir / "candidate.json", 128 * 1024)
    parent = read_json(parent_path)
    if (candidate["candidate_id"] != original["candidate_id"]
            or candidate["candidate_id"] != digest({
                k: v for k, v in candidate.items() if k != "candidate_id"})
            or sha_file(candidate_path) != original["candidate_manifest_sha256"]
            or candidate["parent_id"] != original["parent_id"]
            or parent["candidate_id"] != candidate["parent_id"]
            or parent["candidate_id"] != digest({
                k: v for k, v in parent.items() if k != "candidate_id"})
            or candidate.get("status") != "REQUIRES_INDEPENDENT_FUTURE_EVALUATION"
            or candidate.get("automatic_cutover") is not False
            or candidate.get("pnl_reward_used") is not False
            or candidate.get("broker_orders_sent") != 0
            or candidate.get("created_at_ms", now_ms) >= original["start_ms"]
            or candidate.get("trained_until_ms", now_ms) >= original["start_ms"]
            or original["scope"] != expected_period):
        raise ContractError("RECHALLENGE_CANDIDATE_PROVENANCE")
    if (sha_file(private_file(candidate_dir / "adapter.safetensors",
                              32 * 1024 * 1024)) != candidate["adapter_sha256"]
            or sha_file(private_file(parent_dir / "adapter.safetensors",
                                    32 * 1024 * 1024)) != parent["adapter_sha256"]):
        raise ContractError("RECHALLENGE_TENSOR_PIN_MISMATCH")
    return candidate


def read_current_source(config, status_path, now_ms):
    status = fixed_doc(status_path, maximum=1024 * 1024)
    if (status.get("state") != "READY_FOR_REVIEW_NOT_AUTHORIZATION"
            or status.get("usable") != 24 or status.get("unusable") != 0
            or type(status.get("at_ms")) is not int
            or not 0 <= now_ms - status["at_ms"] <= 90000
            or type(status.get("latest_quote_age_ms")) is not int
            or not 0 <= status["latest_quote_age_ms"] < 5000
            or status.get("reasons") != []):
        raise ContractError("RECHALLENGE_SOURCE_NOT_CONTINUOUS")
    from laya_cell.resident_recent_source import read_recent_original_quotes
    recent, pins = read_recent_original_quotes(config, now_ms)
    if not recent or now_ms - recent[-1]["t"] >= 5000:
        raise ContractError("RECHALLENGE_LIVE_QUOTE_NOT_FRESH")
    return {"readiness_sha256": sha_file(status_path),
            "recent_quote_count": len(recent),
            "recent_quote_receipt": recent[-1]["receipt"],
            "prefixes_digest": digest(pins),
            "source_asof_ms": now_ms}


def register(config_path, original_path, original_plan_sha, final_sha,
             source_status, receipt_directory, lead_ms=180000, dry_run=False):
    if type(lead_ms) is not int or not 180000 <= lead_ms <= 600000:
        raise ContractError("RECHALLENGE_LEAD_TIME_BOUND")
    config_path = private_file(config_path, 256 * 1024)
    config = config_check(read_json(config_path))
    if (config.get("future_source_read_profile") != RECENT
            or config.get("promotion_protocol") != PROTOCOL
            or config.get("automatic_trading") is not False):
        raise ContractError("RECHALLENGE_FUTURE_PROFILE_REQUIRED")
    release_hashes = config.get("runtime_sha256", {})
    if any(not release_hashes.get(name) for name in SRC_PIN_NAMES):
        raise ContractError("RECHALLENGE_RELEASE_PINS_MISSING")
    evaluation_root = Path(config["evaluation_directory"])
    control_root = Path(config["control_directory"])
    if (not evaluation_root.is_dir() or evaluation_root.is_symlink()
            or not control_root.is_dir() or control_root.is_symlink()
            or not receipt_directory.is_absolute()
            or receipt_directory.is_symlink()):
        raise ContractError("RECHALLENGE_ROOT_SCOPE")
    original, final, roster_digest = validate_closed(
        original_path, original_plan_sha, final_sha, evaluation_root)
    now_ms = int(time.time() * 1000)
    candidate = validate_candidate(original, config["period_sha256"], now_ms)
    source = read_current_source(config, source_status, now_ms)
    if dry_run:
        return {"state": "RECHALLENGE_PREFLIGHT_ONLY_NO_WRITE",
                "next_trial_proposed": original["trial"] + 1,
                "original_plan_sha256": original_plan_sha,
                "original_final_sha256": final_sha,
                "same_frozen_candidate": candidate["candidate_id"],
                "source": source, "broker_orders_sent": 0,
                "new_plan_written": False, "model_loaded": False}
    sched_lock = os.open(control_root / "scheduler.lock",
                         os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    eval_lock = os.open(evaluation_root / "evaluator.lock",
                        os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    db = None
    try:
        fcntl.flock(sched_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(eval_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = database(control_root / "scheduler.sqlite")
        db.execute("BEGIN IMMEDIATE")
        current = get_meta(db, "evaluation_trial", 0)
        all_plans = [read_json(p) for p in evaluation_root.glob("*/plan.json")]
        if (original.get("trial") != current or
                len(all_plans) != current
                or {p.get("trial") for p in all_plans} != set(range(1, current + 1))
                or any(not (p.parent / "qualification.json").exists()
                       for p in evaluation_root.glob("*/plan.json"))):
            raise ContractError("RECHALLENGE_TRIAL_HISTORY_NOT_TERMINAL")
        job_id = original["job_id"]
        completed = db.execute("SELECT candidate_id,status,payload FROM jobs WHERE id=?",
                               (job_id,)).fetchone()
        if (completed is None or completed["status"] != "COMPLETED"
                or completed["candidate_id"] != candidate["candidate_id"]):
            raise ContractError("RECHALLENGE_ORIGINAL_TRAIN_JOB")
        original_job = json.loads(completed["payload"])
        if (original_job.get("output_directory") != original["candidate_directory"]
                or original_job.get("parent_directory") != original["parent_directory"]
                or original_job.get("parent_id") != original["parent_id"]
                or original_job.get("automatic_trading") is not False):
            raise ContractError("RECHALLENGE_TRAIN_JOB_PROVENANCE")
        # The original healthy read can become stale while taking both locks.
        # Recheck the REAL append-only live source immediately before freezing
        # a new prospective clock; on failure the DB transaction rolls back.
        source = read_current_source(config, source_status, int(time.time()*1000))
        now_ms = int(time.time()*1000)
        trial = current + 1
        rule = rule_for_trial(trial, config["promotion_protocol"])
        start_ms = now_ms + lead_ms
        new_folder = evaluation_root / ("rechallenge-" + digest([
            original["plan_id"], final_sha, candidate["candidate_id"], trial
        ])[:24])
        if new_folder.exists() or new_folder.is_symlink():
            raise ContractError("RECHALLENGE_ALREADY_REGISTERED")
        pins = {name: release_hashes[name] for name in SRC_PIN_NAMES}
        plan = {
            "schema": "heptatrader.laya-resident-future-plan.v1",
            "job_id": job_id, "candidate_id": candidate["candidate_id"],
            "candidate_directory": original["candidate_directory"],
            "parent_directory": original["parent_directory"],
            "parent_id": original["parent_id"],
            "created_at_ms": now_ms, "start_ms": start_ms,
            "outcome_end_ms": start_ms + (rule["slots"] - 1) *
            rule["slot_spacing_ms"] + rule["input_grace_ms"] +
            rule["horizon_ms"] + 5000,
            "trial": trial, "rule": rule, "scope": config["period_sha256"],
            "no_refit": True, "broker_orders_sent": 0,
            "evaluation_runtime_sha256": pins,
            "candidate_manifest_sha256": original["candidate_manifest_sha256"],
            "future_source_read_profile": RECENT,
            "rechallenge_of_plan_id": original["plan_id"],
            "rechallenge_original_terminal_sha256": final_sha,
            "rechallenge_original_roster_digest": roster_digest,
            "same_frozen_candidate_no_training": True,
            "rechallenge_purpose": "OPERATIONAL_FUTURE_COVERAGE_NO_EFFICACY_CLAIM",
        }
        plan["plan_id"] = digest(plan)
        validate_plan_rule(plan)
        receipt = {
            "schema": "heptatrader.laya-frozen-rechallenge-registration.v1",
            "trial": trial, "source": source,
            "original_trial": current,
            "original_plan_sha256": original_plan_sha,
            "original_terminal_sha256": final_sha,
            "original_roster_digest": roster_digest,
            "plan_id": plan["plan_id"],
            "candidate_id": candidate["candidate_id"],
            "candidate_refit": False,
            "future_plan_sha256": hashlib.sha256(canonical(plan) + b"\n").hexdigest(),
            "alpha": 0.05 / (trial * (trial + 1)),
            "trade_authority": False, "automatic_model_promotion": False,
            "broker_orders_sent": 0,
        }
        receipt_path = Path(receipt_directory) / (
            "trial-" + str(trial) + "-rechallenge-receipt.json")
        if receipt_path.exists():
            raise ContractError("RECHALLENGE_RECEIPT_EXISTS")
        new_folder.mkdir(mode=0o700)
        write_once(new_folder / "plan.json", plan)
        # The ledger increments only after a durable, immutable plan exists.
        put_meta(db, "evaluation_trial", trial)
        db.commit()
        # The receipt is supplemental; the plan+DB are already authoritative.
        write_once(receipt_path, receipt)
        return {"state": "FUTURE_RECHALLENGE_REGISTERED_NOT_TRAINED",
                "trial": trial, "plan": str(new_folder / "plan.json"),
                "plan_sha256": sha_file(new_folder / "plan.json"),
                "receipt": str(receipt_path),
                "start_ms": start_ms,
                "source_profile": RECENT,
                "same_frozen_candidate": True,
                "broker_orders_sent": 0}
    except BaseException:
        if db is not None and db.in_transaction:
            db.rollback()
        raise
    finally:
        if db is not None:
            db.close()
        os.close(eval_lock)
        os.close(sched_lock)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--original-plan", type=Path, required=True)
    ap.add_argument("--original-plan-sha256", required=True)
    ap.add_argument("--terminal-sha256", required=True)
    ap.add_argument("--source-status", type=Path, required=True)
    ap.add_argument("--receipt-directory", type=Path, required=True)
    ap.add_argument("--lead-ms", type=int, default=180000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    os.umask(0o077)
    print(json.dumps(register(
        args.config, args.original_plan, args.original_plan_sha256,
        args.terminal_sha256, args.source_status,
        args.receipt_directory, args.lead_ms, args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
