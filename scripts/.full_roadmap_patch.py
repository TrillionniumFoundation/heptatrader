#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PR95 = "origin/remediation/full-priority-cleanup-20260916"


def read(path: str) -> str:
    return (ROOT / path).read_text()


def write(path: str, value: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)


def replace_once(path: str, old: str, new: str) -> None:
    value = read(path)
    if value.count(old) != 1:
        raise RuntimeError(f"{path}: expected one replacement, got {value.count(old)}")
    write(path, value.replace(old, new, 1))


def copy_pr95(path: str) -> None:
    value = subprocess.check_output(["git", "show", f"{PR95}:{path}"], cwd=ROOT, text=True)
    write(path, value)


# 1) Integrate the useful PR95 implementation differences without replacing main.
for path in (
    "HeptaTrade/execution/execution_generation_support.cpp",
    "HeptaTrade/execution/execution_coordinator_terminal.cpp",
    "HeptaTrade/oms_generation_store.h",
):
    copy_pr95(path)

replace_once(
    "HeptaTrade/CMakeLists.txt",
    "    execution/execution_coordinator_reconnect.cpp\n    execution/execution_coordinator_terminal.cpp)\n",
    "    execution/execution_coordinator_reconnect.cpp\n    execution/execution_coordinator_terminal.cpp\n"
    "    execution/execution_generation_support.cpp)\n",
)
for obsolete in (
    "HeptaTrade/execution/execution_generation_support.inc",
    "HeptaTrade/execution/execution_generation_capacity_support.inc",
    "HeptaTrade/execution/execution_generation_v2_support.inc",
):
    path = ROOT / obsolete
    if path.exists():
        path.unlink()

# 2) Adopt PR95's sorted cumulative send-attempt index, then remove the
#    still-unbounded verification lists and the arbitrary 1024-generation cap.
copy_pr95("scripts/hepta_oms_lifecycle.py")
replace_once("scripts/hepta_oms_lifecycle.py", "MAX_CHAIN = 1024\n", "")
replace_once(
    "scripts/hepta_oms_lifecycle.py",
    '''    runtime_lines = list(_iter_private_lines(root / "runtime-command-index.tsv"))\n    if len(runtime_lines) != manifest.get("command_records"):\n        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")\n    previous = None\n    for line in runtime_lines:\n        key, _, _ = _runtime_row(line)\n        if previous is not None and key <= previous:\n            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")\n        previous = key\n    send_lines = list(_iter_private_lines(root / "send-attempt-index.tsv"))\n    if len(send_lines) != manifest.get("send_attempt_records"):\n        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")\n    if sorted_send_index:\n        previous_send = None\n        for line in send_lines:\n            fields = line.rstrip(b"\\n").decode("ascii").split("\\t")\n            if len(fields) != 7:\n                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID")\n            try:\n                key = (fields[0], fields[1], int(fields[2]), int(fields[6]),\n                       fields[3], fields[4], fields[5])\n            except ValueError as error:\n                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error\n            if previous_send is not None and key <= previous_send:\n                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")\n            previous_send = key\n''',
    '''    runtime_count = 0\n    previous = None\n    for line in _iter_private_lines(root / "runtime-command-index.tsv"):\n        key, _, _ = _runtime_row(line)\n        if previous is not None and key <= previous:\n            raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_ORDER_INVALID")\n        previous = key\n        runtime_count += 1\n    if runtime_count != manifest.get("command_records"):\n        raise v1.GenerationError("OMS_GENERATION_RUNTIME_INDEX_COUNT_MISMATCH")\n\n    send_count = 0\n    previous_send = None\n    for line in _iter_private_lines(root / "send-attempt-index.tsv"):\n        send_count += 1\n        if not sorted_send_index:\n            continue\n        try:\n            fields = line.rstrip(b"\\n").decode("ascii").split("\\t")\n        except UnicodeError as error:\n            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error\n        if len(fields) != 7:\n            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID")\n        try:\n            key = (fields[0], fields[1], int(fields[2]), int(fields[6]),\n                   fields[3], fields[4], fields[5])\n        except ValueError as error:\n            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error\n        if previous_send is not None and key <= previous_send:\n            raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")\n        previous_send = key\n    if send_count != manifest.get("send_attempt_records"):\n        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")\n''')
