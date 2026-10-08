#!/usr/bin/env python3
"""Isolated, order-incapable EUR/USD tick-by-tick BidAsk source-time probe."""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import threading
import time

from hepta_fx_readonly_probe import (
    CashFarmLease, ProbeViolation, read_sdk, validate_isolated_client_id,
)

TICK_REQUEST_ID = 5
TICK_WIRE = [
    "97", str(TICK_REQUEST_ID), "0", "EUR", "CASH", "", "0.0", "", "",
    "IDEALPRO", "", "USD", "", "", "BidAsk", "0", "0",
]


class TickWireGate:
    """A wire-level allowlist: handshake, live mode, one TBT ticker, cancel."""
    def __init__(self, client_id: int):
        self.client_id = client_id
        self.started = False
        self.subscribed = False
        self.cancelled = False
        self.counts: dict[str, int] = {}

    def check(self, msg: str) -> None:
        if not isinstance(msg, str) or not msg.endswith("\0"):
            raise ProbeViolation("TBT_WIRE_INVALID")
        fields = msg.rstrip("\0").split("\0")
        op = fields[0]
        if op == "71":
            valid = not self.started and fields == ["71", "2", str(self.client_id)]
            if valid:
                self.started = True
        elif op == "59":
            valid = self.started and fields == ["59", "1", "1"]
        elif op == "97":
            valid = self.started and not self.subscribed and fields == TICK_WIRE
            if valid:
                self.subscribed = True
        elif op == "98":
            valid = self.subscribed and not self.cancelled and fields == ["98", str(TICK_REQUEST_ID)]
            if valid:
                self.cancelled = True
        else:
            valid = False
        if not valid:
            raise ProbeViolation("TBT_MUTATION_OR_FOREIGN_SEND_DENIED")
        self.counts[op] = self.counts.get(op, 0) + 1


class TimedBidAsk:
    """BID and ASK are bound to one Broker source tick, never re-stamped."""
    def __init__(self):
        self.lock = threading.Lock()
        self.last: tuple[int, float, float, int] | None = None
        self.callbacks = 0
        self.accepted = 0
        self.rejected = 0
        self.source_regressions = 0
        self.source_delay_samples: list[int] = []

    def receive(self, source_seconds: int, bid: float, ask: float, received_ms: int) -> None:
        with self.lock:
            self.callbacks += 1
            delay = received_ms - 1000 * source_seconds if type(source_seconds) is int else 999999
            valid = (
                type(source_seconds) is int and source_seconds > 0 and
                math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask and
                -1000 <= delay <= 5000
            )
            if self.last is not None and source_seconds < self.last[0]:
                self.source_regressions += 1
                valid = False
            if not valid:
                self.rejected += 1
                self.last = None
                return
            self.accepted += 1
            self.last = source_seconds, float(bid), float(ask), received_ms
            if len(self.source_delay_samples) < 1024:
                self.source_delay_samples.append(delay)

    def fresh(self, now_ms: int, age_limit_ms: int = 5000) -> dict | None:
        with self.lock:
            if self.last is None:
                return None
            source_seconds, bid, ask, received_ms = self.last
            source_age = now_ms - 1000 * source_seconds
            receipt_age = now_ms - received_ms
            if not (0 <= source_age <= age_limit_ms and 0 <= receipt_age <= age_limit_ms):
                return None
            return {
                "bid": format(bid, ".17g"), "ask": format(ask, ".17g"),
                "broker_tick_seconds": source_seconds,
                "received_at_ms": received_ms,
                "source_age_ms": source_age, "receipt_age_ms": receipt_age,
                "account_valuation_time_proven": False,
            }


