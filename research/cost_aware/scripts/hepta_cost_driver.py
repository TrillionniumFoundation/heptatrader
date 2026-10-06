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
from hepta_cost_policy import (CostError, FORECAST_VERSION, MODEL_ID, HORIZONS,
    dec, digest, validate_forecast)
from hepta_cost_capture import Capture, QUOTE_FIELDS, ResetHold, require_no_reset_hold

BASE_DRIVER_SHA256 = "2a047583b092bde6c3112581b61ca18f826d5c52485bbc8f9a02f1971747bfbb"
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


def attach(driver, _local_training_compat_path=None, *, capture_factory=Capture, capture_path=None, lifecycle_guard=None, learning_export=None):
    """Attach the remote-model cost guard to a hash-checked PAPER driver.

    The optional second positional argument is retained only for old test/call-site compatibility and is never
    read by the resident remote-model path. Local training artifacts belong to
    offline training/research, not runtime authority.
    """
    # Pure host fixtures may omit the local lifecycle callback. Both executable
    # entry points always supply it; there is no runtime CLI opt-out.
    def lifecycle_check():
        if lifecycle_guard is not None:
            lifecycle_guard()

    def learn(kind, payload):
        if learning_export is not None:
            try:
                learning_export.emit(kind, payload)
            except Exception:
                pass  # Learning export cannot block execution or a risk exit.

    original = {name: getattr(driver, name) for name in
                ("prepare_send", "paper_cmd", "atomic_json", "native", "should_close", "inspect_pending",
                 "model_decision", "entry_candidate_allowed")}
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

    def native(cli, tool, *fields):
        value = original["native"](cli, tool, *fields)
        if tool == "market.get_quote":
            recorder("QUOTE", {key: value.get(key) for key in QUOTE_FIELDS})
            learn("QUOTE", {key: value.get(key) for key in QUOTE_FIELDS})
        return value

    def atomic_json(path, value):
        result = original["atomic_json"](path, value)
        if learning_export is not None:
            try:
                from hepta_learning_export import projected_state
                learn("DECISION", projected_state(value))
            except Exception:
                pass
        return result

    def entry_candidate_allowed(decision):
        """Resident PAPER entry uses the historical directional confidence rule.

        The remote forecast remains schema/identity checked and position-neutral,
        but cost arithmetic has no authorization effect. HOLD stays non-entry and
        the base driver's historical CONF=0.62 threshold remains authoritative.
        """
        try:
            validate_forecast(decision)
        except Exception:
            return False
        return original["entry_candidate_allowed"](decision)

    def prepare_send(state, path, binary, socket, token, outbox, kind, quote=None, side=None):
        """Historical PAPER strategy plus lifecycle fencing; no resident cost gate."""
        if kind == "OPEN":
            lifecycle_check()
        return original["prepare_send"](state, path, binary, socket, token, outbox, kind, quote, side)

    def paper_cmd(binary, socket, token, outbox, operation, extra):
        if operation == "submit":
            try:
                lifecycle_check()
            except Exception as exc:
                return {"ok": False, "transport_ok": True, "application_status": "error",
                        "application_reason": "LIFECYCLE_FINAL_NON_SEND:" + type(exc).__name__,
                        "result": {"status": "error", "order_id": -1,
                                   "reason_code": "LIFECYCLE_FINAL_NON_SEND"}}
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
            raise driver.DriverError("forecast model identity changed; review remote OOS before rollout")
        input_snapshot = {
            "instrument": driver.INSTRUMENT, "model_id": MODEL_ID,
            "forecast_version": FORECAST_VERSION, "quotes": copy.deepcopy(compact),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "request_started_at_ms": driver.now_ms(),
            "position_neutral": True,
        }
        input_id = digest(input_snapshot)
        learn("MODEL_INPUT", {"input_id": input_id, "input": input_snapshot})
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
        learn("FORECAST", {"input_id": input_id, "candidate_id": digest(forecast),
                           "forecast": copy.deepcopy(forecast),
                           "available_at_ms": driver.now_ms()})
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
        return original["should_close"](state, position, quote, decision)

    def inspect_pending(state, path, binary, socket, token, outbox, snap):
        before = copy.deepcopy(state.get("pending"))
        result = original["inspect_pending"](state, path, binary, socket, token, outbox, snap)
        if before and state.get("pending") is None:
            reconciliation = state.get("last_reconciliation") or {}
            if reconciliation.get("command_id") == before.get("command_id"):
                recorder("SETTLEMENT", {"kind": before.get("kind"), "command_id": before.get("command_id"),
                                        "position_after": state.get("position"), "reconciled_at_ms": driver.now_ms()})
                if learning_export is not None:
                    try:
                        from hepta_learning_export import projected_state
                        learn("SETTLEMENT", projected_state(state))
                    except Exception:
                        pass
        return result

    for name, value in {"native": native, "atomic_json": atomic_json, "prepare_send": prepare_send,
                        "paper_cmd": paper_cmd, "model_decision": model_decision,
                        "should_close": should_close, "inspect_pending": inspect_pending,
                        "entry_candidate_allowed": entry_candidate_allowed}.items():
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
    parser.add_argument("--learning-export-dir", type=Path,
                        help="optional non-authorizing, bounded asynchronous learning export")
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
    exporter = None
    if args.learning_export_dir is not None:
        try:
            from hepta_learning_export import LearningExport
            instrument = "EUR.USD"
            if "--instrument" in remaining:
                instrument = remaining[remaining.index("--instrument") + 1]
            exporter = LearningExport(args.learning_export_dir, instrument)
        except Exception as exc:
            print(json.dumps({"learning_export_started": False,
                              "reason": type(exc).__name__}), file=sys.stderr)
    close = attach(module, lifecycle_guard=require_no_reset_hold, learning_export=exporter)
    sys.argv = [str(args.base_driver), *remaining]
    try:
        return module.main()
    finally:
        close()
        if exporter is not None:
            exporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