replace_once(
    "scripts/hepta_oms_lifecycle.py",
    '''    while generation:\n        if generation in seen or len(chain) >= MAX_CHAIN:\n            raise v1.GenerationError("OMS_GENERATION_PARENT_CHAIN_INVALID")\n''',
    '''    while generation:\n        if generation in seen:\n            raise v1.GenerationError("OMS_GENERATION_PARENT_CHAIN_INVALID")\n''')

# 3) Publish a digest-bound simulator checkpoint as the final projection rows in
#    hot-replay.jsonl. The rows are not journal history and are never exported;
#    they are a compact state projection consumed only after generation hashes
#    and the active-tail lineage have been verified.
simulator_helpers = r'''
SIMULATOR_STATE_META = "simulator_state_checkpoint"
SIMULATOR_STATE_POSITION = "simulator_position_checkpoint"
SIMULATOR_STATE_READY = "simulator_state_checkpoint_ready"


def _empty_simulator_state() -> dict[str, Any]:
    return {"max_order_id": 999999, "admitted_orders": 0, "positions": {}}


def _simulator_checkpoint_from_hot(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    positions: dict[str, float] = {}
    ready = False
    for event in events:
        kind = event.get("event", "")
        if kind == SIMULATOR_STATE_META:
            if state is not None or ready or event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            maximum = event.get("order_id", -1)
            admitted = event.get("broker_request_id", -1)
            if (type(maximum) is not int or maximum < 999999 or type(admitted) is not int or admitted < 0):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            state = {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}
            continue
        if kind == SIMULATOR_STATE_POSITION:
            if state is None or ready or event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            instrument = event.get("instrument", "")
            side = event.get("side", "")
            quantity = event.get("qty", 0.0)
            if (not instrument or instrument in positions or side not in {"BUY", "SELL"} or
                    isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or
                    not math.isfinite(float(quantity)) or float(quantity) <= 0.0):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            positions[instrument] = float(quantity) if side == "BUY" else -float(quantity)
            continue
        if kind == SIMULATOR_STATE_READY:
            if state is None or ready or event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            if (event.get("order_id") != state["max_order_id"] or
                    event.get("broker_request_id") != state["admitted_orders"]):
                raise v1.GenerationError("OMS_SIMULATOR_CHECKPOINT_INVALID")
            ready = True
    return state if ready else None


def _apply_simulator_events(state: dict[str, Any], events: list[dict[str, Any]], *,
                            require_new_order_ids: bool) -> dict[str, Any]:
    maximum = int(state["max_order_id"])
    base_maximum = maximum
    admitted = int(state["admitted_orders"])
    positions = dict(state["positions"])
    places: dict[int, dict[str, Any]] = {}
    fills: dict[int, dict[str, Any]] = {}
    for event in events:
        order_id = event.get("order_id", -1)
        if type(order_id) is int and order_id > maximum:
            maximum = order_id
        if event.get("venue") != "SIMULATOR" or event.get("account") != "SIM":
            continue
        is_fill = event.get("event") == "status" and event.get("status") == "Filled"
        if event.get("event") != "place_sent" and not is_fill:
            continue
        quantity = event.get("qty", 0.0)
        if (type(order_id) is not int or order_id < 0 or not event.get("instrument") or
                event.get("side") not in {"BUY", "SELL"} or isinstance(quantity, bool) or
                not isinstance(quantity, (int, float)) or not math.isfinite(float(quantity)) or
                float(quantity) <= 0.0):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_EVENT_INVALID")
        if event.get("event") == "place_sent":
            prior = places.get(order_id)
            identity = (event.get("instrument"), event.get("side"), float(quantity),
                        event.get("req_id"), event.get("request_hash"))
            if prior is not None:
                prior_identity = (prior.get("instrument"), prior.get("side"), float(prior.get("qty", 0.0)),
                                  prior.get("req_id"), prior.get("request_hash"))
                if identity != prior_identity:
                    raise v1.GenerationError("OMS_SIMULATOR_STATE_PLACE_CONFLICT")
                continue
            if require_new_order_ids and order_id <= base_maximum:
                raise v1.GenerationError("OMS_SIMULATOR_STATE_ORDER_ID_REGRESSION")
            places[order_id] = event
            admitted += 1
            continue
        owner = places.get(order_id)
        price = event.get("price", 0.0)
        if (owner is None or isinstance(price, bool) or not isinstance(price, (int, float)) or
                not math.isfinite(float(price)) or float(price) <= 0.0 or
                owner.get("instrument") != event.get("instrument") or
                owner.get("side") != event.get("side") or float(owner.get("qty", 0.0)) != float(quantity)):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_FILL_CONFLICT")
        prior = fills.get(order_id)
        if prior is not None:
            if (prior.get("instrument"), prior.get("side"), float(prior.get("qty", 0.0)), float(prior.get("price", 0.0))) != (
                    event.get("instrument"), event.get("side"), float(quantity), float(price)):
                raise v1.GenerationError("OMS_SIMULATOR_STATE_FILL_CONFLICT")
            continue
        fills[order_id] = event
        instrument = event["instrument"]
        positions[instrument] = positions.get(instrument, 0.0) + (
            float(quantity) if event["side"] == "BUY" else -float(quantity))
        if not math.isfinite(positions[instrument]):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_POSITION_OVERFLOW")
    return {"max_order_id": maximum, "admitted_orders": admitted, "positions": positions}


def _simulator_state_projection(state: dict[str, Any]) -> list[dict[str, Any]]:
    def base(kind: str) -> dict[str, Any]:
        return {
            "schema_version": 4, "event": kind, "ts_ms": 0, "order_id": -1,
            "req_id": "", "client_req_id": "", "trace_id": "",
            "event_id": f"{kind}:v1", "risk_code": "", "venue": "SIMULATOR",
            "strategy": "", "account": "SIM", "execution_domain": "SIM:checkpoint",
            "request_hash": "", "venue_correlation_id": "", "broker_callback_type": "",
            "broker_service_epoch": "", "broker_connection_epoch": 0,
            "broker_request_id": 0, "broker_error_code": 0, "broker_message": "",
            "broker_advanced_order_reject_json": "", "broker_why_held": "",
            "broker_execution_id": "", "broker_remaining_quantity": 0.0,
            "broker_market_cap_price": 0.0, "instrument": "", "side": "",
            "qty": 0.0, "price": 0.0, "status": "", "reason": "", "source": "",
        }
    result: list[dict[str, Any]] = []
    meta = base(SIMULATOR_STATE_META)
    meta["order_id"] = int(state["max_order_id"])
    meta["broker_request_id"] = int(state["admitted_orders"])
    meta["status"] = "complete"
    result.append(meta)
    for instrument, signed in sorted(state["positions"].items()):
        if not math.isfinite(float(signed)):
            raise v1.GenerationError("OMS_SIMULATOR_STATE_POSITION_OVERFLOW")
        if float(signed) == 0.0:
            continue
        row = base(SIMULATOR_STATE_POSITION)
        row["event_id"] = f"{SIMULATOR_STATE_POSITION}:{instrument}"
        row["instrument"] = instrument
        row["side"] = "BUY" if float(signed) > 0.0 else "SELL"
        row["qty"] = abs(float(signed))
        row["status"] = "complete"
        result.append(row)
    ready = base(SIMULATOR_STATE_READY)
    ready["order_id"] = int(state["max_order_id"])
    ready["broker_request_id"] = int(state["admitted_orders"])
    ready["status"] = "complete"
    result.append(ready)
    return result


def _read_generation_segment(root: Path, max_bytes: int, max_records: int,
                             max_record_bytes: int) -> list[dict[str, Any]]:
    path = root / "segment-000001.jsonl"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not v1._private_regular(info):
            raise v1.GenerationError("OMS_GENERATION_UNSAFE_SEGMENT")
        _, events = _strict_records(fd, 0, info.st_size, max_bytes=max_bytes,
                                    max_records=max_records,
                                    max_record_bytes=max_record_bytes)
        return events
    finally:
        os.close(fd)


def _reconstruct_simulator_state(store: Path, generation: str, *, max_bytes: int,
                                 max_records: int, max_record_bytes: int) -> dict[str, Any]:
    chain = _generation_chain(store, generation)
    base_index = 0
    for index, (_, manifest) in enumerate(chain):
        if manifest.get("schema") == v1.SCHEMA:
            base_index = index
    state = _empty_simulator_state()
    applied = False
    for item_generation, _ in chain[base_index:]:
        events = _read_generation_segment(store / item_generation, max_bytes,
                                          max_records, max_record_bytes)
        state = _apply_simulator_events(state, events, require_new_order_ids=applied)
        applied = True
    return state

'''
replace_once(
    "scripts/hepta_oms_lifecycle.py",
    "def _runtime_manifest_bytes(*, generation: str, parent_generation: str,\n",
    simulator_helpers + "\ndef _runtime_manifest_bytes(*, generation: str, parent_generation: str,\n",
)
replace_once(
    "scripts/hepta_oms_lifecycle.py",
    '''        combined = parent_hot + tail_events\n        checkpoint, _, hot_replay, _ = v1._project_hot(combined)\n        _, tail_commands, _, tail_attempts = v1._project_hot(tail_events)\n''',
    '''        combined = parent_hot + tail_events\n        checkpoint, _, hot_replay, _ = v1._project_hot(combined)\n        simulator_state = _simulator_checkpoint_from_hot(parent_hot)\n        if simulator_state is None:\n            simulator_state = (_reconstruct_simulator_state(\n                store, parent_generation, max_bytes=max_bytes, max_records=max_records,\n                max_record_bytes=max_record_bytes) if parent_generation else\n                _empty_simulator_state())\n        simulator_state = _apply_simulator_events(\n            simulator_state, tail_events, require_new_order_ids=bool(parent_generation))\n        hot_replay.extend(_simulator_state_projection(simulator_state))\n        _, tail_commands, _, tail_attempts = v1._project_hot(tail_events)\n''')

