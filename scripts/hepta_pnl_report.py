#!/usr/bin/env python3
"""Read-only PnL client/producer. Never submits, reconciles or changes a driver.

Use --init-from-reset once to attach to an already completed user reset. Default
invocation collects fresh native snapshots. --watch writes only the PnL output.
The trading services are neither started nor stopped by this program.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable

from hepta_pnl_accounting import (EvidenceError, INSTRUMENTS, amount, build_report,
                                  number, positive_int, validate_period)

READ_TOOLS = frozenset({"system.get_health", "orders.list", "portfolio.list_positions", "market.get_quote"})
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_RECORDS = 65536


def now_ms() -> int:
    return time.time_ns() // 1000000


def strict_json(text: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def invalid(value):
        raise EvidenceError("NONFINITE_JSON_VALUE")
    result = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(result, dict):
        raise EvidenceError("JSON_OBJECT_REQUIRED")
    return result


def read_private(path: Path, limit: int = 1024 * 1024) -> tuple[bytes, tuple]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
                st.st_nlink != 1 or st.st_mode & 0o077 or st.st_size > limit):
            raise EvidenceError("UNSAFE_OR_OVERSIZE_INPUT:" + path.name)
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if len(raw) > limit or (st.st_size, st.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise EvidenceError("INPUT_CHANGED_DURING_READ")
        return raw, (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    finally:
        os.close(fd)


def fingerprint(path: Path) -> tuple:
    st = path.lstat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def load_private(path: Path) -> dict:
    return strict_json(read_private(path)[0].decode("utf-8"))


def write_snapshot(path: Path, value: dict, exclusive: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or
            stat.S_IMODE(parent.st_mode) != 0o700):
        raise EvidenceError("UNSAFE_OUTPUT_DIRECTORY")
    if path.exists() or path.is_symlink():
        read_private(path)
        if exclusive:
            raise EvidenceError("PERIOD_ALREADY_EXISTS_EXPLICIT_NEW_PERIOD_REQUIRED")
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=".pnl-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            # A racing initializer must never overwrite the winning period.
            os.link(name, path)
            os.unlink(name)
        else:
            os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


class NativeReader:
    def __init__(self, prefix: Path, config: Path, socket: Path):
        self.prefix, self.config, self.socket = prefix, config, socket

    def __call__(self, instrument: str, tool: str) -> dict:
        if instrument not in INSTRUMENTS or tool not in READ_TOOLS:
            raise EvidenceError("PNL_READ_ONLY_TOOL_ALLOWLIST")
        token = self.config / ("crypto.token" if instrument == "BTC.USD" else "paper.token")
        # Never read/log the token's contents; pass the existing private path.
        args = [str(self.prefix / "bin/heptactl"), "--socket", str(self.socket),
                "--token-file", str(token), "--io-timeout-ms", "2000", "call", tool]
        if tool == "market.get_quote":
            args.append("instrument=" + instrument)
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=4)
        try:
            value = strict_json(result.stdout)
        except (ValueError, UnicodeError):
            raise EvidenceError("NATIVE_READ_TRANSPORT_FAILED") from None
        if result.returncode or value.get("status") != "ok" or not isinstance(value.get("payload"), dict):
            raise EvidenceError(str(value.get("reason_code") or "NATIVE_READ_FAILED"))
        return value["payload"]


def health_identity(health: dict) -> tuple:
    inner = health.get("execution_authoritative_health") or {}
    if (health.get("execution_mode") != "PAPER" or health.get("gateway_ready") is not True or
            health.get("remote_execution_ready") is not True or inner.get("source") != "IB" or
            inner.get("authoritative") is not True or not isinstance(health.get("execution_service_epoch"), str) or not health["execution_service_epoch"] or
            not positive_int(health.get("execution_service_fencing_generation"))):
        raise EvidenceError("NATIVE_HEALTH_NOT_AUTHORITATIVE")
    return (health["execution_service_epoch"], health["execution_service_fencing_generation"],
            health.get("tool_gateway_epoch"))


def observe(reader: Callable, period: dict, instrument: str) -> dict:
    first_health = reader(instrument, "system.get_health")
    identity = health_identity(first_health)
    orders = reader(instrument, "orders.list")
    positions = reader(instrument, "portfolio.list_positions")
    try:
        quote = reader(instrument, "market.get_quote")
        quote_error = None
    except EvidenceError as error:
        quote, quote_error = None, str(error)
    after_orders = reader(instrument, "orders.list")
    after_positions = reader(instrument, "portfolio.list_positions")
    after_health = reader(instrument, "system.get_health")
    if identity != health_identity(after_health):
        raise EvidenceError("PNL_COLLECTION_SERVICE_EPOCH_CHANGED")
    # Ignore read-time-only timestamps, never position/owner/generation changes.
    def stable_positions(value):
        value = dict(value)
        if isinstance(value.get("settlement_observation"), dict):
            value["settlement_observation"] = dict(value["settlement_observation"])
            value["settlement_observation"].pop("sampled_at_ms", None)
        return value
    if orders != after_orders or stable_positions(positions) != stable_positions(after_positions):
        raise EvidenceError("PNL_COLLECTION_BROKER_SNAPSHOT_CHANGED")
    owner = period["instruments"][instrument]
    expected = {"account": period["account"], "execution_domain": "PAPER",
                "agent_id": owner["agent_id"], "session_id": owner["session_id"]}
    if (orders.get("source") != "IB" or orders.get("authoritative") is not True or
            orders.get("global_active_orders_complete") is not True or orders.get("owner_projection_complete") is not True or
            orders.get("owner_scope") != expected or not positive_int(orders.get("active_orders_connection_epoch")) or
            not positive_int(orders.get("active_orders_generation"))):
        raise EvidenceError("PNL_COLLECTION_ORDER_OWNER_UNPROVEN")
    epoch = orders["active_orders_connection_epoch"]
    scope = positions.get("position_scope")
    quantity_rows = positions.get("positions")
    authoritative = (positions.get("source") == "IB" and positions.get("authoritative") is True and
                     scope == "PAPER_BASELINE_DELTA" and positive_int(positions.get("position_generation")) and
                     positive_int(positions.get("fx_cash_generation")))
    if not authoritative and instrument == "BTC.USD":
        physical = positions.get("settlement_observation") or {}
        authoritative = (physical.get("authoritative") is True and physical.get("source") == "IB_REQ_POSITIONS" and
                         physical.get("scope") == "PHYSICAL_CRYPTO_ONLY" and physical.get("mutation_authorized") is False and
                         physical.get("account") == period["account"] and physical.get("execution_service_epoch") == identity[0] and
                         physical.get("connection_epoch") == epoch and positive_int(physical.get("position_generation")) and
                         positive_int(physical.get("terminal_generation")) and
                         physical.get("active_orders_generation") == orders["active_orders_generation"])
        quantity_rows, scope = physical.get("positions"), physical.get("scope")
    if not authoritative or not isinstance(quantity_rows, list):
        raise EvidenceError(positions.get("reason_code") or "PNL_POSITION_UNAVAILABLE")
    found = {}
    for row in quantity_rows:
        name = row.get("instrument")
        if name in found or name not in INSTRUMENTS:
            raise EvidenceError("PNL_POSITION_SCOPE_OR_DUPLICATE_INVALID")
        found[name] = number(row.get("quantity"))
    if scope == "PHYSICAL_CRYPTO_ONLY" and instrument not in found:
        raise EvidenceError("PNL_PHYSICAL_EMPTY_NOT_FLAT_PROOF")
    result = {"authoritative": True, "quantity": amount(found.get(instrument, number(0))),
              "sampled_at_ms": now_ms(), "connection_epoch": epoch, "position_scope": scope,
              "service_identity": identity, "quote": quote, "quote_error": quote_error,
              "active_order_ids": orders.get("active_order_ids"),
              "unsettled_native_command_ids": sorted({r.get("command_id") for r in orders.get("recent_orders", [])
                  if r.get("economic_fill") is True and r.get("economic_evidence_complete") is not True and r.get("command_id")})}
    return result


class IncrementalOMSParser:
    """In-process watch cache bound to the exact original OMS byte prefix.

    Full source bytes are still read and hash-checked on every cycle. Only JSON
    decoding is avoided for unchanged history. Never trust size/mtime alone,
    infer missing fees, overwrite the journal or persist a cached identity.
    """
    def __init__(self):
        self._size = 0
        self._sha = None
        self._events = []
        self.hits = 0
        self.appends = 0
        self.rebuilds = 0

    def parse(self, raw: bytes) -> list[dict]:
        if type(raw) is not bytes or len(raw) > MAX_JOURNAL_BYTES:
            raise EvidenceError("OMS_INPUT_TYPE_OR_SIZE")
        if raw and not raw.endswith(b"\n"):
            raise EvidenceError("OMS_INCOMPLETE_TAIL")
        sha = hashlib.sha256(raw).digest()
        if self._sha == sha and self._size == len(raw):
            self.hits += 1
            return self._events
        if (self._sha is not None and len(raw) > self._size
                and hashlib.sha256(raw[:self._size]).digest() == self._sha):
            lines = raw[self._size:].splitlines()
            if len(lines) + len(self._events) > MAX_JOURNAL_RECORDS:
                raise EvidenceError("OMS_RECORD_BOUND_EXCEEDED")
            parsed = [strict_json(line.decode()) for line in lines]
            events = self._events + parsed
            appended = True
        else:
            lines = raw.splitlines()
            if len(lines) > MAX_JOURNAL_RECORDS:
                raise EvidenceError("OMS_RECORD_BOUND_EXCEEDED")
            events = [strict_json(line.decode()) for line in lines]
            appended = False
        # Never poison a valid cache on malformed, truncated or partial bytes.
        self._events, self._sha, self._size = events, sha, len(raw)
        if appended:
            self.appends += 1
        else:
            self.rebuilds += 1
        return events


def collect(reader: Callable, period_path: Path, state_root: Path,
            journal_parser: IncrementalOMSParser | None = None) -> dict:
    raw_period, period_fingerprint = read_private(period_path)
    period = strict_json(raw_period.decode())
    validate_period(period)
    journal = state_root / "execution/oms-journal.jsonl"
    raw_journal, journal_fingerprint = read_private(journal, MAX_JOURNAL_BYTES)
    if journal_parser is None:
        if raw_journal and not raw_journal.endswith(b"\n"):
            raise EvidenceError("OMS_INCOMPLETE_TAIL")
        lines = raw_journal.splitlines()
        if len(lines) > MAX_JOURNAL_RECORDS:
            raise EvidenceError("OMS_RECORD_BOUND_EXCEEDED")
        events = [strict_json(line.decode()) for line in lines]
    else:
        events = journal_parser.parse(raw_journal)
    states, observations = {}, {}
    for instrument in INSTRUMENTS:
        path = state_root / ("crypto-driver" if instrument == "BTC.USD" else "paper-driver") / "state.json"
        state = load_private(path)
        if (state.get("instrument", "EUR.USD") != instrument or
                state.get("day_one_started_at_ms") != period["started_at_ms"] or
                type(state.get("day_one_started_at_ms")) is not int):
            raise EvidenceError("DRIVER_ACCOUNTING_PERIOD_CHANGED")
        states[instrument] = state
        try:
            observations[instrument] = observe(reader, period, instrument)
        except EvidenceError as error:
            observations[instrument] = {"authoritative": False, "reason_code": str(error)}
    identities = {tuple(v["service_identity"]) for v in observations.values() if v.get("authoritative")}
    if len(identities) > 1:
        raise EvidenceError("CROSS_INSTRUMENT_SERVICE_EPOCH_CHANGED")
    if journal_fingerprint != fingerprint(journal) or period_fingerprint != fingerprint(period_path):
        raise EvidenceError("PNL_COLLECTION_LEDGER_OR_PERIOD_CHANGED")
    for instrument in INSTRUMENTS:
        path = state_root / ("crypto-driver" if instrument == "BTC.USD" else "paper-driver") / "state.json"
        if load_private(path).get("day_one_started_at_ms") != period["started_at_ms"]:
            raise EvidenceError("DRIVER_ACCOUNTING_PERIOD_CHANGED")
    report = build_report(events, period, observations, now_ms(), states)
    report.update(period_sha256=hashlib.sha256(raw_period).hexdigest(), journal_sha256=hashlib.sha256(raw_journal).hexdigest(),
                  journal_records=len(events), journal_fingerprint=list(journal_fingerprint), producer_source_sha=source_revision(),
                  observation_details=observations)
    return report


def source_revision() -> str:
    path = Path(__file__).with_name("PNL_SOURCE_SHA")
    if path.exists():
        return path.read_text().strip()
    return "development-unversioned"


def unavailable(reason: str, period: dict | None = None) -> dict:
    return {"schema": "heptatrader.pnl.v1", "complete": False, "mutation_authorized": False,
            "observed_at_ms": now_ms(), "valid_until_ms": now_ms(), "period_id": (period or {}).get("period_id"),
            "scope": "STRATEGY_PERIOD_NOT_ACCOUNT_NAV", "components": [], "totals": {},
            "reason_codes": [reason], "display_rule": "CURRENT_PNL_IS_TOTAL_NEVER_REALIZED_ONLY"}


def cached(path: Path, period_path: Path, state_root: Path) -> dict:
    report = load_private(path)
    period_raw, _ = read_private(period_path)
    period = strict_json(period_raw.decode())
    if (report.get("period_sha256") != hashlib.sha256(period_raw).hexdigest() or
            report.get("period_id") != period.get("period_id")):
        return unavailable("CACHED_ACCOUNTING_PERIOD_CHANGED", period)
    for name in ("crypto-driver", "paper-driver"):
        if load_private(state_root / name / "state.json").get("day_one_started_at_ms") != period["started_at_ms"]:
            return unavailable("DRIVER_ACCOUNTING_PERIOD_CHANGED", period)
    if (not positive_int(report.get("observed_at_ms")) or not positive_int(report.get("valid_until_ms")) or
            not report["observed_at_ms"] <= now_ms() < report["valid_until_ms"]):
        return unavailable("CACHED_PNL_STALE", period)
    validate_period(period)
    if (report.get("schema") != "heptatrader.pnl.v1" or
            report.get("scope") != "STRATEGY_PERIOD_NOT_ACCOUNT_NAV" or
            report.get("mutation_authorized") is not False or
            report.get("display_rule") != "CURRENT_PNL_IS_TOTAL_NEVER_REALIZED_ONLY"):
        return unavailable("CACHED_PNL_SCHEMA_INVALID", period)
    if report.get("journal_fingerprint") != list(fingerprint(state_root / "execution/oms-journal.jsonl")):
        return unavailable("CACHED_PNL_LEDGER_CHANGED", period)
    return report


def initialize_period(reset: Path, path: Path, reader: Callable, state_root: Path, client_id: int) -> dict:
    broker = load_private(reset / "opening-broker.json")
    quote = load_private(reset / "opening-btc-quote.json")
    prepared = load_private(reset / "PREPARED.json")
    if broker.get("paper") is not True or broker.get("open_orders") != [] or prepared.get("broker_send_count") != 0:
        raise EvidenceError("RESET_OPENING_RECEIPT_INVALID")
    opening_time = datetime.fromisoformat(broker["time"])
    if opening_time.tzinfo is None:
        raise EvidenceError("RESET_OPENING_TIME_WITHOUT_TIMEZONE")
    start = int(opening_time.timestamp() * 1000)
    period = {"schema": "heptatrader.pnl-period.v1", "kind": "USER_RESET", "period_id": "reset-" + str(start),
              "started_at_ms": start, "currency": "USD", "execution_domain": "PAPER", "instruments": {},
              "opening_cash": broker.get("cash"), "opening_cash_is_not_pnl_or_nav": True,
              "source_evidence": {name: hashlib.sha256(read_private(reset / name)[0]).hexdigest()
                                  for name in ("opening-broker.json", "opening-btc-quote.json", "PREPARED.json")}}
    for instrument in INSTRUMENTS:
        orders = reader(instrument, "orders.list")
        scope = orders.get("owner_scope") or {}
        if orders.get("authoritative") is not True or scope.get("execution_domain") != "PAPER":
            raise EvidenceError("RESET_OWNER_UNAVAILABLE")
        account = scope.get("account")
        if "account" in period and period["account"] != account:
            raise EvidenceError("RESET_ACCOUNT_CONFLICT")
        period["account"] = account
        state = load_private(state_root / ("crypto-driver" if instrument == "BTC.USD" else "paper-driver") / "state.json")
        if state.get("day_one_started_at_ms") != start:
            raise EvidenceError("RESET_PERIOD_CONFLICT")
        opening_quantity = number(state.get("daily_opening_position"))
        entry = {"agent_id": scope.get("agent_id"), "session_id": scope.get("session_id"),
                 "broker_client_id": client_id, "opening_quantity": amount(opening_quantity),
                 "opening_mark": None, "opening_mark_source": None}
        if instrument == "BTC.USD":
            positions = [v for v in broker.get("positions", {}).values() if v.get("symbol") == "BTC" and v.get("type") == "CRYPTO"]
            if len(positions) != 1 or number(positions[0]["quantity"]) != opening_quantity:
                raise EvidenceError("RESET_OPENING_POSITION_CONFLICT")
            bid, ask = number(quote.get("bid")), number(quote.get("ask"))
            if (quote.get("source") != "IB" or quote.get("authoritative") is not True or quote.get("stale") is not False or
                    quote.get("instrument") != instrument or bid <= 0 or ask < bid):
                raise EvidenceError("RESET_MARK_INVALID")
            entry.update(opening_mark=amount((bid + ask) / 2), opening_mark_source="IB_MID_AT_OPERATOR_RESET",
                         opening_mark_observed_at_ms=quote.get("observed_at_ms"),
                         opening_mark_valid_until_ms=quote.get("stale_after_ms"),
                         historical_purchase_fill_price=state.get("opening_purchase_fill_price"),
                         historical_broker_average_cost=positions[0].get("avg_cost"), historical_costs_excluded=True)
        elif opening_quantity != 0 or prepared.get("fx_position_delta") != 0:
            raise EvidenceError("RESET_FX_OPENING_NONZERO_REQUIRES_MARK")
        period["instruments"][instrument] = entry
    # Bind the financial reset receipt's baseline account, without modifying it.
    baseline_lines = prepared.get("baseline", "").splitlines()
    if not baseline_lines or baseline_lines[0] != "HFX1" or baseline_lines[1].split("|", 1)[0] != period["account"]:
        raise EvidenceError("RESET_ACCOUNT_RECEIPT_CONFLICT")
    validate_period(period)
    write_snapshot(path, period, exclusive=True)
    return period


def attach_account(report: dict, period_path: Path, state_root: Path, config: Path) -> dict:
    from hepta_account_pnl import account_report, reconcile_nav_return, validate_nav, validate_nav_evidence, normalize_nav, digest
    period = load_private(period_path)
    validate_period(period)
    root = state_root / "pnl/account"
    issues = []
    def optional(path):
        if not path.exists(): return None
        try:
            return load_private(path)
        except (ValueError, OSError, KeyError, TypeError):
            issues.append("ACCOUNT_OPTIONAL_INPUT_INVALID:" + path.name)
            return None
    nav = optional(root / "nav.json")
    public_nav = nav
    if nav and nav.get("schema") == "heptatrader.account-nav.v2":
        nav = optional(root / "arithmetic-latest.json")
    raw_nav = optional(root / "raw-latest.json")
    if nav:
        try:
            if raw_nav is None: raise EvidenceError("ACCOUNT_NAV_RAW_SOURCE_MISSING")
            validate_nav_evidence(nav, raw_nav, period, now_ms())
            if (public_nav and public_nav.get("schema") == "heptatrader.account-nav.v2" and
                    public_nav.get("raw_sha256") != nav.get("raw_sha256")):
                raise EvidenceError("ACCOUNT_NAV_PUBLIC_ARITHMETIC_SOURCE_MISMATCH")
        except (ValueError, KeyError, TypeError) as error:
            issues.append(str(error) if isinstance(error, EvidenceError) else "ACCOUNT_NAV_SOURCE_INVALID")
            nav = None
    if nav and (nav.get("journal_fingerprint") != list(fingerprint(state_root / "execution/oms-journal.jsonl")) or
                nav.get("period_sha256") != hashlib.sha256(read_private(period_path)[0]).hexdigest()):
        nav = None
        issues.append("ACCOUNT_NAV_LEDGER_OR_PERIOD_CHANGED")
    first = optional(root / ("first-" + hashlib.sha256(period["period_id"].encode()).hexdigest()[:16] + ".json"))
    activity = optional(root / "activity.json")
    if activity:
        try:
            from hepta_account_activity import verify_activity_sources
            activity = verify_activity_sources(activity, root / "statement-sources", period["account"], now_ms())
        except (ValueError, OSError, KeyError, TypeError) as error:
            issues.append(str(error) if isinstance(error, EvidenceError) else "ACCOUNT_ACTIVITY_SOURCE_INVALID")
            activity = None
    if first:
        try:
            first_raw = optional(root / ("first-" + hashlib.sha256(period["period_id"].encode()).hexdigest()[:16] + ".raw.json"))
            if first_raw is None or normalize_nav(first_raw, period) != first or digest(first_raw) != first.get("raw_sha256"):
                raise EvidenceError("FIRST_NAV_RAW_SOURCE_MISMATCH")
        except (ValueError, KeyError, TypeError) as error:
            issues.append(str(error) if isinstance(error, EvidenceError) else "FIRST_NAV_SOURCE_INVALID")
            first = None
    model = optional(config / "exit-cost-model.json")
    result = account_report(period, nav, activity, model, first, now_ms())
    result["reason_codes"].extend(issues)
    from hepta_nav_assurance import assess_nav_assurance, enforce_strict_account_report
    assurance = assess_nav_assurance(nav, raw_nav, period, now_ms())
    result = enforce_strict_account_report(result, assurance, nav)

    inputs = optional(root / "return-inputs.json")
    opening_proven = False
    flow_coverage_proven = False
    if inputs and inputs.get("opening_nav_raw"):
        try:
            original_opening = normalize_nav(inputs["opening_nav_raw"], period)
            validate_nav(original_opening, period, now_ms(), historical=True)
            boundary = period["started_at_ms"]
            if not original_opening["observed_at_ms"] <= boundary <= original_opening["valid_until_ms"]:
                raise EvidenceError("RESET_OPENING_NAV_NOT_OBSERVED_AT_BOUNDARY")
            opening_proven = True
        except (ValueError, KeyError, TypeError) as error:
            result["reason_codes"].append(str(error) if isinstance(error, EvidenceError) else "OPENING_NAV_SOURCE_INVALID")
    if inputs and nav and result["nav_complete"]:
        try:
            # Provider evidence must be retained, not just a claimed digest.
            for key in ("external_flows", "unbooked_liabilities"):
                item = inputs.get(key)
                if not item: continue
                sha = item.get("source_sha256", "")
                if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                    raise EvidenceError("ACCOUNT_RETURN_SOURCE_DIGEST_INVALID")
                candidates = (root / "statement-sources" / (sha + ".xml"), root / "supporting-sources" / sha)
                source = next((path for path in candidates if path.exists()), None)
                if source is None or hashlib.sha256(read_private(source, 16*1024*1024)[0]).hexdigest() != sha:
                    raise EvidenceError("ACCOUNT_RETURN_SOURCE_NOT_RETAINED")
                if key == "unbooked_liabilities":
                    assessment = strict_json(read_private(source)[0].decode())
                    # A random retained attachment cannot certify hand-edited tax amounts.
                    if assessment != {k: v for k, v in item.items() if k != "source_sha256"}:
                        raise EvidenceError("LIABILITY_AMOUNTS_DIFFER_FROM_RETAINED_ASSESSMENT")
            # Re-derive funding from the retained raw XML, not caller-authored
            # rows or completeness flags in return-inputs.json.
            from hepta_account_activity import parse_flex, derive_external_flows
            flow_input = inputs["external_flows"]
            flex_raw = read_private(root / "statement-sources" / (flow_input["source_sha256"] + ".xml"), 16*1024*1024)[0]
            parsed = parse_flex(flex_raw, period["account"], period["currency"], inputs["statement_timezone"])
            flows = derive_external_flows(parsed, period["started_at_ms"], nav["observed_at_ms"])
            flow_coverage_proven = True
            # The opening NAV receipt must retain the actual raw read, too.
            from hepta_account_pnl import normalize_nav
            opening_raw = inputs["opening_nav_raw"]
            original_opening = normalize_nav(opening_raw, period)
            computed = reconcile_nav_return(period, original_opening, nav, flows,
                                            inputs.get("unbooked_liabilities"), now_ms())
            result.update(computed)
            result["complete"] = computed["all_in_complete"]
            result["reason_codes"] = [r for r in result["reason_codes"] if r not in (
                "RESET_OPENING_FULL_NAV_OR_EXTERNAL_FLOW_COVERAGE_MISSING",
                "BROKER_CASH_FLOW_AND_CHARGE_STATEMENTS_MISSING", "STATEMENT_DOES_NOT_COVER_RESET_TO_LIVE_NAV")]
            if computed["all_in_complete"]:
                result["reason_codes"] = [r for r in result["reason_codes"] if r != "EXTERNAL_UNREPORTED_LIABILITIES_AND_PERSONAL_TAX_UNPROVEN"]
        except (ValueError, KeyError, OSError) as error:
            result["reason_codes"].append(str(error) if isinstance(error, EvidenceError) else "ACCOUNT_RETURN_INPUT_INVALID")
    # Presence is not proof. Expose each missing financial prerequisite, including
    # the exact unchanged reset boundary, independently of deployment readiness.
    def status(known, reason, path=None):
        return {"status": "KNOWN" if known else "UNKNOWN", "reason": None if known else reason,
                "evidence_path": str(path) if path is not None else None}
    result["evidence_status"] = {
        "current_nav": status(result["nav_complete"], "NO_FRESH_SOURCE_VERIFIED_ACCOUNT_NAV", root / "nav.json"),
        "opening_nav_at_reset": status(False,
            "BROKER_OPENING_VALUATION_BASIS_UNATTESTED", root / "return-inputs.json"),
        "opening_arithmetic_observation_at_reset": status(opening_proven,
            "EXACT_RESET_OPENING_ARITHMETIC_SOURCE_NOT_PROVEN", root / "return-inputs.json"),
        "current_arithmetic_observation": status(result["arithmetic_reconciled"],
            "NO_FRESH_SOURCE_VERIFIED_ARITHMETIC_OBSERVATION", root / "arithmetic-latest.json"),
        "external_cash_and_asset_flows": status(flow_coverage_proven or result.get("reported_account_complete") is True,
            "RESET_TO_CURRENT_EXTERNAL_FLOW_COVERAGE_NOT_PROVEN", root / "statement-sources"),
        "posted_financing_in_period": status(result.get("posted_financing") is not None,
            "DATED_INTEREST_AND_ACCRUAL_CHANGE_BREAKDOWN_MISSING", root / "activity.json"),
        "posted_taxes_in_period": status(result.get("posted_withholding_tax") is not None and result.get("posted_transaction_tax") is not None,
            "DATED_BROKER_TAX_BREAKDOWN_MISSING", root / "activity.json"),
        "other_broker_charges_in_period": status(result.get("posted_other_fees") is not None,
            "DATED_OTHER_CHARGE_COVERAGE_MISSING", root / "activity.json"),
        "personal_tax_assessment": status(result.get("all_in_complete") is True,
            "PERIOD_BOUND_SOURCE_ASSESSMENT_MISSING_OR_UNVERIFIED", root / "supporting-sources"),
        "outside_nav_liabilities": status(result.get("all_in_complete") is True,
            "UNREPORTED_COST_NONOVERLAP_AND_COVERAGE_NOT_PROVEN", root / "supporting-sources"),
        "future_exit_costs": {"status": "ESTIMATE" if (result.get("future_exit_costs") or {}).get("complete") else "UNKNOWN",
            "reason": "MODELED_NOT_INCURRED_OR_ACCOUNT_QUALIFIED", "evidence_path": str(config / "exit-cost-model.json")}}
    result["period_boundary_unchanged"] = True
    report = dict(report)
    report["account"] = result
    report["account_nav"] = result["nav"]
    report["account_complete"] = result["complete"]
    report["account_nav_reason"] = None if result["nav_complete"] else ";".join(result["reason_codes"])
    report["all_in_account_net_pnl"] = result["all_in_account_net_pnl"]
    return report


def render_account(account: dict) -> str:
    def money(value): return "UNKNOWN" if value is None else f"{number(value):+.2f}"
    lines = ["ACCOUNT | USD | " + str(account.get("period_id")),
             "Strict Broker NAV=" + money(account.get("nav")),
             "Arithmetic-only observation (NOT strict NAV)=" + money(account.get("arithmetic_nav")),
             "  Cash=" + money(account.get("cash")) + "; marked assets=" + money(account.get("marked_assets")) +
             "; accrued interest=" + money(account.get("accrued_interest")) + "; dividend accruals=" + money(account.get("dividend_accruals")),
             "Funding-adjusted account return=" + money(account.get("broker_reported_account_net_pnl")) +
             "; including documented outside-NAV liabilities=" + money(account.get("all_in_account_net_pnl"))]
    future = account.get("future_exit_costs") or {}
    lines.append("ESTIMATE ONLY: future exit commissions=" + money(future.get("estimated_future_exit_commissions")))
    for scenario in future.get("scenarios", []):
        lines.append("  " + scenario["name"] + " estimated NAV after liquidation=" + money(scenario["estimated_nav_after_liquidation"]) +
                     "; assumed total cost=" + money(scenario["total_estimated_cost"]))
    for statement in account.get("statement_periods", [])[-3:]:
        lines.append("STATEMENT " + str(statement["from_ms"]) + ".." + str(statement["to_ms"]) +
                     " broker-account net=" + money(statement.get("broker_reported_account_net_pnl")))
    lines.append("Posted fees/interest/taxes are already reflected in NAV; never subtract them twice.")
    lines.append("Future commissions/spread/slippage are model scenarios, NOT incurred costs or executable quotes.")
    lines.append("Personal income tax and unreported outside-account costs remain UNKNOWN without source coverage.")
    lines.append("NAV observed_at_ms=" + str(account.get("nav_observed_at_ms")) + "; valid_until_ms=" + str(account.get("nav_valid_until_ms")))
    lines.extend("  " + r for r in account.get("reason_codes", []))
    if account.get("evidence_status"):
        lines.append("Evidence (same reset boundary; not silently reinitialized):")
        for name, evidence in account["evidence_status"].items():
            lines.append("  " + name + "=" + evidence["status"] + ("; " + evidence["reason"] if evidence.get("reason") else ""))
    return "\n".join(lines)


def render(report: dict) -> str:
    def money(value):
        return "UNKNOWN" if value is None else f"{number(value):+.2f}"
    title = ("HeptaTrader PnL | USD | " + str(report.get("period_id")) +
             " | strategy period, NOT account NAV")
    lines = [title, "Instrument      Realized gross   Unrealized gross   Gross total   Fees paid   Net total (execution fees)"]
    for row in report.get("components", []):
        lines.append(f"{row['instrument']:<15} {money(row.get('realized_gross_pnl')):>14} {money(row.get('unrealized_gross_pnl')):>18} "
                     f"{money(row.get('gross_total_pnl')):>13} {money(row.get('fees_paid')):>11} {money(row.get('net_total_pnl')):>17}")
        if row.get("reason_codes"):
            lines.append("  " + "; ".join(row["reason_codes"]))
    totals = report.get("totals", {})
    lines.append("TOTAL gross=" + money(totals.get("gross_total_pnl")) +
                 "; after execution commissions=" + money(totals.get("net_total_pnl")))
    lines.append("Current total is unavailable if any open position is unpriced/unproven. Unknown is never zero.")
    lines.append("Financing, tax, external cash flows, prior-period costs and future exit costs are NOT included.")
    lines.append("observed_at_ms=" + str(report.get("observed_at_ms")) + "; valid_until_ms=" + str(report.get("valid_until_ms")))
    if report.get("reason_codes"):
        lines.append("; ".join(report["reason_codes"]))
    if isinstance(report.get("account"), dict):
        lines.append("\n" + render_account(report["account"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--watch", action="store_true")
    modes.add_argument("--cached", action="store_true")
    modes.add_argument("--init-from-reset", type=Path)
    modes.add_argument("--import-flex", type=Path)
    parser.add_argument("--statement-timezone")
    parser.add_argument("--account", action="store_true", help="show complete account scope, not strategy total")
    parser.add_argument("--broker-client-id", type=int)
    parser.add_argument("--period", type=Path, default=home / ".config/heptatrader/pnl-period.json")
    parser.add_argument("--state-root", type=Path, default=home / ".local/state/heptatrader")
    parser.add_argument("--prefix", type=Path, default=home / ".local/share/heptatrader/current")
    parser.add_argument("--config", type=Path, default=home / ".config/heptatrader")
    parser.add_argument("--socket", type=Path, default=Path(f"/run/user/{os.geteuid()}/heptatrader/tools.sock"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval-sec", type=float, default=5.0)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if not 1 <= args.interval_sec <= 3600:
        parser.error("interval must be between 1 and 3600 seconds")
    if os.geteuid() == 0:
        parser.error("run as the unprivileged PAPER account owner")
    output = args.output or args.state_root / "pnl/snapshot.json"
    reader = NativeReader(args.prefix, args.config, args.socket)
    os.umask(0o077)
    if args.import_flex:
        if not args.statement_timezone:
            parser.error("--import-flex requires explicit --statement-timezone")
        from hepta_account_activity import import_flex
        try:
            result = import_flex(args.import_flex, args.period, args.state_root / "pnl/account", args.statement_timezone)
        except (OSError, ValueError, KeyError) as error:
            parser.exit(2, str(error) + "\n")
        print(json.dumps({"imported_statement_periods": len(result["statements"]),
                          "account_return_periods_complete": sum(s["complete"] for s in result["statements"]),
                          "trading_state_changed": False, "current_period_coverage_inferred": False}))
        return 0
    if args.init_from_reset:
        if not positive_int(args.broker_client_id):
            parser.error("--init-from-reset requires the existing nonzero --broker-client-id")
        try:
            period = initialize_period(args.init_from_reset, args.period, reader, args.state_root, args.broker_client_id)
        except (OSError, ValueError, KeyError, IndexError) as error:
            parser.exit(2, str(error) + "\n")
        print(json.dumps({"created_period_id": period["period_id"], "path": str(args.period), "trading_state_changed": False}))
        return 0
    stop = False
    journal_parser = IncrementalOMSParser() if args.watch else None
    def stopping(_signum, _frame):
        nonlocal stop
        stop = True
    if args.watch:
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(output.parent / "producer.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
    while not stop:
        start = time.monotonic()
        report = None
        for _ in range(3 if not args.cached else 1):
            try:
                report = cached(output, args.period, args.state_root) if args.cached else collect(reader, args.period, args.state_root, journal_parser)
                break
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                report = unavailable(str(error) if isinstance(error, EvidenceError) else type(error).__name__)
                if not isinstance(error, EvidenceError) or "CHANGED" not in str(error):
                    break
        try:
            report = attach_account(report, args.period, args.state_root, args.config)
        except (OSError, ValueError, KeyError, TypeError) as error:
            report["account"] = {"nav": None, "nav_complete": False, "complete": False,
                "mutation_authorized": False, "reason_codes": [str(error) if isinstance(error, EvidenceError) else "ACCOUNT_REPORT_UNAVAILABLE"]}
            report["account_nav"] = None
            report["account_complete"] = False
            report["all_in_account_net_pnl"] = None
        if args.watch or (args.output and not args.cached):
            write_snapshot(output, report)
        if not args.watch:
            view = report.get("account", {}) if args.account else report
            print(json.dumps(view, ensure_ascii=False, indent=2, allow_nan=False) if args.format == "json" else (render_account(view) if args.account else render(report)))
            return 0 if (view.get("complete") if args.account else report.get("complete")) else 2
        deadline = start + args.interval_sec
        while not stop and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