def capture_one(client_id: int, sdk: Path, window_sec: float = 30.0) -> dict:
    EClient, EWrapper, Contract = read_sdk(sdk)
    gate = TickWireGate(client_id)
    quote = TimedBidAsk()
    farm = CashFarmLease()
    ready = threading.Event()
    errors: list[int] = []
    global_errors: list[int] = []

    class Reader(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.lost = False
            self.nonlive = False

        def sendMsg(self, msg):
            gate.check(msg)
            return EClient.sendMsg(self, msg)

        def nextValidId(self, _):
            ready.set()

        def error(self, req, code, message, *args):
            if req == -1:
                farm.observe(code, message)
                if code not in (2104, 2106, 2107, 2108, 2158, 2172) and len(global_errors) < 20:
                    global_errors.append(int(code))
            elif req == TICK_REQUEST_ID and len(errors) < 20:
                errors.append(int(code))

        def marketDataType(self, req, data_type):
            if req == TICK_REQUEST_ID and data_type != 1:
                self.nonlive = True

        def tickByTickBidAsk(self, req, source_seconds, bid, ask, bid_size, ask_size, attrib):
            if req == TICK_REQUEST_ID:
                quote.receive(source_seconds, bid, ask, time.time_ns() // 1_000_000)

        def connectionClosed(self):
            self.lost = True

    client = Reader()
    worker = None
    result = {
        "schema": "heptatrader.fx-tick-by-tick-readonly-probe.v1",
        "status": "UNAVAILABLE", "read_only": True,
        "mutation_authorized": False, "account_valuation_time_proven": False,
        "instrument": "EUR|CASH|IDEALPRO|USD",
        "source": "IB_TICK_BY_TICK_BID_ASK", "quote_age_limit_ms": 5000,
    }
    sample_count = fresh_samples = longest_stale_run_ms = stale_run_ms = 0
    started_receiving = False
    try:
        client.connect("127.0.0.1", 4002, client_id)
        worker = threading.Thread(target=client.run, daemon=True)
        worker.start()
        if not ready.wait(8):
            result["status"] = "HANDSHAKE_TIMEOUT"
        elif not farm.wait_stable(0.5, 8):
            result["status"] = "CASH_FARM_UNATTESTED"
        elif client.lost or not client.isConnected():
            result["status"] = "CONNECTION_LOST"
        else:
            contract = Contract()
            contract.symbol = "EUR"
            contract.secType = "CASH"
            contract.exchange = "IDEALPRO"
            contract.currency = "USD"
            client.reqMarketDataType(1)
            client.reqTickByTickData(TICK_REQUEST_ID, contract, "BidAsk", 0, False)
            deadline = time.monotonic() + window_sec
            while (time.monotonic() < deadline and not errors and not global_errors
                   and farm.ready.is_set() and not client.lost and not client.nonlive):
                at_ms = time.time_ns() // 1_000_000
                evidence = quote.fresh(at_ms)
                if quote.accepted:
                    started_receiving = True
                if started_receiving:
                    sample_count += 1
                    if evidence is not None:
                        fresh_samples += 1
                        stale_run_ms = 0
                    else:
                        stale_run_ms += 100
                        longest_stale_run_ms = max(longest_stale_run_ms, stale_run_ms)
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            end_quote = quote.fresh(time.time_ns() // 1_000_000)
            result["fresh_pair_at_end"] = end_quote
            result["status"] = (
                "CONNECTION_LOST" if client.lost or not client.isConnected() else
                "CASH_FARM_LOST" if not farm.ready.is_set() else
                "REQUEST_REJECTED" if errors else
                "BROKER_GLOBAL_ERROR" if global_errors else
                "NONLIVE_MARKET_DATA" if client.nonlive else
                "NO_BIDASK_CALLBACK" if quote.callbacks == 0 else
                "FRESH_AT_END" if end_quote is not None else "STALE_AT_END"
            )
    except (OSError, ValueError, ProbeViolation) as exc:
        result["status"] = "FORBIDDEN_WIRE" if isinstance(exc, ProbeViolation) else "TRANSPORT_FAILURE"
        result["error_type"] = type(exc).__name__
    finally:
        try:
            if client.isConnected() and gate.subscribed and not gate.cancelled:
                client.cancelTickByTickData(TICK_REQUEST_ID)
        except Exception:
            result["cancel_failed"] = True
        try:
            client.disconnect()
        except Exception:
            result["disconnect_failed"] = True
        if worker:
            worker.join(2)
        result.update(
            cash_farm_ready_at_end=farm.ready.is_set(),
            cash_farm_id=farm.farm_id,
            total_callbacks=quote.callbacks,
            accepted_callbacks=quote.accepted,
            rejected_callbacks=quote.rejected,
            source_time_regressions=quote.source_regressions,
            source_delay_ms_min=min(quote.source_delay_samples) if quote.source_delay_samples else None,
            source_delay_ms_max=max(quote.source_delay_samples) if quote.source_delay_samples else None,
            fresh_samples=fresh_samples, observed_samples=sample_count,
            freshness_fraction=round(fresh_samples / sample_count, 4) if sample_count else None,
            longest_stale_run_after_first_tick_ms=longest_stale_run_ms,
            ticker_error_codes=errors, global_error_codes=global_errors,
            allowed_wire_counts=gate.counts, order_send_count=0,
        )
        if result.get("cancel_failed") or result.get("disconnect_failed"):
            result["status"] = "CLEANUP_FAILED"
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-id", type=int, required=True)
    ap.add_argument("--sdk", type=Path, required=True)
    ap.add_argument("--window-sec", type=int, default=30)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--reconnect-delay-sec", type=int, default=20)
    args = ap.parse_args(argv)
    if not 10 <= args.window_sec <= 60 or not 1 <= args.cycles <= 3:
        ap.error("window 10..60; cycles 1..3")
    if args.reconnect_delay_sec < 20:
        ap.error("same-contract retry interval must exceed 15s")
    validate_isolated_client_id(args.client_id, Path.home())
    logging.disable(logging.CRITICAL)
    results = []
    for cycle in range(args.cycles):
        if cycle:
            time.sleep(args.reconnect_delay_sec)
        result = capture_one(args.client_id, args.sdk, args.window_sec)
        result["cycle"] = cycle + 1
        results.append(result)
        print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if all(r["status"] == "FRESH_AT_END" for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