# 4) Generation-aware simulator startup: verify the selected generation, ignore
#    ordinary hot replay until the checkpoint-ready marker, then apply only the
#    active tail on top of the compact checkpoint. Legacy/V1 journals retain the
#    old complete Replay path.
replace_once(
    "HeptaTrade/execution/execution_service_runtime_composition.cpp",
    '#include "execution_event_feed_server.h"\n',
    '#include "execution_event_feed_server.h"\n#include "../oms_generation_store.h"\n')
composition = read("HeptaTrade/execution/execution_service_runtime_composition.cpp")
start = composition.index("bool ExecutionServiceRuntimeComposition::RestoreSimulatorState(std::string& reason)\n{")
end = composition.index("void ExecutionServiceRuntimeComposition::RefreshSimulatorQuotes()", start)
new_restore = r'''bool ExecutionServiceRuntimeComposition::RestoreSimulatorState(std::string& reason)
{
    const long kInitialOrderId = 999999;
    long maximumOrderId = kInitialOrderId;
    std::uint64_t admittedOrderCount = 0;
    std::map<std::string, double> positions;
    std::map<long, OmsJournalEvent> tailAdmitted;
    std::map<long, OmsJournalEvent> tailFills;
    bool checkpointSeen = false;
    bool checkpointReady = false;
    bool valid = true;
    long checkpointMaximumOrderId = kInitialOrderId;

    const auto applyTail = [&](const OmsJournalEvent& event) {
        if (event.orderId > maximumOrderId) maximumOrderId = event.orderId;
        const bool fill = event.eventType == "status" && event.status == "Filled";
        if (event.eventType != "place_sent" && !fill) return;
        if (event.orderId < 0 || event.venue != "SIMULATOR" || event.account != "SIM" ||
            event.instrument.empty() || (event.side != "BUY" && event.side != "SELL") ||
            !std::isfinite(event.qty) || event.qty <= 0.0)
        {
            valid = false;
            return;
        }
        if (event.eventType == "place_sent")
        {
            const auto prior = tailAdmitted.find(event.orderId);
            if (prior != tailAdmitted.end())
            {
                if (prior->second.instrument != event.instrument || prior->second.side != event.side ||
                    prior->second.qty != event.qty || prior->second.reqId != event.reqId ||
                    prior->second.requestHash != event.requestHash)
                    valid = false;
                return;
            }
            if (event.orderId <= checkpointMaximumOrderId ||
                admittedOrderCount == std::numeric_limits<std::uint64_t>::max())
            {
                valid = false;
                return;
            }
            tailAdmitted[event.orderId] = event;
            ++admittedOrderCount;
            return;
        }
        const auto owner = tailAdmitted.find(event.orderId);
        if (!std::isfinite(event.price) || event.price <= 0.0 ||
            owner == tailAdmitted.end() || owner->second.instrument != event.instrument ||
            owner->second.side != event.side || owner->second.qty != event.qty)
        {
            valid = false;
            return;
        }
        const auto prior = tailFills.find(event.orderId);
        if (prior != tailFills.end())
        {
            if (prior->second.instrument != event.instrument || prior->second.side != event.side ||
                prior->second.qty != event.qty || prior->second.price != event.price)
                valid = false;
            return;
        }
        tailFills[event.orderId] = event;
        positions[event.instrument] += event.side == "BUY" ? event.qty : -event.qty;
        if (!std::isfinite(positions[event.instrument])) valid = false;
    };

    OmsGenerationStore generation(m_config.journalPath);
    const bool generationPresent = generation.HasStore();
    if (generationPresent)
    {
        const OmsJournalHealthSnapshot health = m_journal.GetHealthSnapshot();
        std::string generationReason;
        if (!generation.Recover(
                health.replayMaxBytes, health.replayMaxRecords,
                health.replayMaxRecordBytes,
                [&](const OmsJournalEvent& event) {
                    if (!checkpointReady && event.eventType == "simulator_state_checkpoint")
                    {
                        if (checkpointSeen || event.venue != "SIMULATOR" || event.account != "SIM" ||
                            event.orderId < kInitialOrderId || event.brokerRequestId < 0)
                        {
                            valid = false;
                            return;
                        }
                        checkpointSeen = true;
                        maximumOrderId = event.orderId;
                        checkpointMaximumOrderId = event.orderId;
                        admittedOrderCount = static_cast<std::uint64_t>(event.brokerRequestId);
                        positions.clear();
                        return;
                    }
                    if (!checkpointReady && event.eventType == "simulator_position_checkpoint")
                    {
                        if (!checkpointSeen || event.venue != "SIMULATOR" || event.account != "SIM" ||
                            event.instrument.empty() || positions.count(event.instrument) != 0 ||
                            (event.side != "BUY" && event.side != "SELL") ||
                            !std::isfinite(event.qty) || event.qty <= 0.0)
                        {
                            valid = false;
                            return;
                        }
                        positions[event.instrument] = event.side == "BUY" ? event.qty : -event.qty;
                        return;
                    }
                    if (!checkpointReady && event.eventType == "simulator_state_checkpoint_ready")
                    {
                        if (!checkpointSeen || event.venue != "SIMULATOR" || event.account != "SIM" ||
                            event.orderId != maximumOrderId || event.brokerRequestId < 0 ||
                            static_cast<std::uint64_t>(event.brokerRequestId) != admittedOrderCount)
                        {
                            valid = false;
                            return;
                        }
                        checkpointReady = true;
                        checkpointMaximumOrderId = maximumOrderId;
                        return;
                    }
                    if (checkpointReady) applyTail(event);
                }, generationReason))
        {
            reason = generationReason.empty() ?
                "EXECUTION_SIMULATOR_GENERATION_RECOVERY_FAILED" : generationReason;
            return false;
        }
        if (!valid)
        {
            reason = "EXECUTION_SIMULATOR_GENERATION_CHECKPOINT_CONFLICT";
            return false;
        }
        if (checkpointReady)
        {
            if (maximumOrderId == std::numeric_limits<long>::max())
            {
                reason = "EXECUTION_ORDER_ID_WATERMARK_EXHAUSTED";
                return false;
            }
            if (!m_venue.RestoreRiskState(positions, admittedOrderCount, reason)) return false;
            m_venue.RestoreNextOrderIdAtLeast(maximumOrderId + 1);
            reason.clear();
            return true;
        }
    }

    // Legacy and V1 generations still retain a complete JSONL ledger. Reuse
    // the original replay semantics exactly. A V2 generation without the new
    // checkpoint fails here instead of silently discarding sealed history; a
    // stopped-state `hepta_oms_lifecycle.py seal` upgrades that generation by
    // reconstructing one checkpoint from its verified lineage.
    maximumOrderId = kInitialOrderId;
    std::map<long, OmsJournalEvent> admitted;
    std::map<long, OmsJournalEvent> fills;
    valid = true;
    const int replayed = m_journal.Replay([&](const OmsJournalEvent& event) {
        if (event.orderId > maximumOrderId) maximumOrderId = event.orderId;
        const bool fill = event.eventType == "status" && event.status == "Filled";
        if (event.eventType != "place_sent" && !fill) return;
        if (event.orderId < 0 || event.venue != "SIMULATOR" || event.account != "SIM" ||
            event.instrument.empty() || (event.side != "BUY" && event.side != "SELL") ||
            !std::isfinite(event.qty) || event.qty <= 0.0)
        {
            valid = false;
            return;
        }
        if (event.eventType == "place_sent")
        {
            const auto prior = admitted.find(event.orderId);
            if (prior != admitted.end() &&
                (prior->second.instrument != event.instrument || prior->second.side != event.side ||
                 prior->second.qty != event.qty || prior->second.reqId != event.reqId ||
                 prior->second.requestHash != event.requestHash))
                valid = false;
            admitted[event.orderId] = event;
            return;
        }
        const auto owner = admitted.find(event.orderId);
        if (!std::isfinite(event.price) || event.price <= 0.0 ||
            owner == admitted.end() || owner->second.instrument != event.instrument ||
            owner->second.side != event.side || owner->second.qty != event.qty)
            valid = false;
        const auto prior = fills.find(event.orderId);
        if (prior != fills.end() &&
            (prior->second.instrument != event.instrument || prior->second.side != event.side ||
             prior->second.qty != event.qty || prior->second.price != event.price))
            valid = false;
        fills[event.orderId] = event;
    });
    if (replayed < 0 || maximumOrderId == std::numeric_limits<long>::max())
    {
        reason = replayed < 0 && generationPresent ?
            "EXECUTION_SIMULATOR_GENERATION_CHECKPOINT_REQUIRED" :
            (replayed < 0 ? "EXECUTION_OMS_REPLAY_FAILED" :
             "EXECUTION_ORDER_ID_WATERMARK_EXHAUSTED");
        return false;
    }
    if (!valid)
    {
        reason = "EXECUTION_SIMULATOR_RISK_REPLAY_CONFLICT";
        return false;
    }
    positions.clear();
    for (const auto& fill : fills)
    {
        const auto& event = fill.second;
        positions[event.instrument] += event.side == "BUY" ? event.qty : -event.qty;
        if (!std::isfinite(positions[event.instrument]))
        {
            reason = "EXECUTION_SIMULATOR_POSITION_REPLAY_OVERFLOW";
            return false;
        }
    }
    if (!m_venue.RestoreRiskState(
            positions, static_cast<std::uint64_t>(admitted.size()), reason))
        return false;
    m_venue.RestoreNextOrderIdAtLeast(maximumOrderId + 1);
    reason.clear();
    return true;
}
'''
write("HeptaTrade/execution/execution_service_runtime_composition.cpp",
      composition[:start] + new_restore + composition[end:])

