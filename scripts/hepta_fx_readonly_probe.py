#!/usr/bin/env python3
"""Strictly read-only, isolated IB PAPER EUR/USD quote transport probe.

No NAV publication, order API, account subscription, account values, or
production service control. All outbound SDK messages are exact allowlisted.
This diagnoses market-data transport; it NEVER certifies a Broker NAV mark.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import sys
import threading
import time

REQUEST_ID = 4
INSTRUMENT = "EUR|CASH|IDEALPRO|USD"
PINNED_MARKET_DATA_WIRE = [
    "1", "11", str(REQUEST_ID), "0", "EUR", "CASH", "", "0.0",
    "", "", "IDEALPRO", "", "USD", "", "", "0", "", "0", "0",
]


class ProbeViolation(Exception):
    pass


class QuoteWireGate:
    """No account, position, order, or generic instrument API send exists."""
    def __init__(self, client_id: int):
        if type(client_id) is not int or not 1 <= client_id < 2**31:
            raise ProbeViolation("PROBE_CLIENT_ID_INVALID")
        self.client_id = client_id
        self.started = False
        self.subscribed = False
        self.cancelled = False
        self.counts: dict[str, int] = {}

    def check(self, message: str) -> None:
        if not isinstance(message, str) or not message.endswith("\0"):
            raise ProbeViolation("PROBE_SEND_FORMAT_INVALID")
        fields = message.rstrip("\0").split("\0")
        opcode = fields[0]
        if opcode == "71":
            allowed = (not self.started and fields == [
                "71", "2", str(self.client_id)])
            if allowed:
                self.started = True
        elif opcode == "59":
            allowed = self.started and fields == ["59", "1", "1"]
        elif opcode == "1":
            allowed = self.started and not self.subscribed and (
                fields == PINNED_MARKET_DATA_WIRE)
            if allowed:
                self.subscribed = True
        elif opcode == "2":
            allowed = (self.subscribed and not self.cancelled and
                       fields == ["2", "2", str(REQUEST_ID)])
            if allowed:
                self.cancelled = True
        else:
            allowed = False
        if not allowed:
            raise ProbeViolation("PROBE_MUTATION_OR_FOREIGN_SEND_DENIED")
        self.counts[opcode] = self.counts.get(opcode, 0) + 1


class LiveQuoteState:
    """No cache across sessions; require two independently observed live legs."""
    def __init__(self):
        self.data_type: int | None = None
        self.bid: tuple[float, int] | None = None
        self.ask: tuple[float, int] | None = None
        self.tick_count = 0
        self.invalid_tick_count = 0

    def market_type(self, value: int) -> None:
        self.data_type = value
        if value != 1:
            self.bid = None
            self.ask = None

    def price(self, field: int, value: float, at_ms: int) -> None:
        self.tick_count += 1
        if (self.data_type != 1 or field not in (1, 2) or
                not isinstance(value, (int, float)) or
                not math.isfinite(value) or value <= 0 or
                type(at_ms) is not int or at_ms <= 0):
            self.invalid_tick_count += 1
            return
        if field == 1:
            self.bid = (float(value), at_ms)
        else:
            self.ask = (float(value), at_ms)

    def evidence(self, now_ms: int, max_age_ms: int = 5000) -> dict | None:
        if self.data_type != 1 or self.bid is None or self.ask is None:
            return None
        (bid, bid_at), (ask, ask_at) = self.bid, self.ask
        if (ask < bid or any(now_ms < at or now_ms - at > max_age_ms
                             for at in (bid_at, ask_at))):
            return None
        return {
            "bid": format(bid, ".17g"), "ask": format(ask, ".17g"),
            "bid_callback_at_ms": bid_at, "ask_callback_at_ms": ask_at,
            "oldest_leg_age_ms": max(now_ms - bid_at, now_ms - ask_at),
            "broker_valuation_time_proven": False,
        }


def read_sdk(path: Path):
    path = path.resolve(strict=True)
    if not (path / "ibapi/client.py").is_file():
        raise ProbeViolation("IB_SDK_NOT_FOUND")
    sys.path.insert(0, str(path))
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    return EClient, EWrapper, Contract


def capture_one(client_id: int, sdk: Path, window_sec: float = 8.0,
                handshake_sec: float = 8.0) -> dict:
    EClient, EWrapper, Contract = read_sdk(sdk)
    gate = QuoteWireGate(client_id)
    quotes = LiveQuoteState()
    errors: list[dict] = []
    ready = threading.Event()

    class QuoteReader(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.lost = False

        def sendMsg(self, message):
            gate.check(message)
            return EClient.sendMsg(self, message)

        def nextValidId(self, _):
            ready.set()

        def error(self, request_id, code, message, *args):
            if len(errors) < 32:
                errors.append({"request_id": request_id, "code": code})

        def marketDataType(self, request_id, data_type):
            if request_id == REQUEST_ID:
                quotes.market_type(data_type)

        def tickPrice(self, request_id, field, price, attrib):
            if request_id == REQUEST_ID:
                quotes.price(field, price, time.time_ns() // 1_000_000)

        def connectionClosed(self):
            self.lost = True

        def placeOrder(self, *args, **kwargs):
            raise ProbeViolation("PROBE_ORDER_FORBIDDEN")

        def cancelOrder(self, *args, **kwargs):
            raise ProbeViolation("PROBE_ORDER_FORBIDDEN")

        def reqGlobalCancel(self, *args, **kwargs):
            raise ProbeViolation("PROBE_ORDER_FORBIDDEN")

    client = QuoteReader()
    worker = None
    result = {
        "schema": "heptatrader.fx-read-only-broker-probe.v1",
        "status": "UNAVAILABLE", "read_only": True,
        "mutation_authorized": False, "same_broker_valuation_time_proven": False,
        "instrument": INSTRUMENT, "quote_source": "IB_API_LIVE_BID_ASK",
    }
    try:
        client.connect("127.0.0.1", 4002, client_id)
        worker = threading.Thread(target=client.run, daemon=True)
        worker.start()
        if not ready.wait(handshake_sec):
            result["status"] = "HANDSHAKE_TIMEOUT"
        else:
            contract = Contract()
            contract.symbol = "EUR"
            contract.secType = "CASH"
            contract.exchange = "IDEALPRO"
            contract.currency = "USD"
            client.reqMarketDataType(1)
            client.reqMktData(REQUEST_ID, contract, "", False, False, [])
            # Sample the entire specified window; a prior valid quote is never
            # treated as refreshed by the passage of local time.
            deadline = time.monotonic() + window_sec
            while time.monotonic() < deadline:
                if client.lost or not client.isConnected():
                    break
                time.sleep(max(0.0, min(0.1, deadline - time.monotonic())))
            result["live_quote"] = quotes.evidence(time.time_ns() // 1_000_000)
            result["status"] = ("LIVE_BID_ASK" if result["live_quote"] is not None
                                else "CONNECTION_LOST" if client.lost
                                else "NO_LIVE_BID_ASK")
    except (OSError, ValueError, ProbeViolation) as error:
        result["status"] = ("PROBE_UNSAFE_SEND" if isinstance(error, ProbeViolation)
                            else "PROBE_TRANSPORT_FAILURE")
        result["failure_type"] = type(error).__name__
    finally:
        try:
            if client.isConnected() and gate.subscribed and not gate.cancelled:
                client.cancelMktData(REQUEST_ID)
        except Exception:
            result["cancel_error"] = "PROBE_CANCEL_FAILED"
        try:
            client.disconnect()
        except Exception:
            result["disconnect_error"] = "PROBE_DISCONNECT_FAILED"
        if worker is not None:
            worker.join(2)
        result["market_data_type"] = quotes.data_type
        result["tick_callbacks"] = quotes.tick_count
        result["invalid_tick_callbacks"] = quotes.invalid_tick_count
        result["error_codes"] = errors
        result["allowed_send_counts"] = gate.counts
        result["order_send_count"] = 0  # wire allowlist denies every order opcode
        if result.get("cancel_error"):
            result["status"] = "PROBE_CANCEL_FAILED"
    return result


def validate_isolated_client_id(client_id: int, home: Path) -> None:
    """Never share a client ID with a live trader or account observer.

    This intentionally requires the local immutable PAPER period/config to
    exist. A missing or malformed safety witness is a hard refusal.
    """
    if type(client_id) is not int or not 20000 <= client_id <= 30000:
        raise ProbeViolation("PROBE_CLIENT_ID_OUTSIDE_ISOLATED_RANGE")
    try:
        config = json.loads((home / ".config/heptatrader/account-observer.json").read_text())
        period = json.loads((home / ".config/heptatrader/pnl-period.json").read_text())
        assert period["schema"] == "heptatrader.pnl-period.v1"
        assert config["schema"] == "heptatrader.account-observer-config.v1"
        owners = {config["client_id"]}
        owners.update(v["broker_client_id"] for v in period["instruments"].values())
        if client_id in owners:
            raise ProbeViolation("PROBE_CLIENT_COLLIDES_WITH_RUNNING_OWNER")
        if any(type(i) is not int for i in owners):
            raise ProbeViolation("PROBE_OWNER_ID_INVALID")
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as error:
        raise ProbeViolation("PROBE_OWNER_FENCE_UNAVAILABLE") from error


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-id", type=int, required=True)
    ap.add_argument("--sdk", type=Path, required=True)
    ap.add_argument("--window-sec", type=float, default=8)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--reconnect-delay-sec", type=float, default=12)
    args = ap.parse_args(argv)
    if not 1 <= args.cycles <= 3 or not 1 <= args.window_sec <= 20:
        ap.error("cycles 1..3 and window-sec 1..20 required")
    if not 10 <= args.reconnect_delay_sec <= 120:
        ap.error("reconnect-delay-sec 10..120 required")
    try:
        validate_isolated_client_id(args.client_id, Path.home())
    except ProbeViolation as error:
        ap.error(str(error))
    logging.disable(logging.CRITICAL)
    result = []
    for i in range(args.cycles):
        if i:
            time.sleep(args.reconnect_delay_sec)
        record = capture_one(args.client_id, args.sdk, args.window_sec)
        record["cycle"] = i + 1
        result.append(record)
        print(json.dumps(record, sort_keys=True), flush=True)
    return 0 if all(r["status"] == "LIVE_BID_ASK" for r in result) else 2


if __name__ == "__main__":
    raise SystemExit(main())

