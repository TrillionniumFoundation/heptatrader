"""Bounded successor evaluation of frozen Value plans, with unchanged-input reuse.

The original evaluator/runtime and prediction custody stay authoritative. Reuse
only avoids recomputing the same read-only assessment; it grants no selection.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import shutil
import stat
import time
from pathlib import Path

from .common import digest
from .value_ledger_stream import stream_execution_index
from laya_cell.integrity import read_json, sha_file
from laya_cell.financial_forward_witness import verify_trust, verify_record
from laya_cell import financial_forward_evaluate as ff
from laya_cell.resident_store import write_once, atomic

SCHEMA = "heptatrader.value-evaluator-successor.v1"
CACHE_SCHEMA = "heptatrader.value-evaluator-input-checkpoint.v1"
MAX_INPUT_ENTRIES = 20000
MAX_REUSE_MS = 3600000


def allowed_output(path):
    root = Path.home() / ".local/state/heptatrader/neuron-learning"
    path = Path(path)
    if path.is_symlink() or not path.is_absolute() or path.parent != root:
        raise ValueError("SUCCESSOR_ROOT_MUST_BE_NEW_CHILD_OF_NEURON_LEARNING")
    if not path.name.startswith("value-evaluator-successor-"):
        raise ValueError("EXPLICIT_SUCCESSOR_ID_REQUIRED")
    return path


def ensure_successor(c, target, expected_original_plan_sha):
    original = Path(c["forward_root"])
    original_plans = list(original.glob("plans/*/plan.json"))
    if len(original_plans) != 1:
        raise ValueError("ORIGINAL_SINGLE_PLAN_REQUIRED")
    source = original_plans[0]
    plan = read_json(source)
    if sha_file(source) != expected_original_plan_sha:
        raise ValueError("ORIGINAL_PLAN_HASH_CHANGED")
    ff.validate_registration(c, source, plan)
    successor = target / "plans" / plan["plan_id"]
    if not target.exists():
        target.mkdir(mode=0o700)
        successor.mkdir(parents=True, mode=0o700)
        shutil.copyfile(source, successor / "plan.json")
        shutil.copyfile(source.parent / "registration.json", successor / "registration.json")
        (successor / "predictions").symlink_to(source.parent / "predictions", target_is_directory=True)
    if sha_file(successor / "plan.json") != expected_original_plan_sha:
        raise ValueError("SUCCESSOR_PLAN_MISMATCH")
    if sha_file(successor / "registration.json") != sha_file(source.parent / "registration.json"):
        raise ValueError("SUCCESSOR_REGISTRATION_MISMATCH")
    predictions = successor / "predictions"
    if not predictions.is_symlink() or os.readlink(predictions) != str(source.parent / "predictions"):
        raise ValueError("SUCCESSOR_PREDICTION_LINK_CHANGED")
    return plan, source


def _prepare(config_path, output_root, expected_original_plan_sha):
    c = read_json(Path(config_path))
    if (c.get("schema") != "heptatrader.current100k-forward-worker-config.v1"
            or c.get("signing_role") != "evaluator"
            or c.get("automatic_trading") is not False
            or c.get("actor_update") is not False):
        raise ValueError("READ_ONLY_EVALUATOR_ROLE_REQUIRED")
    target = allowed_output(output_root)
    verify_trust(c)
    if sha_file(Path(c["period_path"])) != c["period_sha256"]:
        raise ValueError("FINANCIAL_EVAL_PERIOD_CHANGED")
    plan, source = ensure_successor(c, target, expected_original_plan_sha)
    return c, target, plan, source


def _entries(path, rows, *, journal=False, recursive=False, skip=()):
    """Metadata witnesses for ordinary files, semantic health for heartbeats."""
    path = Path(path)
    if len(rows) >= MAX_INPUT_ENTRIES:
        raise ValueError("SUCCESSOR_INPUT_ENTRY_BOUND")
    try:
        ls = path.lstat()
    except FileNotFoundError:
        rows.append([str(path), "MISSING"])
        return
    if stat.S_ISLNK(ls.st_mode):
        rows.append([str(path), "LINK", os.readlink(path),
                     ls.st_dev, ls.st_ino, ls.st_mode, ls.st_uid, ls.st_gid,
                     ls.st_mtime_ns, ls.st_ctime_ns])
        try:
            st = path.stat()
        except FileNotFoundError:
            rows.append([str(path), "TARGET_MISSING"])
            return
    else:
        st = ls
    if stat.S_ISDIR(st.st_mode):
        # A health heartbeat replaces a file in this directory every ten seconds.
        # Directory mtime/ctime and health inode changes are not business input.
        rows.append([str(path), "DIRECTORY", st.st_dev, st.st_ino,
                     st.st_mode, st.st_uid, st.st_gid])
        for child in sorted(path.iterdir()):
            if child.name in skip:
                continue
            if journal and not (child.name.endswith(".jsonl") or child.name.endswith(".health.json")):
                continue
            if recursive and child.is_symlink():
                raise ValueError("SUCCESSOR_NESTED_INPUT_SYMLINK")
            if recursive or not child.is_dir():
                _entries(child, rows, journal=journal, recursive=recursive, skip=skip)
        return
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("SUCCESSOR_INPUT_NOT_REGULAR")
    if journal and path.name.endswith(".health.json"):
        from hepta_decision_attribution import read_private, strict
        health = strict(read_private(str(path), 65536))
        if not isinstance(health, dict):
            raise ValueError("SUCCESSOR_HEALTH_SHAPE")
        rows.append([str(path), "HEALTH", st.st_mode, st.st_uid, st.st_gid,
                     {k: v for k, v in health.items() if k != "at_ms"}])
    else:
        rows.append([str(path), "FILE", st.st_dev, st.st_ino, st.st_mode,
                     st.st_uid, st.st_gid, st.st_nlink, st.st_size,
                     st.st_mtime_ns, st.st_ctime_ns])


def input_identity(c, plan, source):
    rows = []
    for key in ("oms_path", "period_path", "witness_trust_path", "role_key_path"):
        if c.get(key):
            _entries(c[key], rows)
    for path in (source, source.parent / "registration.json",
                 source.parent / "assessment.json", source.parent / "assessment-signed.json"):
        _entries(path, rows)
    _entries(source.parent / "predictions", rows, recursive=True)
    # ensure_successor validates the frozen runtime, candidate and dataset on
    # EVERY check, including cache hits. No cached byte stamp replaces it.
    registrations = [plan["producer_registration"]]
    for values in c.get("decision_attribution_registrations", {}).values():
        registrations.extend(values)
    manifests = sorted({r["manifest"] for r in registrations})
    directories = {c["attribution_directory"]} if c.get("attribution_directory") else set()
    for manifest in manifests:
        _entries(manifest, rows)
        data = read_json(Path(manifest))
        directories.add(data["journal_directory"])
    for directory in sorted(directories):
        _entries(directory, rows, journal=True)
    export_dirs = set(c.get("export_directories", {}).values())
    if c.get("export_directory"):
        export_dirs.add(c["export_directory"])
    for directory in sorted(x for x in export_dirs if x):
        _entries(directory, rows, journal=True)
    runtime = {"runner": sha_file(Path(__file__)),
               "stream_reader": sha_file(Path(__file__).with_name("value_ledger_stream.py")),
               "frozen_evaluator": sha_file(Path(ff.__file__))}
    return digest({"config": c, "plan_id": plan["plan_id"],
                   "runtime": runtime, "entries": rows})


def output_identity(target, plan):
    rows = []
    _entries(target / "plans" / plan["plan_id"], rows, recursive=True,
             skip=("predictions",))
    return digest(rows)


def next_due_ms(plan, evaluated_at_ms, evidence_due_ms=None):
    boundaries = [plan[k] for k in ("input_start_ms", "input_end_ms", "outcome_end_ms")
                  if type(plan.get(k)) is int and plan[k] > evaluated_at_ms]
    # Keep crossed evidence deadlines due for the next timer tick.
    if type(evidence_due_ms) is int:
        boundaries.append(evidence_due_ms)
    return min(boundaries + [evaluated_at_ms + MAX_REUSE_MS])


def _assessment(c, target, plan):
    path = target / "plans" / plan["plan_id"] / "assessment.json"
    signed_path = path.with_name("assessment-signed.json")
    value, signed = read_json(path), read_json(signed_path)
    verify_record(c, "evaluator", signed)
    if {k: v for k, v in signed.items() if k not in ("role_witness", "signature")} != value:
        raise ValueError("SUCCESSOR_SIGNED_ASSESSMENT_NOT_IDENTICAL")
    if (not value.get("current_ledger_prefix")
            or value.get("financial_candidate_selected") is not False):
        raise ValueError("SUCCESSOR_LEDGER_OR_PROMOTION_SCOPE")
    return path, signed_path, value


def _evaluate(c, target, plan, source):
    original_assessments = list(Path(c["forward_root"]).glob("plans/*/assessment.json"))
    before = {str(p): sha_file(p) for p in original_assessments}
    started_at_ms = int(time.time() * 1000)
    evidence_due_ms = None

    def observe_timestamp(stamp):
        nonlocal evidence_due_ms
        if stamp > started_at_ms:
            evidence_due_ms = stamp if evidence_due_ms is None else min(evidence_due_ms, stamp)

    def execution_index(source):
        return stream_execution_index(source, timestamp_observer=observe_timestamp)

    previous = ff.execution_index
    try:
        ff.execution_index = execution_index
        ff.Evaluator({**c, "forward_root": str(target)}).step()
    finally:
        ff.execution_index = previous
    _assessment(c, target, plan)
    if {str(p): sha_file(p) for p in original_assessments} != before:
        raise ValueError("ORIGINAL_ASSESSMENT_MUTATED")
    return evidence_due_ms


def _publish(c, target, plan, expected, now, *, skipped, cacheable, evidence_due_ms=None):
    path, signed_path, value = _assessment(c, target, plan)
    stable = {
        "schema": SCHEMA, "original_plan_id": plan["plan_id"],
        "original_plan_sha256": expected,
        "frozen_evaluator_sha256": sha_file(Path(ff.__file__)),
        "successor_runner_sha256": sha_file(Path(__file__)),
        "stream_reader_sha256": sha_file(Path(__file__).with_name("value_ledger_stream.py")),
        "original_source_root": c["forward_root"],
        "successor_result_root": str(target),
        "assessment_semantic_sha256": digest({k: v for k, v in value.items() if k != "as_of_ms"}),
        "evaluator_role_witness_verified": True,
        "original_assessment_files_unchanged": True,
        "historical_replay_not_original_precommitted_runtime": True,
        "canonical_agentd_accepted": False, "automatic_selection": False, "broker_orders_sent": 0,
        "state": value["state"], "observed_trades": value["observed_trades"],
        "total_original_trades": value["total_original_trades"],
        "unresolved_or_censored": value["unresolved_or_censored"],
    }
    identity = digest(stable)
    receipt_path = target / "successor-receipts" / (identity + ".json")
    volatile = {"at_ms": now, "result_sha256": sha_file(path),
                "signed_assessment_sha256": sha_file(signed_path)}
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if {k: v for k, v in receipt.items() if k not in volatile} != stable:
            raise ValueError("SUCCESSOR_RECEIPT_CONFLICT")
        created = False
    else:
        receipt = {**stable, **volatile}
        write_once(receipt_path, receipt)
        created = True
    status = {**receipt, **volatile, "receipt_id": identity,
              "receipt_at_ms": receipt["at_ms"], "receipt_created": created,
              "evaluation_skipped": skipped,
              "skip_reason": "UNCHANGED_INPUTS_BEFORE_NEXT_DEADLINE" if skipped else None,
              "assessment_as_of_ms": value["as_of_ms"],
              "next_evaluation_due_ms": next_due_ms(plan, value["as_of_ms"], evidence_due_ms),
              "input_checkpoint_reusable": cacheable}
    atomic(target / "successor-status.json", status)
    return status


def run(config_path, output_root, expected_original_plan_sha):
    os.umask(0o077)
    c, target, plan, source = _prepare(config_path, output_root, expected_original_plan_sha)
    fd = os.open(target / "successor.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = int(time.time() * 1000)
        before = input_identity(c, plan, source)
        cache_path = target / "successor-input-checkpoint.json"
        try:
            cache = read_json(cache_path) if cache_path.exists() else {}
        except (OSError, ValueError, TypeError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        evaluated = cache.get("evaluated_at_ms")
        if type(evaluated) is int and now < evaluated:
            raise ValueError("SUCCESSOR_CLOCK_REGRESSION")
        reusable = (cache.get("schema") == CACHE_SCHEMA
                    and cache.get("input_identity") == before
                    and type(evaluated) is int
                    and type(cache.get("next_due_ms")) is int
                    and cache["next_due_ms"] == next_due_ms(plan, evaluated, cache.get("evidence_due_ms"))
                    and now < next_due_ms(plan, evaluated, cache.get("evidence_due_ms"))
                    and cache.get("output_identity") == output_identity(target, plan))
        if reusable:
            return _publish(c, target, plan, expected_original_plan_sha, now,
                            skipped=True, cacheable=True,
                            evidence_due_ms=cache.get("evidence_due_ms"))
        # A failed evaluation must never leave the preceding checkpoint reusable.
        if cache_path.exists():
            cache_path.unlink()
        evidence_due_ms = _evaluate(c, target, plan, source)
        after = input_identity(c, plan, source)
        _, _, value = _assessment(c, target, plan)
        # Invalid future evidence may recover without a file change.
        stable_inputs = (before == after and not value.get("source_issues")
                         and not value.get("invalidated_training_reward_ids"))
        if stable_inputs:
            atomic(cache_path, {"schema": CACHE_SCHEMA, "input_identity": after,
                                "output_identity": output_identity(target, plan),
                                "evaluated_at_ms": value["as_of_ms"],
                                "evidence_due_ms": evidence_due_ms,
                                "next_due_ms": next_due_ms(plan, value["as_of_ms"], evidence_due_ms)})
        return _publish(c, target, plan, expected_original_plan_sha, int(time.time() * 1000),
                        skipped=False, cacheable=stable_inputs,
                        evidence_due_ms=evidence_due_ms)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--original-plan-sha256", required=True)
    args = parser.parse_args()
    result = run(args.config, args.output_root, args.original_plan_sha256)
    fields = ("state", "observed_trades", "total_original_trades",
              "unresolved_or_censored", "evaluator_role_witness_verified",
              "canonical_agentd_accepted", "broker_orders_sent",
              "evaluation_skipped", "receipt_created", "next_evaluation_due_ms")
    print(json.dumps({k: result[k] for k in fields}, sort_keys=True))


if __name__ == "__main__":
    main()