# 5) Focused lifecycle tests: checkpoint continuity, streaming validation and an
#    unbounded-by-count lineage traversal. The existing process suite gets a
#    real daemon stop/seal/restart case for installed acceptance.
test_path = "tests/python/test_oms_lifecycle_rotation.py"
tests = read(test_path)
tests = tests.replace("import unittest\n", "import unittest\nfrom unittest import mock\n", 1)
insert = r'''
    def test_simulator_checkpoint_carries_position_count_and_watermark_across_seals(self) -> None:
        def sim(kind: str, order_id: int, side: str, qty: float, *, status: str = "", price: float = 1.1, ts: int = 1000) -> dict:
            value = event(kind, f"sim-{order_id}", f"hash-{order_id}", status=status,
                          order_id=order_id, ts_ms=ts)
            value.update(venue="SIMULATOR", account="SIM", execution_domain="SIM:fixture",
                         side=side, qty=qty, price=price, instrument="EUR.USD")
            return value

        self.journal.write_bytes(encode([
            sim("place_sent", 1000000, "BUY", 10.0, status="submitted", ts=1000),
            sim("status", 1000000, "BUY", 10.0, status="Filled", ts=1001),
        ]))
        os.chmod(self.journal, 0o600)
        first = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        first_hot = lifecycle._read_hot(self.store / first["generation"], 1024 * 1024, 1024, 262144)
        state = lifecycle._simulator_checkpoint_from_hot(first_hot)
        self.assertEqual(state["max_order_id"], 1000000)
        self.assertEqual(state["admitted_orders"], 1)
        self.assertEqual(state["positions"], {"EUR.USD": 10.0})

        self.append([
            sim("place_sent", 1000001, "SELL", 4.0, status="submitted", ts=2000),
            sim("status", 1000001, "SELL", 4.0, status="Filled", ts=2001),
        ])
        second = lifecycle.seal_generation(self.journal, self.store, stopped=True)
        second_hot = lifecycle._read_hot(self.store / second["generation"], 1024 * 1024, 1024, 262144)
        state = lifecycle._simulator_checkpoint_from_hot(second_hot)
        self.assertEqual(state["max_order_id"], 1000001)
        self.assertEqual(state["admitted_orders"], 2)
        self.assertEqual(state["positions"], {"EUR.USD": 6.0})
        lifecycle.verify_generation(self.store, journal=self.journal)

    def test_generation_chain_has_no_arbitrary_count_ceiling(self) -> None:
        manifests = {
            f"g{index}": {"generation": f"g{index}",
                           "parent_generation": f"g{index - 1}" if index else ""}
            for index in range(1500)
        }
        with mock.patch.object(lifecycle, "_manifest_for",
                               side_effect=lambda _store, generation: manifests[generation]):
            chain = lifecycle._generation_chain(Path("/unused"), "g1499")
        self.assertEqual(len(chain), 1500)
        self.assertEqual(chain[0][0], "g0")
        self.assertEqual(chain[-1][0], "g1499")

'''
needle = "    def test_v1_generation_upgrades_to_v2_delta_and_exports_back_to_legacy(self) -> None:\n"
if tests.count(needle) != 1:
    raise RuntimeError("lifecycle test insertion point changed")
