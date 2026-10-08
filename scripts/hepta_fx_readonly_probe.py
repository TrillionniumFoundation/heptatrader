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
        self.tick_fields: dict[int, int] = {}
        self.valid_pair_tick_events = 0
        self.last_valid_pair_at_ms: int | None = None

    def market_type(self, value: int) -> None:
        self.data_type = value
        if value != 1:
            self.bid = None
            self.ask = None

    def price(self, field: int, value: float, at_ms: int) -> None:
        self.tick_count += 1
        self.tick_fields[field] = self.tick_fields.get(field, 0) + 1
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
        if self.evidence(at_ms) is not None:
            self.valid_pair_tick_events += 1
            self.last_valid_pair_at_ms = at_ms

    def leg_ages(self, now_ms: int) -> dict:
        return {
            "bid_age_ms": now_ms - self.bid[1] if self.bid else None,
            "ask_age_ms": now_ms - self.ask[1] if self.ask else None,
        }

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


class QuoteContinuity:
    """Sampled freshness evidence, separate from broker tick/valuation time.

    The observer checks BOTH market-data legs and the CASH-farm lease on
    each bounded sample. No callback is backdated or re-stamped by sampling.
    A finite read-only window never proves future continuous availability.
    """

    def __init__(self):
        self.samples = 0
        self.fresh_samples = 0
        self.after_first_missing_samples = 0
        self.first_fresh_at_ms = None
        self.last_fresh_at_ms = None
        self.first_sample_at_ms = None
        self.last_sample_at_ms = None
        self.missing_run_since_ms = None
        self.longest_post_first_missing_ms = 0
        self.farm_missing_samples = 0

    def sample(self, quote: LiveQuoteState, at_ms: int, farm_ready: bool) -> bool:
        if (type(at_ms) is not int or at_ms <= 0 or
                self.last_sample_at_ms is not None and
                at_ms <= self.last_sample_at_ms):
            raise ProbeViolation("PROBE_SAMPLE_TIME_INVALID")
        if self.first_sample_at_ms is None:
            self.first_sample_at_ms = at_ms
        self.last_sample_at_ms = at_ms
        self.samples += 1
        if not farm_ready:
            self.farm_missing_samples += 1
        fresh = farm_ready and quote.evidence(at_ms) is not None
        if fresh:
            self.fresh_samples += 1
            if self.first_fresh_at_ms is None:
                self.first_fresh_at_ms = at_ms
            self.last_fresh_at_ms = at_ms
            self.missing_run_since_ms = None
        elif self.first_fresh_at_ms is not None:
            self.after_first_missing_samples += 1
            if self.missing_run_since_ms is None:
                self.missing_run_since_ms = at_ms
            self.longest_post_first_missing_ms = max(
                self.longest_post_first_missing_ms,
                at_ms - self.missing_run_since_ms)
        return fresh

    def receipt(self) -> dict:
        return {
            "schema": "heptatrader.fx-freshness-window.v1",
            "status": "OBSERVED" if self.samples else "NO_SAMPLES",
            "sample_count": self.samples,
            "fresh_pair_sample_count": self.fresh_samples,
            "fresh_pair_sample_percent": (
                100 * self.fresh_samples // self.samples if self.samples else 0),
            "first_fresh_pair_sample_at_ms": self.first_fresh_at_ms,
            "last_fresh_pair_sample_at_ms": self.last_fresh_at_ms,
            "first_sample_at_ms": self.first_sample_at_ms,
            "last_sample_at_ms": self.last_sample_at_ms,
            "missing_after_first_pair_samples": self.after_first_missing_samples,
            "longest_sampled_missing_after_first_pair_ms":
                self.longest_post_first_missing_ms,
            "farm_unavailable_sample_count": self.farm_missing_samples,
            "original_quote_freshness_limit_ms": 5000,
            "sampled_post_first_gap_over_limit": (
                self.longest_post_first_missing_ms > 5000),
            "synchronous_broker_valuation_time_proven": False,
            "production_recovery_qualified": False,
        }


class CashFarmLease:
    """Only a scoped current-session CASH-farm 2104 opens the quote probe."""
    def __init__(self):
        self.ready = threading.Event()
        self.farm_id: str | None = None
        self.ready_since = 0.0

    def observe(self, code: int, message: str) -> None:
        farm = message.rsplit(":", 1)[-1].strip().lower()
        if farm not in ("cashfarm", "hfarm"):
            return  # Other farms' 2104 do not authorize CASH market data.
        if code == 2104:
            self.farm_id = farm
            self.ready_since = time.monotonic()
            self.ready.set()
        elif code in (2103, 2119):
            self.ready.clear()
            self.ready_since = 0.0

    def wait_stable(self, stable_sec: float, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.ready.is_set() and time.monotonic() - self.ready_since >= stable_sec:
                return True
            time.sleep(0.02)
        return False


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
                handshake_sec: float = 8.0, farm_stability_sec: float = 0.5) -> dict:
    EClient, EWrapper, Contract = read_sdk(sdk)
    gate = QuoteWireGate(client_id)
    quotes = LiveQuoteState()
    farm = CashFarmLease()
    continuity = QuoteContinuity()
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
            if request_id == -1:
                farm.observe(code, message)
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
        elif not farm.wait_stable(farm_stability_sec, handshake_sec):
            result["status"] = "CASH_FARM_UNATTESTED"
        elif client.lost or not client.isConnected() or not farm.ready.is_set():
            result["status"] = "CONNECTION_LOST"
        else:
            contract = Contract()
            contract.symbol = "EUR"
            contract.secType = "CASH"
            contract.exchange = "IDEALPRO"
            contract.currency = "USD"
            # Read-only request after the scoped farm witness stabilizes.
            # A 2104 acknowledgment itself never becomes a quote.
            client.reqMarketDataType(1)
            client.reqMktData(REQUEST_ID, contract, "", False, False, [])
            deadline = time.monotonic() + window_sec
            continuity.sample(quotes, time.time_ns() // 1_000_000,
                              farm.ready.is_set())
            while time.monotonic() < deadline:
                if client.lost or not client.isConnected() or not farm.ready.is_set():
                    break
                time.sleep(max(0.0, min(0.1, deadline - time.monotonic())))
                continuity.sample(quotes, time.time_ns() // 1_000_000,
                                  farm.ready.is_set())
            result["live_quote"] = quotes.evidence(time.time_ns() // 1_000_000)
            result["status"] = ("CONNECTION_LOST" if client.lost or not client.isConnected()
                                else "CASH_FARM_LOST" if not farm.ready.is_set()
                                else "LIVE_BID_ASK" if result["live_quote"] is not None
                                else "NO_LIVE_BID_ASK")
            if result["status"] != "LIVE_BID_ASK":
                result["live_quote"] = None
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
        result["cash_farm_attested_at_end"] = farm.ready.is_set()
        result["cash_farm_id"] = farm.farm_id
        result["market_data_type"] = quotes.data_type
        result["tick_callbacks"] = quotes.tick_count
        result["tick_field_counts"] = quotes.tick_fields
        result["invalid_tick_callbacks"] = quotes.invalid_tick_count
        result["valid_pair_tick_events"] = quotes.valid_pair_tick_events
        result["last_valid_pair_at_ms"] = quotes.last_valid_pair_at_ms
        result["freshness_window"] = continuity.receipt()
        result["leg_ages_at_end_ms"] = quotes.leg_ages(
            time.time_ns() // 1_000_000)
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

