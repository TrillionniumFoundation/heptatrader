#!/usr/bin/env python3
"""Refresh the multi-asset read-only data plane under a fail-closed guard."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import pathlib
import subprocess
from typing import Any, Dict, List, Sequence


SCHEMA = "openclaw.hepta.agent_trader_data_plane_refresh.v1"
DEFAULT_OUTPUT = pathlib.Path("runtime-logs/agent-trader-data-plane-refresh/latest.json")


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


def _write_json_atomic(path: pathlib.Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run(repo: pathlib.Path, command: Sequence[str], timeout_sec: int) -> Dict[str, Any]:
    try:
        proc = subprocess.run(list(command), cwd=str(repo), text=True, capture_output=True, timeout=timeout_sec, check=False)
        return {
            "command": list(command),
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "stdout_tail": proc.stdout[-500:],
            "stderr_tail": proc.stderr[-500:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": list(command),
            "returncode": None,
            "ok": False,
            "stdout_tail": str(exc.stdout or "")[-500:],
            "stderr_tail": str(exc.stderr or "")[-500:],
            "timed_out": True,
        }


def _order_safe(order: Dict[str, Any]) -> bool:
    summary = order.get("summary") if isinstance(order.get("summary"), dict) else {}
    return (
        order.get("order_submission_allowed") is False
        and order.get("execution_allowed") is False
        and order.get("paper_consumer_clearance_allowed") is False
        and summary.get("order_intent_count") in {0, None}
        and summary.get("place_sent_count") in {0, None}
        and summary.get("route_leaks") in (None, [])
    )


def run_refresh(args: argparse.Namespace) -> Dict[str, Any]:
    repo = args.repo.resolve()
    preflight = _run(repo, ["./status_openclaw_hepta_order_path_safety_sentinel.sh"], args.command_timeout_sec)
    order_before = _read_json(args.order_sentinel)
    source_commands = [
        ["python3", "scripts/openclaw_agent_trader_market_mark_adapter.py", "--fetch-crypto-public", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_public_equity_index_snapshot_adapter.py", "--fetch-public-yahoo", "--write-drop-snapshot", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_fut_opt_snapshot_adapter.py", "--fetch-public-yahoo-futures", "--fetch-public-cboe-options", "--write-drop-snapshot", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_macro_vol_snapshot_adapter.py", "--fetch-public-yahoo-macro-vol", "--write-drop-snapshot", "--write-defaults"],
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        source_results = list(pool.map(lambda command: _run(repo, command, args.source_timeout_sec), source_commands))
    pipeline_commands = [
        ["python3", "scripts/openclaw_agent_trader_asset_snapshot_source_exporter.py", "--stage-valid-snapshots", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_asset_mark_snapshot_adapter.py", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_mark_coverage.py", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_asset_worklist_runner.py", "--write-defaults"],
        ["python3", "scripts/openclaw_agent_trader_data_plane_contract.py", "--write-defaults"],
    ]
    pipeline_results: List[Dict[str, Any]] = []
    if all(result["ok"] for result in source_results):
        for command in pipeline_commands:
            result = _run(repo, command, args.command_timeout_sec)
            pipeline_results.append(result)
            if not result["ok"]:
                break
    postflight = _run(repo, ["./status_openclaw_hepta_order_path_safety_sentinel.sh"], args.command_timeout_sec)
    order_after = _read_json(args.order_sentinel)
    contract = _read_json(args.data_contract)
    assets = contract.get("assets") if isinstance(contract.get("assets"), dict) else {}
    allowed_assets = sorted(name for name, detail in assets.items() if isinstance(detail, dict) and detail.get("live_decision_input_allowed") is True)
    command_ok = preflight["ok"] and postflight["ok"] and all(result["ok"] for result in source_results + pipeline_results)
    safe = _order_safe(order_before) and _order_safe(order_after)
    return {
        "schema": SCHEMA,
        "generated_at_utc": _now_iso(),
        "status": "completed" if command_ok and safe else "failed_closed",
        "source_results": source_results,
        "pipeline_results": pipeline_results,
        "order_preflight": preflight,
        "order_postflight": postflight,
        "order_path_safe_before": _order_safe(order_before),
        "order_path_safe_after": _order_safe(order_after),
        "data_plane_ready": contract.get("data_plane_ready") is True,
        "live_decision_input_allowed_assets": allowed_assets,
        "contract_violations": contract.get("violations", []),
        "guard": {
            "calls_broker": False,
            "starts_consumer": False,
            "writes_oms_intent": False,
            "writes_order_intent": False,
            "paper_live_enabled": False,
            "route_leaks": [],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh all read-only Agent Trader data sources.")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path("."))
    parser.add_argument("--order-sentinel", type=pathlib.Path, default=pathlib.Path("runtime-logs/openclaw-hepta-order-path-safety-sentinel/latest.json"))
    parser.add_argument("--data-contract", type=pathlib.Path, default=pathlib.Path("runtime-logs/agent-trader-data-plane-contract/latest.json"))
    parser.add_argument("--source-timeout-sec", type=int, default=120)
    parser.add_argument("--command-timeout-sec", type=int, default=120)
    parser.add_argument("--json-output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run_refresh(args)
    _write_json_atomic(args.json_output, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