tests = tests.replace(needle, insert + needle, 1)
write(test_path, tests)

process_path = "tests/python/test_installed_runtime_processes.py"
process_tests = read(process_path)
process_insert = r'''
    def test_installed_simulator_generation_seal_restart_preserves_state(self):
        runtime = self.fixture("generation-seal-restart")
        runtime.start(self.slots["CANDIDATE"])
        runtime.provision()
        command, fields, order = runtime.place("BUY", 25, "1.1002")
        runtime.wait_position(25)
        runtime.wait_no_orders()
        sends = runtime.send_count()
        runtime.stop()

        journal = runtime.root / "es/oms-journal.jsonl"
        store = Path(str(journal) + ".generations")
        helper = self.slots["CANDIDATE"] / "libexec/heptatrader/hepta_oms_lifecycle.py"
        sealed = subprocess.run([
            sys.executable, "-S", str(helper), "seal", "--journal", str(journal),
            "--store", str(store), "--stopped-state"], env=CLEAN_ENV,
            user=EXECUTION_UID, group=TEST_GID, extra_groups=[], capture_output=True,
            text=True, timeout=20)
        self.assertEqual(sealed.returncode, 0, sealed.stderr)
        self.assertEqual(json.loads(sealed.stdout)["authorization_effect"], "NONE")

        runtime.start(self.slots["CANDIDATE"])
        runtime.wait_position(25)
        status = runtime.call("execution.get_command_status", [f"command_id={command}"])["payload"]
        self.assertEqual(status["order_id"], order)
        runtime.call("trade.place_order", fields, call_id=command, duplicate=True)
        self.assertEqual(runtime.send_count(), sends)
        _, _, next_order = runtime.place("SELL", 25, "1.1000")
        self.assertGreater(next_order, order)
        runtime.wait_position(0)
        runtime.wait_no_orders()
        self.record_success("simulator-generation-seal-restart", runtime,
            ["fill-before-seal", "stopped-state-v2-seal", "restart-position-restored",
             "command-identity-preserved", "order-watermark-advanced", "final-flat"])

'''
needle = "    def test_installed_archive_replay_and_explicit_downgrade_restore(self):\n"
if process_tests.count(needle) != 1:
    raise RuntimeError("installed process test insertion point changed")
