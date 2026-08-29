#!/usr/bin/env python3
"""Supervise one fail-closed AI-native observation cycle with a process lease.

The installed service intentionally runs scout-only.  Explicit CLI flags can
add either a reviewable-bucket model review or prospective forward-OOS
observation sampling.  Both paths remain non-consumable and keep the
paper/live/order path closed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence


SCHEMA = "openclaw.hepta.agent_trader_ai_native_supervisor.v1"
DEFAULT_OUTPUT_DIR = pathlib.Path("runtime-logs/agent-trader-ai-native-supervisor")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _repo_path(repo: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else repo / path


def _parse_iso(value: Any) -> Optional[dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _write_json_atomic(path: pathlib.Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256_path(path: pathlib.Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl_line_count(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _data_plane_eligibility(
    report: Dict[str, Any],
    *,
    asset: str,
    contract_path: pathlib.Path,
    max_age_sec: float,
) -> Dict[str, Any]:
    assets = report.get("assets") if isinstance(report.get("assets"), dict) else {}
    detail = assets.get(asset) if isinstance(assets.get(asset), dict) else {}
    freshness = report.get("artifact_freshness") if isinstance(report.get("artifact_freshness"), dict) else {}
    generated = _parse_iso(report.get("generated_at_utc"))
    age_sec = (
        max(0.0, (dt.datetime.now(dt.timezone.utc) - generated).total_seconds())
        if generated is not None
        else None
    )
    contract_fresh = bool(age_sec is not None and age_sec <= max_age_sec)
    eligible = bool(
        detail.get("live_decision_input_allowed") is True
        and freshness.get("ready") is True
        and contract_fresh
    )
    return {
        "asset": asset,
        "eligible": eligible,
        "live_decision_input_allowed": detail.get("live_decision_input_allowed"),
        "source_contract_state": detail.get("source_contract_state"),
        "artifact_freshness_ready": freshness.get("ready"),
        "contract_age_sec": age_sec,
        "contract_max_age_sec": max_age_sec,
        "contract_fresh_now": contract_fresh,
        "contract_hash": _sha256_path(contract_path),
        "reason": "eligible" if eligible else "asset_data_not_live_decision_eligible_or_fresh_fail_closed",
    }


def _ensure_policy_epoch(
    *,
    repo: pathlib.Path,
    protocol: Dict[str, Any],
    critique_source: pathlib.Path,
    epoch_root: pathlib.Path,
    model_version: str,
    prompt_version: str,
) -> Dict[str, Any]:
    config = protocol.get("policy_epoch") if isinstance(protocol.get("policy_epoch"), dict) else {}
    epoch_id = str(config.get("epoch_id") or "").strip()
    source_paths = [str(item) for item in config.get("policy_source_paths", []) if str(item).strip()]
    valid_config = bool(
        epoch_id
        and str(config.get("model_version") or "") == model_version
        and str(config.get("prompt_version") or "") == prompt_version
        and source_paths
    )
    if not valid_config:
        return {"ready": False, "reason": "policy_epoch_config_invalid_fail_closed"}

    epoch_dir = epoch_root / epoch_id
    manifest_path = epoch_dir / "manifest.json"
    frozen_critique_path = epoch_dir / "critique-snapshot.json"
    critique_source = _repo_path(repo, critique_source)
    source_hashes = {
        item: _sha256_path(_repo_path(repo, pathlib.Path(item)))
        for item in source_paths
    }
    if not all(source_hashes.values()):
        return {"ready": False, "reason": "policy_epoch_source_missing_fail_closed", "source_hashes": source_hashes}

    manifest = _read_json(manifest_path)
    if not manifest:
        critique = _read_json(critique_source)
        if not critique:
            return {"ready": False, "reason": "policy_epoch_critique_source_missing_fail_closed"}
        _write_json_atomic(frozen_critique_path, critique)
        critique_hash = _sha256_path(frozen_critique_path)
        basis = {
            "epoch_id": epoch_id,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "critique_snapshot_hash": critique_hash,
            "policy_source_hashes": source_hashes,
        }
        policy_state_hash = hashlib.sha256(
            json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest = {
            "schema": "openclaw.hepta.agent_trader_ai_native_policy_epoch.v1",
            "created_at_utc": _now_iso(),
            **basis,
            "policy_state_hash": policy_state_hash,
            "frozen_critique_path": str(frozen_critique_path),
            "guard": {
                "calls_broker": False,
                "writes_order_intent": False,
                "paper_live_enabled": False,
            },
        }
        _write_json_atomic(manifest_path, manifest)

    current_critique_hash = _sha256_path(frozen_critique_path)
    ready = bool(
        manifest.get("epoch_id") == epoch_id
        and manifest.get("model_version") == model_version
        and manifest.get("prompt_version") == prompt_version
        and manifest.get("critique_snapshot_hash") == current_critique_hash
        and manifest.get("policy_source_hashes") == source_hashes
        and manifest.get("policy_state_hash")
    )
    expected_policy_hash = str(config.get("expected_policy_state_hash") or "")
    expected_critique_hash = str(config.get("expected_critique_snapshot_hash") or "")
    if expected_policy_hash and manifest.get("policy_state_hash") != expected_policy_hash:
        ready = False
    if expected_critique_hash and current_critique_hash != expected_critique_hash:
        ready = False
    return {
        "ready": ready,
        "reason": "policy_epoch_frozen_and_verified" if ready else "policy_epoch_hash_mismatch_fail_closed",
        "epoch_id": epoch_id,
        "manifest_path": str(manifest_path),
        "frozen_critique_path": str(frozen_critique_path),
        "policy_state_hash": manifest.get("policy_state_hash"),
        "critique_snapshot_hash": current_critique_hash,
        "model_version": manifest.get("model_version"),
        "prompt_version": manifest.get("prompt_version"),
        "policy_source_hashes": source_hashes,
    }


def _scout_summary(redesign: Dict[str, Any]) -> Dict[str, Any]:
    for component in redesign.get("components", []):
        if isinstance(component, dict) and component.get("name") == "ai_native_edge_repair_live_setup_scout":
            summary = component.get("summary")
            return summary if isinstance(summary, dict) else {}
    return {}


def _run_command(
    command: Sequence[str],
    *,
    cwd: pathlib.Path,
    timeout_sec: int,
    retries: int,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    for attempt in range(1, retries + 2):
        started = time.monotonic()
        try:
            proc = subprocess.run(
                list(command),
                cwd=str(cwd),
                env=merged_env,
                text=True,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
            )
            result = {
                "attempt": attempt,
                "returncode": proc.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "stdout_tail": proc.stdout[-1000:],
                "stderr_tail": proc.stderr[-1000:],
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "attempt": attempt,
                "returncode": None,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "stdout_tail": str(exc.stdout or "")[-1000:],
                "stderr_tail": str(exc.stderr or "")[-1000:],
                "timed_out": True,
            }
        except OSError as exc:
            result = {
                "attempt": attempt,
                "returncode": None,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "stdout_tail": "",
                "stderr_tail": f"{type(exc).__name__}: {exc}",
                "timed_out": False,
                "spawn_failed": True,
            }
        attempts.append(result)
        if result["returncode"] == 0:
            return {"ok": True, "attempts": attempts}
    return {"ok": False, "attempts": attempts}


def _order_path_safe(order: Dict[str, Any]) -> bool:
    summary = order.get("summary") if isinstance(order.get("summary"), dict) else {}
    return (
        order.get("order_submission_allowed") is False
        and order.get("execution_allowed") is False
        and order.get("paper_consumer_clearance_allowed") is False
        and summary.get("order_intent_count") in {0, None}
        and summary.get("place_sent_count") in {0, None}
        and summary.get("route_leaks") in (None, [])
    )


def _oos_sampling_plan(
    protocol: Dict[str, Any],
    state: Dict[str, Any],
    *,
    now: dt.datetime,
    authorized: bool,
    observation_eligible: bool = True,
    journal_line_count: int = 0,
) -> Dict[str, Any]:
    """Return a prospective fixed-slot OOS observation decision."""
    policy = protocol.get("observation_sampling") if isinstance(protocol.get("observation_sampling"), dict) else {}
    cutover = _parse_iso(protocol.get("cutover_utc"))
    if state.get("protocol_id") != protocol.get("protocol_id"):
        state = {}
    last_attempt = _parse_iso(state.get("last_attempt_at_utc"))
    try:
        cadence_sec = int(policy.get("cadence_sec") or 0)
    except (TypeError, ValueError):
        cadence_sec = 0
    try:
        cycles_per_sample = int(policy.get("cycles_per_sample") or 0)
    except (TypeError, ValueError):
        cycles_per_sample = 0
    try:
        retry_limit = int(policy.get("failure_retry_limit") or 0)
    except (TypeError, ValueError):
        retry_limit = 0
    try:
        retry_delay_sec = int(policy.get("failure_retry_delay_sec") or 0)
    except (TypeError, ValueError):
        retry_delay_sec = 0
    protocol_valid = bool(
        protocol.get("schema") == "openclaw.hepta.agent_trader_ai_native_oos_protocol.v1"
        and protocol.get("protocol_id")
        and cutover is not None
        and protocol.get("post_cutover_partition") == "forward_oos"
        and policy.get("enabled") is True
        and policy.get("require_reviewable_bucket") is False
        and policy.get("execution_effect") == "none"
        and cadence_sec == 900
        and cycles_per_sample == 1
        and retry_limit == 1
        and retry_delay_sec >= 300
    )
    now = now.astimezone(dt.timezone.utc)
    cutover_reached = bool(cutover is not None and now >= cutover)
    slot_index = int((now - cutover).total_seconds() // cadence_sec) if cutover_reached and cadence_sec > 0 else None
    slot_start = cutover + dt.timedelta(seconds=slot_index * cadence_sec) if cutover is not None and slot_index is not None else None
    slot_id = f"{protocol.get('protocol_id')}:{slot_start.isoformat()}" if slot_start is not None else None
    same_slot = bool(slot_id and state.get("slot_id") == slot_id)
    attempt_count = int(state.get("attempt_count") or 0) if same_slot else 0
    prior_status = str(state.get("last_attempt_status") or "") if same_slot else ""
    journal_before = int(state.get("journal_line_count_before") or 0) if same_slot else journal_line_count
    recovered_signal = bool(
        same_slot
        and prior_status in {"reserved", "failed_closed", "no_signal"}
        and journal_line_count > journal_before
    )
    slot_completed = bool(same_slot and (prior_status == "signal_recorded" or recovered_signal))
    elapsed_sec = None if last_attempt is None else max(0.0, (now - last_attempt).total_seconds())
    retry_due = bool(
        same_slot
        and not slot_completed
        and attempt_count > 0
        and attempt_count < 1 + retry_limit
        and elapsed_sec is not None
        and elapsed_sec >= retry_delay_sec
    )
    first_attempt_due = bool(not same_slot or attempt_count == 0)
    due = bool(
        authorized
        and protocol_valid
        and cutover_reached
        and observation_eligible
        and not slot_completed
        and (first_attempt_due or retry_due)
    )
    if not authorized:
        reason = "scheduled_oos_sampling_not_authorized"
    elif not protocol_valid:
        reason = "oos_sampling_protocol_invalid_fail_closed"
    elif not cutover_reached:
        reason = "prospective_oos_cutover_not_reached"
    elif not observation_eligible:
        reason = "oos_observation_asset_not_eligible_fail_closed"
    elif slot_completed:
        reason = "fixed_sampling_slot_already_completed"
    elif same_slot and attempt_count >= 1 + retry_limit:
        reason = "fixed_sampling_slot_retry_exhausted"
    elif same_slot and not retry_due:
        reason = "fixed_sampling_slot_retry_delay_not_reached"
    else:
        reason = "prospective_fixed_slot_oos_observation_due"
    return {
        "authorized": authorized,
        "protocol_valid": protocol_valid,
        "protocol_id": protocol.get("protocol_id"),
        "cutover_utc": protocol.get("cutover_utc"),
        "cutover_reached": cutover_reached,
        "partition": protocol.get("post_cutover_partition"),
        "cadence_sec": cadence_sec,
        "cycles_per_sample": cycles_per_sample,
        "fixed_slot": True,
        "slot_index": slot_index,
        "slot_start_utc": slot_start.isoformat() if slot_start is not None else None,
        "slot_id": slot_id,
        "attempt_count": attempt_count,
        "failure_retry_limit": retry_limit,
        "failure_retry_delay_sec": retry_delay_sec,
        "retry_due": retry_due,
        "slot_completed": slot_completed,
        "recovered_signal_detected": recovered_signal,
        "observation_eligible": observation_eligible,
        "last_attempt_at_utc": state.get("last_attempt_at_utc"),
        "elapsed_since_last_attempt_sec": elapsed_sec,
        "due": due,
        "reason": reason,
        "require_reviewable_bucket": policy.get("require_reviewable_bucket"),
        "natural_abstention_allowed": policy.get("natural_abstention_allowed") is True,
        "execution_effect": policy.get("execution_effect"),
    }


def run_cycle(args: argparse.Namespace) -> Dict[str, Any]:
    trace_id = str(uuid.uuid4())
    commands: List[Dict[str, Any]] = []
    sentinel = _run_command(
        ["./status_openclaw_hepta_order_path_safety_sentinel.sh"],
        cwd=args.repo,
        timeout_sec=args.command_timeout_sec,
        retries=args.retries,
    )
    commands.append({"name": "order_path_preflight", **sentinel})
    if not sentinel["ok"]:
        return _report(args, trace_id, commands, "preflight_failed", {}, False, False)

    order = _read_json(args.order_sentinel)
    if not _order_path_safe(order):
        return _report(args, trace_id, commands, "order_path_safety_breach", {}, False, False)

    data_refresh_ok = True
    if args.refresh_data_plane:
        data_refresh = _run_command(
            ["./status_openclaw_agent_trader_data_plane_refresh.sh"],
            cwd=args.repo,
            timeout_sec=args.data_plane_timeout_sec,
            retries=0,
        )
        commands.append({"name": "read_only_data_plane_refresh", **data_refresh})
        data_refresh_ok = data_refresh["ok"]

    scout = _run_command(
        ["./status_openclaw_agent_trader_ai_native_evidence_graph.sh"],
        cwd=args.repo,
        timeout_sec=args.command_timeout_sec,
        retries=args.retries,
    )
    commands.append({"name": "no_model_scout_refresh", **scout})
    fast_eligibility_refresh_ok = True
    if args.refresh_oos_eligibility or args.allow_scheduled_oos_sampling:
        fast_refresh = _run_command(
            ["./status_openclaw_agent_trader_fx_oos_eligibility_refresh.sh"],
            cwd=args.repo,
            timeout_sec=args.command_timeout_sec,
            retries=args.retries,
        )
        commands.append({"name": "fast_fx_oos_eligibility_refresh", **fast_refresh})
        fast_eligibility_refresh_ok = fast_refresh["ok"]
    data_contract_path = _repo_path(args.repo, args.data_plane_contract)
    data_eligibility = _data_plane_eligibility(
        _read_json(data_contract_path),
        asset=args.oos_eligible_asset,
        contract_path=data_contract_path,
        max_age_sec=args.data_plane_contract_max_age_sec,
    )
    redesign = _read_json(args.redesign)
    scout_summary = _scout_summary(redesign)
    reviewable = int(scout_summary.get("reviewable_candidate_count") or 0)
    oos_protocol_path = _repo_path(args.repo, args.oos_protocol)
    oos_state_path = _repo_path(args.repo, args.oos_sampling_state)
    protocol = _read_json(oos_protocol_path)
    policy_epoch = _ensure_policy_epoch(
        repo=args.repo,
        protocol=protocol,
        critique_source=args.critique_json,
        epoch_root=_repo_path(args.repo, args.policy_epoch_dir),
        model_version=args.model_version,
        prompt_version=args.prompt_version,
    ) if args.allow_scheduled_oos_sampling else {"ready": False, "reason": "scheduled_oos_sampling_not_authorized"}
    signal_journal_path = _repo_path(args.repo, args.signal_journal)
    journal_line_count = _jsonl_line_count(signal_journal_path)
    oos_sampling = _oos_sampling_plan(
        protocol,
        _read_json(oos_state_path),
        now=dt.datetime.now(dt.timezone.utc),
        authorized=bool(args.allow_scheduled_oos_sampling),
        observation_eligible=bool(data_eligibility.get("eligible")),
        journal_line_count=journal_line_count,
    )
    oos_sampling["policy_epoch_ready"] = policy_epoch.get("ready")
    oos_sampling["policy_epoch_id"] = policy_epoch.get("epoch_id")
    reviewable_model_due = bool(reviewable > 0 and args.allow_bounded_model_review)
    oos_sampling_due = bool(oos_sampling.get("due") and policy_epoch.get("ready"))
    maturity_ok = True
    p3_observation_data_ready = bool(fast_eligibility_refresh_ok and data_eligibility.get("eligible"))
    if scout["ok"] and p3_observation_data_ready:
        maturity = _run_command(
            ["bash", "./status_openclaw_agent_trader_ai_native_maturity_refresh.sh"],
            cwd=args.repo,
            timeout_sec=args.command_timeout_sec,
            retries=args.retries,
        )
        commands.append({"name": "maturity_refresh", **maturity})
        maturity_ok = maturity["ok"]

    model_review_ran = False
    oos_sampling_ran = False
    model_trigger = "none"
    model_cycles_requested = 0
    model_ok = not bool(oos_sampling.get("due") and not policy_epoch.get("ready"))
    if scout["ok"] and p3_observation_data_ready and maturity_ok and (reviewable_model_due or oos_sampling_due):
        if reviewable_model_due and oos_sampling_due:
            model_trigger = "reviewable_bucket_and_scheduled_forward_oos"
        elif reviewable_model_due:
            model_trigger = "reviewable_bucket"
        else:
            model_trigger = "scheduled_forward_oos"
        model_cycles_requested = int(oos_sampling.get("cycles_per_sample") or 1) if oos_sampling_due else 3
        env = {
            "OPENCLAW_AI_NATIVE_ACCUMULATOR_BATCHES": "1",
            "OPENCLAW_AI_NATIVE_ACCUMULATOR_CYCLES_PER_BATCH": str(model_cycles_requested),
            "OPENCLAW_AI_NATIVE_ACCUMULATOR_PRE_REFRESH_BEFORE_BURN": "1",
            "OPENCLAW_AI_NATIVE_MODEL_FAILURE_POLICY": "no-signal",
            "OPENCLAW_AI_NATIVE_MODEL_VERSION": args.model_version,
            "OPENCLAW_AI_NATIVE_PROMPT_VERSION": args.prompt_version,
            "OPENCLAW_AI_NATIVE_OOS_PROTOCOL": str(args.oos_protocol),
            "OPENCLAW_AI_NATIVE_OPENCLAW_MODEL": args.openclaw_model,
        }
        if oos_sampling_due:
            env.update({
                "OPENCLAW_AI_NATIVE_CRITIQUE_JSON": str(policy_epoch.get("frozen_critique_path") or ""),
                "OPENCLAW_AI_NATIVE_POLICY_EPOCH_ID": str(policy_epoch.get("epoch_id") or ""),
                "OPENCLAW_AI_NATIVE_POLICY_STATE_HASH": str(policy_epoch.get("policy_state_hash") or ""),
                "OPENCLAW_AI_NATIVE_CRITIQUE_SNAPSHOT_HASH": str(policy_epoch.get("critique_snapshot_hash") or ""),
                "OPENCLAW_AI_NATIVE_SAMPLING_SLOT_ID": str(oos_sampling.get("slot_id") or ""),
                "OPENCLAW_AI_NATIVE_OBSERVATION_ELIGIBLE": "1",
                "OPENCLAW_AI_NATIVE_DATA_PLANE_CONTRACT_HASH": str(data_eligibility.get("contract_hash") or ""),
            })
            journal_before = _jsonl_line_count(signal_journal_path)
            _write_json_atomic(oos_state_path, {
                "schema": "openclaw.hepta.agent_trader_ai_native_oos_sampling_state.v1",
                "protocol_id": oos_sampling.get("protocol_id"),
                "slot_id": oos_sampling.get("slot_id"),
                "slot_index": oos_sampling.get("slot_index"),
                "slot_start_utc": oos_sampling.get("slot_start_utc"),
                "attempt_count": int(oos_sampling.get("attempt_count") or 0) + 1,
                "last_attempt_at_utc": _now_iso(),
                "last_attempt_trace_id": trace_id,
                "last_attempt_trigger": model_trigger,
                "last_attempt_status": "reserved",
                "journal_line_count_before": journal_before,
                "policy_epoch_id": policy_epoch.get("epoch_id"),
                "policy_state_hash": policy_epoch.get("policy_state_hash"),
                "guard": {
                    "calls_broker": False,
                    "writes_order_intent": False,
                    "paper_live_enabled": False,
                },
            })
        else:
            env["OPENCLAW_AI_NATIVE_EVALUATION_PARTITION_OVERRIDE"] = "non_formal_review"
        accumulator = _run_command(
            ["./status_openclaw_agent_trader_ai_native_sample_accumulator.sh"],
            cwd=args.repo,
            timeout_sec=args.model_timeout_sec,
            retries=0,
            env=env,
        )
        commands.append({"name": "bounded_model_accumulator", **accumulator})
        model_review_ran = True
        oos_sampling_ran = oos_sampling_due
        model_ok = accumulator["ok"]
        if oos_sampling_due:
            state = _read_json(oos_state_path)
            journal_after = _jsonl_line_count(signal_journal_path)
            signal_recorded = journal_after > int(state.get("journal_line_count_before") or 0)
            state["journal_line_count_after"] = journal_after
            state["signal_journal_lines_added"] = max(0, journal_after - int(state.get("journal_line_count_before") or 0))
            state["last_attempt_status"] = (
                "signal_recorded"
                if signal_recorded
                else "no_signal"
                if accumulator["ok"]
                else "failed_closed"
            )
            state["last_attempt_completed_at_utc"] = _now_iso()
            _write_json_atomic(oos_state_path, state)
            oos_sampling["attempt_result"] = state["last_attempt_status"]
            oos_sampling["signal_journal_lines_added"] = state["signal_journal_lines_added"]

    final_sentinel = _run_command(
        ["./status_openclaw_hepta_order_path_safety_sentinel.sh"],
        cwd=args.repo,
        timeout_sec=args.command_timeout_sec,
        retries=args.retries,
    )
    commands.append({"name": "order_path_postflight", **final_sentinel})
    final_order = _read_json(args.order_sentinel)
    safe = final_sentinel["ok"] and _order_path_safe(final_order)
    status = "completed" if safe and scout["ok"] and fast_eligibility_refresh_ok and maturity_ok and model_ok else "failed_closed"
    return _report(
        args,
        trace_id,
        commands,
        status,
        scout_summary,
        model_review_ran,
        safe,
        oos_sampling=oos_sampling,
        oos_sampling_ran=oos_sampling_ran,
        model_trigger=model_trigger,
        model_cycles_requested=model_cycles_requested,
        data_eligibility=data_eligibility,
        policy_epoch=policy_epoch,
    )


def _report(
    args: argparse.Namespace,
    trace_id: str,
    commands: List[Dict[str, Any]],
    status: str,
    scout: Dict[str, Any],
    model_review_ran: bool,
    order_path_safe: bool,
    *,
    oos_sampling: Optional[Dict[str, Any]] = None,
    oos_sampling_ran: bool = False,
    model_trigger: str = "none",
    model_cycles_requested: int = 0,
    data_eligibility: Optional[Dict[str, Any]] = None,
    policy_epoch: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generated_at_utc": _now_iso(),
        "trace_id": trace_id,
        "status": status,
        "leader_lease_acquired": True,
        "mode": "supervised_scout_with_optional_review_and_forward_oos_observation",
        "model_review_authorized_for_this_run": bool(args.allow_bounded_model_review),
        "model_review_ran": model_review_ran,
        "model_trigger": model_trigger,
        "model_cycles_requested": model_cycles_requested,
        "scheduled_oos_sampling_authorized_for_this_run": bool(getattr(args, "allow_scheduled_oos_sampling", False)),
        "scheduled_oos_sampling_ran": oos_sampling_ran,
        "oos_sampling": oos_sampling or {},
        "oos_data_eligibility": data_eligibility or {},
        "policy_epoch": policy_epoch or {},
        "model_version": args.model_version,
        "prompt_version": args.prompt_version,
        "command_timeout_sec": args.command_timeout_sec,
        "model_timeout_sec": args.model_timeout_sec,
        "retry_count": args.retries,
        "scout": scout,
        "commands": commands,
        "order_path_safe": order_path_safe,
        "guard": {
            "calls_broker": False,
            "starts_consumer": False,
            "writes_oms_intent": False,
            "writes_order_intent": False,
            "writes_paper_request": False,
            "writes_shadow_intent": False,
            "paper_live_enabled": False,
            "route_leaks": [],
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run one supervised, fail-closed AI-native scout cycle.")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--lock-file", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-ai-native-supervisor/leader.lock"))
    parser.add_argument("--redesign", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-ai-native-redesign/latest.json"))
    parser.add_argument("--order-sentinel", type=pathlib.Path, default=pathlib.Path("runtime-logs/openclaw-hepta-order-path-safety-sentinel/latest.json"))
    parser.add_argument("--command-timeout-sec", type=int, default=120)
    parser.add_argument("--model-timeout-sec", type=int, default=480)
    parser.add_argument("--data-plane-timeout-sec", type=int, default=240)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--model-version", default=os.environ.get("OPENCLAW_AI_NATIVE_MODEL_VERSION", "gpt-5.6-sol"))
    parser.add_argument("--prompt-version", default=os.environ.get("OPENCLAW_AI_NATIVE_PROMPT_VERSION", "ai-native-v1"))
    parser.add_argument("--openclaw-model", default=os.environ.get("OPENCLAW_AI_NATIVE_OPENCLAW_MODEL", "gpt-5.6-sol"))
    parser.add_argument("--allow-bounded-model-review", action="store_true")
    parser.add_argument("--allow-scheduled-oos-sampling", action="store_true")
    parser.add_argument("--oos-protocol", type=pathlib.Path, default=pathlib.Path("configs/ai-native-oos-protocol-v3.json"))
    parser.add_argument(
        "--oos-sampling-state",
        type=pathlib.Path,
        default=pathlib.Path("runtime-logs/agent-trader-ai-native-supervisor/oos-sampling-state-v3.json"),
    )
    parser.add_argument("--data-plane-contract", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-data-plane-contract/latest.json"))
    parser.add_argument("--data-plane-contract-max-age-sec", type=float, default=180.0)
    parser.add_argument("--oos-eligible-asset", default="FX")
    parser.add_argument("--critique-json", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-ai-native-journal-critique/latest.json"))
    parser.add_argument("--policy-epoch-dir", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-ai-native-supervisor/policy-epochs"))
    parser.add_argument("--signal-journal", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-ai-native-signal/ai-native-signal-journal.jsonl"))
    parser.add_argument("--refresh-data-plane", action="store_true")
    parser.add_argument("--refresh-oos-eligibility", action="store_true")
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--json-output", type=pathlib.Path)
    parser.add_argument("--write-defaults", action="store_true")
    args = parser.parse_args(argv)
    args.repo = args.repo.resolve()
    args.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with args.lock_file.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            report = {
                "schema": SCHEMA,
                "generated_at_utc": _now_iso(),
                "status": "duplicate_suppressed",
                "leader_lease_acquired": False,
                "guard": {"calls_broker": False, "writes_order_intent": False, "paper_live_enabled": False, "route_leaks": []},
            }
        else:
            report = run_cycle(args)
    if args.write_defaults or args.json_output:
        output = args.json_output or args.output_dir / "latest.json"
        _write_json_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"completed", "duplicate_suppressed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
