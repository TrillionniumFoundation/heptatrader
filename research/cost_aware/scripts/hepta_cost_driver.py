#!/usr/bin/env python3
"""Version-pinned PAPER driver adapter; opt-in launch, no live-account support.

The immutable original driver owns OMS/HSR1, original-command recovery, native
permits and reduce-only execution. This adapter gates OPEN before preparation and
again just before native submit, adds position-neutral forecast candidates, and
records decisions. No change is made to Execution or to its authorization policy.

Remote deployment has NOT been performed by the accompanying delivery.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import threading
from hepta_cost_policy import (CostError, DEFAULT_POLICY, FORECAST_VERSION, MODEL_ID, HORIZONS,
    dec, digest, validate_forecast, load_calibration, entry_admission, risk_exit, discretionary_exit)
from hepta_cost_capture import Capture, QUOTE_FIELDS, ResetHold, require_no_reset_hold

BASE_DRIVER_SHA256 = "df3e05610ffd29797db39ad231b765955a3fdf83b35e6f7c41a85534a213cefb"
BASE_COMMIT = "387a1ed0373e298d0c38c37999ddc3975f8dfc37"
CANDIDATE_SCHEMA = {
    "type": "object", "required": ["action", "confidence", "rationale", "horizon_sec", "scenarios", "invalidation_price"],
    "additionalProperties": False,
    "properties": {
        "action": {"enum": ["BUY", "SELL", "HOLD"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string", "maxLength": 8192},
        "horizon_sec": {"enum": list(HORIZONS)},
        "scenarios": {"type": "array", "minItems": 2, "maxItems": 7, "items": {
            "type": "object", "additionalProperties": False, "required": ["probability", "move_bps"],
            "properties": {"probability": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                           "move_bps": {"type": "number", "exclusiveMinimum": -2000, "exclusiveMaximum": 2000}}}},
        "invalidation_price": {"type": "number", "minimum": 0},
    },
}


def attach(driver, calibration_path, *, capture_factory=Capture, capture_path=None, lifecycle_guard=None):
    """Attach to an already loaded, hash-checked driver. Tests inject a fake host.

    Never use this function as a substitute for the launcher's source verification.
    All synthetic-calibration paths are refused in this resident adapter.
    """
    # Pure host fixtures may omit the local lifecycle callback. Both executable
    # entry points always supply it; there is no runtime CLI opt-out.
    def lifecycle_check():
        if lifecycle_guard is not None:
            lifecycle_guard()

    original = {name: getattr(driver, name) for name in
                ("prepare_send", "paper_cmd", "atomic_json", "native", "should_close", "inspect_pending", "model_decision")}
    ctx = threading.local()
    capture, capture_problem = None, None
    lock = threading.Lock()

    def recorder(event, payload):
        nonlocal capture, capture_problem
        # Recorder calls from model and main threads share the same single writer.
        with lock:
            try:
                lifecycle_check()
                if capture is None:
                    directory = Path.home() / ".local/state/heptatrader" / ("crypto-driver" if driver.INSTRUMENT == "BTC.USD" else "paper-driver")
                    capture = capture_factory(Path(capture_path) if capture_path is not None else directory / "cost-observations.jsonl", driver.INSTRUMENT)
                capture.append(event, payload)  # Timestamp is taken inside the writer thread lock.
                capture_problem = None
                return True
            except Exception as exc:
                capture_problem = str(exc)
                return False

    def get_artifact():
        try:
            return load_calibration(calibration_path), None
        except Exception as exc:
            return None, "CALIBRATION_REQUIRED" if isinstance(exc, FileNotFoundError) else "CALIBRATION_LOAD_FAILED:" + str(exc)

    def native(cli, tool, *fields):
        value = original["native"](cli, tool, *fields)
        if tool == "market.get_quote":
            recorder("QUOTE", {key: value.get(key) for key in QUOTE_FIELDS})
        return value

    def check(state, quote, side):
        lifecycle_check()
        forecast = state.get("last_decision")
        artifact, load_reason = get_artifact()
        size = driver.crypto_market_cash_budget(quote["ask"]) if driver.INSTRUMENT == "BTC.USD" else driver.QTY
        result = entry_admission(forecast, quote, size, driver.now_ms(), artifact,
                                 last_exit_ms=state.get("cost_last_exit_ms", 0))
        if not isinstance(forecast, dict) or forecast.get("instrument") != driver.INSTRUMENT or forecast.get("action") != side:
            result["allowed"] = False
            result["reasons"].append("COST_FORECAST_ORDER_SCOPE_MISMATCH")
        if state.get("halt_new_entries") or state.get("owner_fence_recovery_required") or state.get("lease_uncertain"):
            result["allowed"] = False
            result["reasons"].append("EXISTING_ENTRY_PROTECTION")
        if abs(float(state.get("position", 0))) > driver.QUANTITY_TOLERANCE:
            result["allowed"] = False
            result["reasons"].append("COST_ENTRY_REQUIRES_FLAT")
        if load_reason:
            result["allowed"] = False
            result["calibration_load_reason"] = load_reason
        if not recorder("ENTRY_CHECK", {"forecast": forecast, "quote": quote, "result": result}):
            result["allowed"] = False
            result["reasons"].append("COST_EVIDENCE_WRITE_FAILED")
        state["cost_last_admission"] = result
        state["cost_capture_error"] = capture_problem
        return result

    def atomic_json(path, value):
        context = getattr(ctx, "opening", None)
        pending = value.get("pending")
        if context and pending and pending.get("kind") == "OPEN":
            pending.setdefault("cost_forecast", copy.deepcopy(context["forecast"]))
            pending.setdefault("cost_admission", copy.deepcopy(context["admission"]))
        reconciled = getattr(ctx, "reconciling", None)
        if reconciled and pending is None:
            before = reconciled["pending"]
            receipt = value.get("last_reconciliation") or {}
            # The first durable write that clears the original pending command
            # must also save its horizon/invalidation or close-cooldown metadata.
            # A second post-return save would leave a crash gap with new inventory
            # but no new-entry thesis. Monetary fields remain original-owned.
            if receipt.get("command_id") == before.get("command_id"):
                position = float(value.get("position", 0))
                if before.get("kind") == "OPEN" and abs(position) > driver.QUANTITY_TOLERANCE:
                    if before.get("cost_forecast"):
                        value["cost_entry_forecast"] = copy.deepcopy(before["cost_forecast"])
                elif (before.get("kind") == "CLOSE" and
                      abs(reconciled["position_before"]) > driver.QUANTITY_TOLERANCE and
                      abs(position) <= driver.QUANTITY_TOLERANCE):
                    value["cost_last_exit_ms"] = driver.now_ms()
                    value.pop("cost_entry_forecast", None)
        return original["atomic_json"](path, value)

    def prepare_send(state, path, binary, socket, token, outbox, kind, quote=None, side=None):
        if kind != "OPEN":
            # Costs, missing files, capture failures and cooldown cannot block this.
            return original["prepare_send"](state, path, binary, socket, token, outbox, kind, quote, side)
        if state.get("pending") is not None:
            raise driver.DriverError("original command pending; cost adapter cannot replace it")
        admission = check(state, quote, side)
        original["atomic_json"](path, state)
        if admission["allowed"] is not True:
            raise driver.DriverError("COST_ENTRY_REJECTED:" + ",".join(admission["reasons"]))
        # Native readiness is still authoritative. The local cost result is not a permit.
        ctx.opening = {"state": state, "path": path, "side": side,
                       "forecast": copy.deepcopy(state["last_decision"]), "admission": admission}
        try:
            return original["prepare_send"](state, path, binary, socket, token, outbox, kind, quote, side)
        finally:
            ctx.opening = None

    def paper_cmd(binary, socket, token, outbox, operation, extra):
        context = getattr(ctx, "opening", None)
        if operation == "submit" and context:
            # This hook is after native prepare and immediately BEFORE the original
            # submit transport. The original durable POSSIBLY_SENT marker is kept.
            # A local final rejection returns a definite non-send result through
            # the original driver's normal reconciliation path; pending isn't erased.
            try:
                lifecycle_check()
                cli = str(Path.home() / ".local/bin/heptatrader")
                snap = driver.snapshot(cli, require_quote=False)
                if (snap["active"] or abs(float(snap["position"])) > driver.QUANTITY_TOLERANCE or
                        snap["owner_health"].get("mutation_ready") is not True):
                    raise CostError("COST_FINAL_AUTHORITATIVE_FLATNESS_UNPROVEN")
                quote = driver.native(cli, "market.get_quote", "instrument=" + driver.INSTRUMENT)
                # Recompute against the original candidate, even if state receives
                # other observational fields while the native prepare call runs.
                state = context["state"]
                if state.get("last_decision") != context["forecast"]:
                    raise CostError("COST_CANDIDATE_CHANGED_DURING_PREPARE")
                final = check(state, quote, context["side"])
                state["pending"]["cost_final_admission"] = final
                original["atomic_json"](context["path"], state)
                if final["allowed"] is not True:
                    raise CostError("COST_FINAL_CHECK:" + ",".join(final["reasons"]))
            except Exception as exc:
                return {"ok": False, "transport_ok": True, "application_status": "error",
                        "application_reason": "COST_FINAL_NON_SEND:" + str(exc),
                        "result": {"status": "error", "order_id": -1, "reason_code": "COST_FINAL_NON_SEND"}}
        return original["paper_cmd"](binary, socket, token, outbox, operation, extra)

    def build_model_candidate(openclaw, state_dir, history, _position):
        # The position is deliberately not included. One exogenous forecast tape
        # can therefore be replayed through three different position trajectories.
        lifecycle_check()
        if not history:
            raise driver.DriverError("forecast needs a quote history")
        schema_path = state_dir / "decision-schema-v2.json"
        raw = json.dumps(CANDIDATE_SCHEMA, sort_keys=True, separators=(",", ":"))
        if not schema_path.exists():
            with schema_path.open("x", encoding="utf-8") as stream:
                stream.write(raw)
            schema_path.chmod(0o600)
        elif schema_path.is_symlink() or schema_path.read_text() != raw:
            raise driver.DriverError("forecast v2 schema drift")
        compact = [{"t": item["observed_at_ms"], "bid": item["bid"], "ask": item["ask"], "mid": item["mid"]} for item in history[-24:]]
        prompt = ("For an IB PAPER research candidate, forecast the UNDERLYING MID-price return, independent of any holding. "
                  "You cannot trade or use tools. Return only JSON matching: " + raw + ". "
                  "scenarios move_bps are SIGNED UNDERLYING returns, not side-adjusted profit and not net of fees. "
                  "Probabilities must sum to 1. State a fixed horizon and a price invalidation: BUY below current mid, "
                  "SELL above current mid, HOLD uses zero. Prefer HOLD without a coherent measurable edge. "
                  "Your confidence is NOT calibrated and your response never grants permission. "
                  + "Instrument=" + driver.INSTRUMENT + "; quotes=" + json.dumps(compact, separators=(",", ":")))
        actual_model = driver.openclaw_model_ref(driver.MODEL)
        if actual_model + ":" + FORECAST_VERSION != MODEL_ID:
            raise driver.DriverError("forecast model requires a new calibration identity")
        lifecycle_check()
        process = driver.run([openclaw, "infer", "model", "run", "--local", "--model", actual_model,
                              "--thinking", "low", "--prompt", prompt, "--json"], 150)
        if process.returncode != 0:
            raise driver.DriverError("forecast model request failed")
        envelope = driver.strict_json(process.stdout)
        outputs = envelope.get("outputs")
        if envelope.get("ok") is not True or not isinstance(outputs, list) or len(outputs) != 1:
            raise driver.DriverError("forecast envelope invalid")
        forecast = driver.strict_json(outputs[0]["text"])
        if set(forecast) != set(CANDIDATE_SCHEMA["required"]):
            raise driver.DriverError("forecast candidate fields invalid")
        last = history[-1]
        forecast.update(forecast_version=FORECAST_VERSION, model_id=MODEL_ID, instrument=driver.INSTRUMENT,
                        issued_at_ms=int(last["observed_at_ms"]), reference_mid=str((dec(last["bid"]) + dec(last["ask"])) / 2),
                        position_neutral=True)
        validate_forecast(forecast)
        # Record HOLD, refused, and eventually accepted candidates alike.
        lifecycle_check()
        if not recorder("CANDIDATE", {"forecast": forecast, "candidate_id": digest(forecast)}):
            raise driver.DriverError("cost candidate capture failed")
        return forecast

    def model_decision(openclaw, state_dir, history, position):
        try:
            return build_model_candidate(openclaw, state_dir, history, position)
        except Exception as exc:
            # Failure evidence must not include raw model responses/credentials.
            # Invalid candidate output is never silently changed into a forecast.
            recorder("MODEL_FAILURE", {"instrument": driver.INSTRUMENT,
                                      "forecast_version": FORECAST_VERSION,
                                      "failure_type": type(exc).__name__,
                                      "candidate_accepted": False})
            raise

    def should_close(state, position, quote, decision):
        original_safety = original["should_close"](state, position, quote, None)
        if original_safety[0]:
            return original_safety
        forecast = state.get("cost_entry_forecast")
        why = risk_exit(position, state.get("entry_price"), quote, driver.now_ms(), entry_forecast=forecast,
                        max_adverse=str(driver.MAX_ADVERSE))
        if why:
            return True, why
        if not forecast:
            # A pre-upgrade position keeps its original exit behavior. Do not
            # invent an entry thesis or make an inherited holding wait for fees.
            return original["should_close"](state, position, quote, decision)
        artifact, _ = get_artifact()
        result = discretionary_exit(decision, quote, position, driver.now_ms(), artifact)
        recorder("EXIT_CHECK", {"forecast": decision, "position": position, "close": result[0], "reason": result[1]})
        return result

    def inspect_pending(state, path, binary, socket, token, outbox, snap):
        before = copy.deepcopy(state.get("pending"))
        old_position = float(state.get("position", 0))
        ctx.reconciling = {"pending": before, "position_before": old_position} if before else None
        try:
            result = original["inspect_pending"](state, path, binary, socket, token, outbox, snap)
        finally:
            ctx.reconciling = None
        if before and state.get("pending") is None:
            reconciliation = state.get("last_reconciliation") or {}
            if reconciliation.get("command_id") == before.get("command_id"):
                recorder("SETTLEMENT", {"kind": before.get("kind"), "command_id": before.get("command_id"),
                                        "position_after": state.get("position"), "reconciled_at_ms": driver.now_ms()})
        return result

    for name, value in {"native": native, "atomic_json": atomic_json, "prepare_send": prepare_send,
                        "paper_cmd": paper_cmd, "model_decision": model_decision,
                        "should_close": should_close, "inspect_pending": inspect_pending}.items():
        setattr(driver, name, value)

    def close():
        if capture:
            capture.close()
    return close


def load_pinned_driver(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or info.st_nlink != 1:
        raise CostError("BASE_DRIVER_FILE_UNSAFE")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != BASE_DRIVER_SHA256:
        raise CostError("BASE_DRIVER_HASH_MISMATCH_REVIEW_REQUIRED")
    spec = importlib.util.spec_from_file_location("hepta_pinned_paper_driver", path)
    module = importlib.util.module_from_spec(spec)
    # Execute the already hashed bytes, not a second unpinned file read.
    exec(compile(content, str(path), "exec"), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-paper-hooks", action="store_true", required=True)
    parser.add_argument("--base-driver", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, default=Path.home() / ".config/heptatrader/cost-calibration.json")
    args, remaining = parser.parse_known_args()
    if os.geteuid() == 0:
        parser.error("unprivileged PAPER owner required")
    try:
        require_no_reset_hold()
    except ResetHold as exc:
        print(json.dumps({"state": "RESET_HOLD", "reason": str(exc),
                          "driver_started": False, "broker_orders_sent": 0}), file=sys.stderr)
        return 78
    module = load_pinned_driver(args.base_driver)
    close = attach(module, args.calibration, lifecycle_guard=require_no_reset_hold)
    sys.argv = [str(args.base_driver), *remaining]
    try:
        return module.main()
    finally:
        close()


if __name__ == "__main__":
    raise SystemExit(main())