process_tests = process_tests.replace(needle, process_insert + needle, 1)
write(process_path, process_tests)

# Keep module docs honest about the now-integrated restart contract and old-v2
# upgrade requirement. Host/Broker evidence stays explicitly external.
simulator_doc = "docs/modules/simulator.md"
value = read(simulator_doc)
anchor = "## Recovery"
if anchor in value and "simulator_state_checkpoint" not in value:
    value = value.replace(anchor, anchor + "\n\nStopped-state OMS V2 sealing now publishes a digest-bound `simulator_state_checkpoint` projection at the end of the verified hot replay. Simulator startup validates the selected generation, restores net positions, admitted-order count and the order-id watermark from that projection, then applies only the lineage-bound active tail. A V2 generation produced before this projection existed must be re-sealed once while all writers are stopped; the maintenance tool reconstructs the checkpoint from the verified lineage and never treats a missing checkpoint as empty history.\n", 1)
write(simulator_doc, value)

oms_doc = "docs/modules/oms-journal.md"
value = read(oms_doc)
if "account-domain-time-v1" not in value:
    value += "\n### Long-history maintenance\n\nV2 verification streams cumulative command and send-attempt indexes instead of materializing them as Python lists. New send-attempt indexes are ordered by `account-domain-time-v1`, enabling native lower-bound window scans. Downgrade lineage traversal is cycle-bounded by the actual parent graph rather than an arbitrary generation-count ceiling; cumulative index storage is still retained deliberately for permanent command identity and must be measured as history grows.\n"
write(oms_doc, value)

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
print("full roadmap core patch applied")
