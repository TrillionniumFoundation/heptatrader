#!/usr/bin/env python3
"""Pure, read-only USD strategy PnL since an explicit accounting-period boundary.

Amounts are decimal strings, never account NAV. A local acceptance is not a fill.
Order ownership, cumulative/individual executions, fees, positions and marks each
have independent completeness. Unknown values are null, not synthetic zeroes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any

ZERO = Decimal(0)
INSTRUMENTS = {"EUR.USD": Decimal("0.000001"), "BTC.USD": Decimal("0.0000000001")}
CORRELATION = re.compile(r"hepta-v1-sha256:[0-9a-f]{64}\Z")
TERMINALS = {"Filled", "Cancelled", "ApiCancelled", "Rejected", "Inactive"}


class EvidenceError(ValueError):
    pass


def number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise EvidenceError("INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise EvidenceError("INVALID_NUMBER") from error
    if not result.is_finite() or abs(result) >= Decimal("1e100"):
        raise EvidenceError("UNSET_OR_NONFINITE_NUMBER")
    return result


def positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def amount(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        raise EvidenceError("ARITHMETIC_NONFINITE")
    return format(value, "f") if value else "0"


def side(value: Any) -> int:
    if value in ("BUY", "BOT"):
        return 1
    if value in ("SELL", "SLD"):
        return -1
    raise EvidenceError("EXECUTION_SIDE_INVALID")


def validate_period(period: dict) -> None:
    if (period.get("schema") != "heptatrader.pnl-period.v1" or
            period.get("execution_domain") != "PAPER" or period.get("currency") != "USD" or
            not isinstance(period.get("account"), str) or not period["account"].startswith("DU") or
            not isinstance(period.get("period_id"), str) or not period["period_id"] or
            not positive_int(period.get("started_at_ms")) or
            period.get("kind") != "USER_RESET" or
            set(period.get("instruments", {})) != set(INSTRUMENTS)):
        raise EvidenceError("ACCOUNTING_PERIOD_INVALID")
    for instrument, entry in period["instruments"].items():
        if (not isinstance(entry.get("agent_id"), str) or not entry["agent_id"] or
                not isinstance(entry.get("session_id"), str) or not entry["session_id"] or
                not positive_int(entry.get("broker_client_id"))):
            raise EvidenceError("ACCOUNTING_OWNER_INVALID")
        quantity = number(entry.get("opening_quantity"))
        if quantity:
            if (number(entry.get("opening_mark")) <= 0 or
                    not positive_int(entry.get("opening_mark_observed_at_ms")) or
                    not positive_int(entry.get("opening_mark_valid_until_ms")) or
                    not entry["opening_mark_observed_at_ms"] <= period["started_at_ms"] <= entry["opening_mark_valid_until_ms"] or
                    entry.get("opening_mark_source") != "IB_MID_AT_OPERATOR_RESET"):
                raise EvidenceError("OPENING_MARK_NOT_PROVEN")


@dataclass
class Fill:
    command: str
    correlation: str
    execution_id: str
    order_id: int
    sent_at_ms: int
    received_at_ms: int
    direction: int
    cumulative: Decimal
    average: Decimal
    shares: Decimal | None
    price: Decimal | None
    contract_id: int | None
    permanent_id: int | None
    epochs: set[tuple[str, int]] = field(default_factory=set)
    fee: Decimal | None = None

    def signature(self) -> tuple:
        return (self.command, self.correlation, self.order_id, self.direction,
                self.cumulative, self.average, self.shares, self.price,
                self.contract_id, self.permanent_id)


@dataclass
class Book:
    quantity: Decimal = ZERO
    cost: Decimal = ZERO
    realized: Decimal = ZERO
    open_fees: Decimal = ZERO
    paid_fees: Decimal = ZERO

    def apply(self, direction: int, quantity: Decimal, price: Decimal, fee: Decimal) -> None:
        delta = direction * quantity
        self.paid_fees += fee
        if self.quantity == 0 or self.quantity * delta > 0:
            total = abs(self.quantity) + quantity
            self.cost = (abs(self.quantity) * self.cost + quantity * price) / total
            self.quantity += delta
            self.open_fees += fee
            return
        old = self.quantity
        closed = min(abs(old), quantity)
        self.realized += (1 if old > 0 else -1) * (price - self.cost) * closed
        self.open_fees *= (abs(old) - closed) / abs(old)
        self.quantity += delta
        if self.quantity == 0:
            self.cost = self.open_fees = ZERO
        elif old * self.quantity < 0:
            self.cost = price
            self.open_fees = fee * (quantity - closed) / quantity


def _owns(event: dict, period: dict, instrument: str) -> bool:
    owner = period["instruments"][instrument]
    return (event.get("account") == period["account"] and event.get("execution_domain") == "PAPER" and
            event.get("instrument") == instrument and event.get("trace_id") == owner["session_id"] and
            event.get("source") == "agent.tool:" + owner["agent_id"])


def contract_subject(identity: str) -> tuple[str, str, str]:
    if not isinstance(identity, str) or not identity.startswith("HIBE1:"):
        raise EvidenceError("ECONOMIC_CONTRACT_INVALID")
    rest = identity[6:]
    fields = []
    for _ in range(8):
        length, separator, tail = rest.partition(":")
        if not separator or not length.isdigit() or len(length) > 4:
            raise EvidenceError("ECONOMIC_CONTRACT_INVALID")
        size = int(length)
        if size > len(tail):
            raise EvidenceError("ECONOMIC_CONTRACT_INVALID")
        fields.append(tail[:size])
        rest = tail[size:]
    if not rest.startswith(":") or number(rest[1:]) != 0 or any(fields[3:6]):
        raise EvidenceError("ECONOMIC_CONTRACT_UNSUPPORTED")
    return tuple(fields[:3])


def _binding(event: dict) -> tuple:
    return (event.get("req_id"), event.get("venue_correlation_id"), event.get("order_id"),
            side(event.get("side")), event.get("instrument"), event.get("account"), event.get("trace_id"))


def replay(events: list[dict], period: dict, instrument: str, now_ms: int) -> dict:
    """Replay the existing one-pending-command-per-instrument strategy contract.

    Original send time assigns the accounting period; historical download time
    cannot move an old execution or an unrelated cash conversion into this book.
    """
    owner = period["instruments"][instrument]
    start = period["started_at_ms"]
    sends: dict[str, dict] = {}
    old_correlations: set[str] = set()
    old_execution_ids: set[str] = set()
    intents: dict[str, dict] = {}
    reliable_rejects: dict[str, dict] = {}
    # IB completed-execution downloads can replay an execution after a new
    # accounting boundary.  Baseline the exact pre-period execution identity,
    # never merely its numeric order id or later download timestamp.
    for event in events:
        if (event.get("event") == "broker_execution" and
                event.get("account") == period["account"] and
                event.get("execution_domain") == "PAPER" and
                event.get("instrument") == instrument and
                event.get("broker_client_id") == owner["broker_client_id"] and
                event.get("source") == "ib-api-callback" and
                event.get("broker_callback_type") == "execDetails" and
                positive_int(event.get("ts_ms")) and event["ts_ms"] < start):
            execution_id = event.get("broker_execution_id")
            if isinstance(execution_id, str) and 1 <= len(execution_id) <= 128:
                old_execution_ids.add(execution_id)
    for event in events:
        if not _owns(event, period, instrument):
            continue
        kind = event.get("event")
        if kind not in ("order_intent", "flatten_intent", "place_sent", "flatten_sent", "reject"):
            continue
        if not positive_int(event.get("ts_ms")) or event["ts_ms"] > now_ms:
            raise EvidenceError("COMMAND_TIME_INVALID")
        if event["ts_ms"] < start:
            if event.get("venue_correlation_id"):
                old_correlations.add(event["venue_correlation_id"])
            continue
        command = event.get("req_id")
        if not isinstance(command, str) or not command or event.get("client_req_id") != command:
            raise EvidenceError("ORIGINAL_COMMAND_IDENTITY_INVALID")
        if kind in ("order_intent", "flatten_intent"):
            previous_intent = intents.get(command)
            if previous_intent and _binding(previous_intent) != _binding(event):
                raise EvidenceError("ORIGINAL_INTENT_BINDING_CONFLICT")
            if not previous_intent or event["ts_ms"] < previous_intent["ts_ms"]:
                intents[command] = event
            continue
        if kind == "reject":
            # ExecutionCoordinator emits IB_PLACE_REJECT only for an explicit,
            # reliable VenuePlaceResult::Rejected outcome.  Uncertain dispatches
            # use place_outcome_uncertain and must remain unresolved.
            if (event.get("risk_code") == "IB_PLACE_REJECT" and
                    event.get("status") == "rejected" and
                    type(event.get("order_id")) is int and event["order_id"] == -1 and
                    CORRELATION.fullmatch(str(event.get("venue_correlation_id", ""))) and
                    isinstance(event.get("reason"), str) and event["reason"]):
                previous_reject = reliable_rejects.get(command)
                if previous_reject and _binding(previous_reject) != _binding(event):
                    raise EvidenceError("ORIGINAL_REJECT_BINDING_CONFLICT")
                if not previous_reject or event["ts_ms"] < previous_reject["ts_ms"]:
                    reliable_rejects[command] = event
            continue
        if (not CORRELATION.fullmatch(str(event.get("venue_correlation_id", ""))) or
                type(event.get("order_id")) is not int or event["order_id"] < 0):
            raise EvidenceError("ORIGINAL_SEND_BINDING_INVALID")
        previous = sends.get(command)
        if previous and _binding(previous) != _binding(event):
            raise EvidenceError("ORIGINAL_SEND_BINDING_CONFLICT")
        if not previous or event["ts_ms"] < previous["ts_ms"]:
            sends[command] = event
    # A durable intent can close without a Broker send only through the exact
    # coordinator reliable-rejection path.  Do not confuse a send attempt,
    # uncertain outcome, missing receipt or foreign rejection with proven zero-send.
    proven_rejects: set[str] = set()
    for command, rejected in reliable_rejects.items():
        intent = intents.get(command)
        if not intent:
            continue
        if _binding(intent) != _binding(rejected):
            raise EvidenceError("ORIGINAL_REJECT_BINDING_CONFLICT")
        if command in sends:
            raise EvidenceError("ORIGINAL_SEND_AND_REJECT_CONFLICT")
        proven_rejects.add(command)
    if set(intents) - sends.keys() - proven_rejects:
        raise EvidenceError("ORIGINAL_SEND_OUTCOME_UNPROVEN")
    by_correlation: dict[str, dict] = {}
    for event in sends.values():
        correlation = event["venue_correlation_id"]
        if correlation in by_correlation and by_correlation[correlation]["req_id"] != event["req_id"]:
            raise EvidenceError("CORRELATION_COMMAND_CONFLICT")
        by_correlation[correlation] = event
    fills: dict[str, Fill] = {}
    statuses: dict[str, list[dict]] = {c: [] for c in sends}
    subjectless_statuses: list[tuple[dict, dict]] = []
    for event in events:
        if event.get("event") not in ("broker_execution", "broker_order_status"):
            continue
        correlation = event.get("venue_correlation_id")
        sent = by_correlation.get(correlation)
        if not sent:
            # Foreign/manual conversions and proven pre-period receipts are not
            # strategy fills. An unmapped current own-client execution is unsafe.
            if (event.get("event") == "broker_execution" and event.get("account") == period["account"] and
                    event.get("instrument") == instrument and event.get("broker_client_id") == owner["broker_client_id"] and
                    event.get("ts_ms", 0) >= start):
                # A live callback is never excused by an older execution id.
                # For historical downloads, accept only an execution id that was
                # itself observed before this period under the exact same account,
                # domain, instrument and Broker client.
                if event.get("broker_request_id") == -1:
                    raise EvidenceError("PRE_PERIOD_LATE_LIVE_EXECUTION")
                historical_execution = (event.get("source") == "ib-api-callback" and
                    event.get("broker_callback_type") == "execDetails" and
                    event.get("execution_domain") == "PAPER" and
                    event.get("broker_execution_id") in old_execution_ids)
                if correlation not in old_correlations and not historical_execution:
                    raise EvidenceError("UNBOUND_OWN_EXECUTION")
            continue
        # Native orderStatus carries order/quantity fields, not contract/side.
        # After reconnect an otherwise exact callback may lack enrichment. It
        # is NOT a fill and never gets a subject from a numeric order id alone.
        # Defer this narrow case until fully validated same-epoch execDetails
        # independently witnesses the original command/correlation binding.
        subjectless_status = (
            event.get("event") == "broker_order_status" and
            event.get("broker_callback_type") == "orderStatus" and
            event.get("source") == "ib-api-callback" and
            event.get("instrument") in (None, "") and event.get("side") in (None, ""))
        if (event.get("account") != period["account"] or event.get("execution_domain") != "PAPER" or
                event.get("broker_client_id") != owner["broker_client_id"] or
                event.get("order_id") != sent["order_id"] or
                (not subjectless_status and (event.get("instrument") != instrument or side(event.get("side")) != side(sent["side"]))) or
                event.get("req_id") not in (None, "", sent["req_id"]) or
                event.get("trace_id") not in (None, "", owner["session_id"])):
            raise EvidenceError("BROKER_EXECUTION_BINDING_CONFLICT")
        if (not positive_int(event.get("ts_ms")) or event["ts_ms"] > now_ms or event["ts_ms"] < sent["ts_ms"] or
                not isinstance(event.get("broker_service_epoch"), str) or not event["broker_service_epoch"] or
                not positive_int(event.get("broker_connection_epoch"))):
            raise EvidenceError("BROKER_EXECUTION_EPOCH_OR_TIME_INVALID")
        if event["event"] == "broker_order_status":
            if subjectless_status:
                subjectless_statuses.append((event, sent))
            elif event.get("broker_callback_type") == "orderStatus":
                statuses[sent["req_id"]].append(event)
            continue
        if event.get("broker_callback_type") != "execDetails":
            raise EvidenceError("EXECUTION_CALLBACK_INVALID")
        execution_id = event.get("broker_execution_id")
        if not isinstance(execution_id, str) or not 1 <= len(execution_id) <= 128:
            raise EvidenceError("EXECUTION_ID_INVALID")
        quantity, average = number(event.get("qty")), number(event.get("price"))
        if quantity <= 0 or average <= 0:
            raise EvidenceError("EXECUTION_ECONOMICS_INVALID")
        shares = price = contract = permanent = None
        if "broker_econ_version" in event:
            if (type(event.get("broker_econ_version")) is not int or event.get("broker_econ_version") != 1 or
                    type(event.get("broker_econ_valid")) is not int or event.get("broker_econ_valid") != 1 or
                    event.get("broker_econ_kind") != "EXECUTION" or type(event.get("broker_econ_price_revision_pending")) is not int or
                    event.get("broker_econ_price_revision_pending") != 0):
                raise EvidenceError("EXECUTION_ECONOMICS_INVALID_OR_REVISION_PENDING")
            expected_subject = ("BTC", "CRYPTO", "USD") if instrument == "BTC.USD" else ("EUR", "CASH", "USD")
            if contract_subject(event.get("broker_econ_contract_identity")) != expected_subject:
                raise EvidenceError("ECONOMIC_CONTRACT_SUBJECT_MISMATCH")
            shares = number(event.get("broker_econ_shares"))
            price = number(event.get("broker_econ_execution_price"))
            contract, permanent = event.get("broker_econ_contract_id"), event.get("broker_econ_permanent_order_id")
            if (shares <= 0 or price <= 0 or shares > quantity or not positive_int(contract) or
                    type(permanent) is not int or permanent < 0 or
                    quantity != number(event.get("broker_econ_cumulative_quantity")) or
                    average != number(event.get("broker_econ_cumulative_average"))):
                raise EvidenceError("EXECUTION_ECONOMICS_INCONSISTENT")
        fill = Fill(sent["req_id"], correlation, execution_id, sent["order_id"], sent["ts_ms"], event["ts_ms"],
                    side(event["side"]), quantity, average, shares, price, contract, permanent)
        fill.epochs.add((event["broker_service_epoch"], event["broker_connection_epoch"]))
        previous = fills.get(execution_id)
        if previous:
            if previous.signature() != fill.signature():
                raise EvidenceError("EXECUTION_ID_REPLAY_CONFLICT")
            previous.epochs.update(fill.epochs)
            previous.received_at_ms = min(previous.received_at_ms, fill.received_at_ms)
        else:
            fills[execution_id] = fill
    # A commission has no account: require unique execId binding and a paired
    # native stream epoch; do not borrow numeric order IDs or account='All'.
    collisions = set()
    for event in events:
        fill = fills.get(event.get("broker_execution_id"))
        if fill and event.get("event") == "broker_execution":
            if (event.get("account") != period["account"] or event.get("broker_client_id") != owner["broker_client_id"] or
                    event.get("venue_correlation_id") != fill.correlation):
                collisions.add(fill.execution_id)
    if collisions:
        raise EvidenceError("CROSS_SUBJECT_EXECUTION_ID_COLLISION")
    # Require a validated full-subject execution from the very same service
    # and Broker connection epoch. Missing or contradictory execution identity
    # still fails above; a status cannot stand in for zero-fill/economic proof.
    execution_witnesses = {}
    for fill in sorted(fills.values(), key=lambda f: f.execution_id):
        for epoch in fill.epochs:
            key = (fill.command, fill.correlation, fill.order_id, epoch)
            execution_witnesses.setdefault(key, fill.execution_id)
    recovered_status_witnesses = {}
    for event, sent in subjectless_statuses:
        epoch = (event["broker_service_epoch"], event["broker_connection_epoch"])
        key = (sent["req_id"], event["venue_correlation_id"], event["order_id"], epoch)
        witness = execution_witnesses.get(key)
        if witness is None:
            raise EvidenceError("ORDER_STATUS_SUBJECT_REQUIRES_SAME_EPOCH_EXECUTION")
        statuses[sent["req_id"]].append(event)
        recovered_status_witnesses[key] = {"command_id": sent["req_id"], "order_id": event["order_id"],
            "venue_correlation_id": event["venue_correlation_id"], "execution_id": witness,
            "service_epoch": epoch[0], "connection_epoch": epoch[1],
            "basis": "VALIDATED_SAME_EPOCH_EXEC_DETAILS_NOT_NUMERIC_ORDER_ID"}
    fees: dict[str, tuple[Decimal, str]] = {}
    fee_conflicts: set[str] = set()
    for event in events:
        eid = event.get("broker_execution_id")
        fill = fills.get(eid)
        if not fill or event.get("event") != "broker_commission":
            continue
        epoch = (event.get("broker_service_epoch"), event.get("broker_connection_epoch"))
        try:
            if (event.get("broker_callback_type") != "commissionReport" or epoch not in fill.epochs or
                    type(event.get("broker_econ_version")) is not int or event.get("broker_econ_version") != 1 or
                    type(event.get("broker_econ_valid")) is not int or event.get("broker_econ_valid") != 1 or
                    event.get("broker_econ_kind") != "COMMISSION" or
                    event.get("account") not in (None, "") or event.get("order_id") != -1 or
                    not positive_int(event.get("ts_ms")) or not start <= event["ts_ms"] <= now_ms):
                raise EvidenceError("FEE_EVIDENCE_INVALID")
            fee, currency = number(event.get("broker_econ_commission")), event.get("broker_econ_commission_currency")
            if abs(fee) > Decimal("1000000") or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise EvidenceError("FEE_EVIDENCE_INVALID")
            if eid in fees and fees[eid] != (fee, currency):
                fee_conflicts.add(eid)
            fees[eid] = (fee, currency)
        except EvidenceError:
            fee_conflicts.add(eid)
    # Native IB TWS API error 202 is the Broker's cancellation of an active
    # order. It is NOT an orderStatus and does not erase the already executed
    # quantity or synthesize zero-fill proof. Use only an exact original owner
    # + venue correlation + same service/connection epoch execDetails witness
    # to settle the cancelled UNFILLED REMAINDER of an executed command.
    # Terminal orderStatus continues to take precedence when already present.
    broker_cancel_202: dict[str, list[dict]] = {}
    for event in events:
        if event.get("event") != "broker_error" or event.get("risk_code") != "IB_ERROR_202":
            continue
        sent = by_correlation.get(event.get("venue_correlation_id"))
        if sent is None:
            continue
        if (event.get("broker_callback_type") != "error" or
                event.get("status") != "Cancelled" or
                event.get("source") != "agent:" + owner["agent_id"] or
                event.get("req_id") != sent["req_id"] or
                event.get("client_req_id") != sent["req_id"] or
                event.get("account") != period["account"] or
                event.get("execution_domain") != "PAPER" or
                event.get("instrument") != instrument or
                # TWS error() callback has no owner client-id: the existing
                # native adapter persists its deliberate (-1, 0) sentinels.
                # Do not borrow a client ID from numeric orderId or accept
                # a normal per-client error as the original Broker callback.
                event.get("broker_client_id") != -1 or
                event.get("broker_request_id") != 0 or
                type(event.get("broker_error_code")) is not int or
                event.get("broker_error_code") != 202 or
                event.get("trace_id") != owner["session_id"] or
                event.get("order_id") != sent["order_id"] or
                side(event.get("side")) != side(sent["side"])):
            raise EvidenceError("BROKER_CANCEL_202_BINDING_CONFLICT")
        epoch = (event.get("broker_service_epoch"), event.get("broker_connection_epoch"))
        if (not isinstance(epoch[0], str) or not epoch[0] or
                not positive_int(epoch[1]) or
                not positive_int(event.get("ts_ms")) or
                not sent["ts_ms"] <= event["ts_ms"] <= now_ms):
            raise EvidenceError("BROKER_CANCEL_202_TIME_OR_EPOCH_INVALID")
        broker_cancel_202.setdefault(sent["req_id"], []).append(event)

    # Independent native Broker completedOrder(Filled) callbacks deliberately
    # omit their connection-scoped numeric order ID (order_id=0). The original
    # coordinator's reconciled-terminal event binds that same venue correlation
    # back to the durable original command/order identity. Neither event by
    # itself proves economic completion: require BOTH plus exact same-epoch
    # full-subject execDetails, never derive a fill from an order ID or owner
    # assertion alone. This is read-only accounting; it issues no orders.
    completed_by_correlation: dict[str, list[dict]] = {}
    owner_terminal_by_correlation: dict[str, list[dict]] = {}
    for event in events:
        kind = event.get("event")
        if kind not in ("broker_completed_order", "order_owner_reconciled_terminal"):
            continue
        correlation = event.get("venue_correlation_id")
        sent = by_correlation.get(correlation)
        if sent is None:
            continue
        if kind == "broker_completed_order":
            if (event.get("source") != "ib-api-callback" or
                    event.get("broker_callback_type") != "completedOrder" or
                    event.get("status") != "Filled" or
                    event.get("risk_code") != "IB_FILLED_ECONOMIC_EVIDENCE_REQUIRED" or
                    event.get("broker_error_code") != 0 or
                    # Native completedOrder omits subject and carries a
                    # synthetic zero client ID and zero numeric order ID.
                    # Full account+instrument+side+client identity MUST come
                    # from same-epoch original execDetails, not this callback.
                    event.get("broker_client_id") != 0 or
                    event.get("order_id") != 0 or
                    event.get("account") != period["account"] or
                    event.get("execution_domain") != "PAPER" or
                    event.get("instrument") not in (None, "") or
                    event.get("side") not in (None, "") or
                    event.get("req_id") not in (None, "", sent["req_id"]) or
                    event.get("trace_id") not in (None, "", owner["session_id"]) or
                    not isinstance(event.get("broker_service_epoch"), str) or
                    not event.get("broker_service_epoch") or
                    not positive_int(event.get("broker_connection_epoch")) or
                    not positive_int(event.get("ts_ms")) or
                    not sent["ts_ms"] <= event["ts_ms"] <= now_ms):
                continue
            completed_by_correlation.setdefault(correlation, []).append(event)
        else:
            if (event.get("source") != "agent.tool:" + owner["agent_id"] or
                    event.get("status") != "terminal" or
                    event.get("risk_code") != "ORDER_OWNER_RECONCILED_TERMINAL" or
                    event.get("account") != period["account"] or
                    event.get("execution_domain") != "PAPER" or
                    event.get("instrument") != instrument or
                    side(event.get("side")) != side(sent["side"]) or
                    event.get("trace_id") != owner["session_id"] or
                    event.get("order_id") != sent["order_id"] or
                    # The native owner reconciliation is a NEW read-only
                    # control request, not the original placement command.
                    # Its own request identity must be internally paired.
                    not isinstance(event.get("req_id"), str) or
                    not event.get("req_id") or
                    event.get("client_req_id") != event.get("req_id") or
                    not positive_int(event.get("ts_ms")) or
                    not sent["ts_ms"] <= event["ts_ms"] <= now_ms):
                continue
            owner_terminal_by_correlation.setdefault(correlation, []).append(event)
    fee_missing = sorted(set(fills) - fees.keys())
    fee_currencies: dict[str, Decimal] = {}
    for eid, (fee, currency) in fees.items():
        if eid not in fee_conflicts:
            fee_currencies[currency] = fee_currencies.get(currency, ZERO) + fee
    fees_complete = not fee_missing and not fee_conflicts and all(c == "USD" for _, c in fees.values())
    opening = period["instruments"][instrument]
    book = Book(quantity=number(opening["opening_quantity"]),
                cost=number(opening["opening_mark"]) if number(opening["opening_quantity"]) else ZERO)
    unsettled: list[str] = []
    broker_cancel_witnesses = []
    owner_terminal_witnesses = []
    executions = []
    for sent in sorted(sends.values(), key=lambda e: (e["ts_ms"], e["req_id"])):
        points = sorted((f for f in fills.values() if f.command == sent["req_id"]), key=lambda f: f.cumulative)
        if len(points) > 128:
            raise EvidenceError("EXECUTION_CAPACITY_EXCEEDED")
        previous_quantity = previous_notional = individual_notional = ZERO
        families: set[str] = set()
        for fill in points:
            family = fill.execution_id.rsplit(".", 1)[0] if "." in fill.execution_id else fill.execution_id
            if family in families:
                raise EvidenceError("EXECUTION_CORRECTION_REQUIRES_RECONCILIATION")
            families.add(family)
            delta = fill.cumulative - previous_quantity
            notional = fill.cumulative * fill.average
            if delta <= 0 or notional <= previous_notional:
                raise EvidenceError("CUMULATIVE_EXECUTION_CONFLICT")
            if fill.shares is not None:
                if abs(fill.shares - delta) > INSTRUMENTS[instrument]:
                    raise EvidenceError("EXECUTION_SHARES_GAP")
                price = fill.price
                individual_notional += delta * price
                # IB rounds cumulative avgPrice; compare at its six-decimal
                # precision, not with an invented zero-tolerance dollar test.
                if abs(individual_notional - notional) > max(Decimal("0.00000001"), fill.cumulative * Decimal("0.000001")):
                    raise EvidenceError("EXECUTION_NOTIONAL_CONFLICT")
            else:
                price = (notional - previous_notional) / delta
                individual_notional = notional
            fee = fees.get(fill.execution_id)
            usable_fee = fee[0] if fee and fee[1] == "USD" and fill.execution_id not in fee_conflicts else ZERO
            book.apply(fill.direction, delta, price, usable_fee)
            executions.append({"command_id": fill.command, "order_id": fill.order_id, "execution_id": fill.execution_id,
                               "side": "BUY" if fill.direction == 1 else "SELL", "quantity": amount(delta),
                               "price": amount(price), "cumulative_quantity": amount(fill.cumulative),
                               "commission": amount(fee[0]) if fee else None, "commission_currency": fee[1] if fee else None})
            previous_quantity, previous_notional = fill.cumulative, notional
        terminal = [e for e in statuses[sent["req_id"]] if e.get("status") in TERMINALS]
        if not points:
            if not terminal or any(e.get("status") == "Filled" or number(e.get("qty")) != 0 or number(e.get("price")) != 0 for e in terminal):
                raise EvidenceError("SUBMITTED_COMMAND_WITHOUT_FILL_OR_ZERO_FILL_PROOF")
        elif terminal:
            if any(abs(number(e.get("qty")) - previous_quantity) > INSTRUMENTS[instrument] or
                   abs(number(e.get("price")) - points[-1].average) > Decimal("0.000001") for e in terminal):
                raise EvidenceError("TERMINAL_EXECUTION_TOTAL_CONFLICT")
        else:
            matched = [
                e for e in broker_cancel_202.get(sent["req_id"], [])
                if any((e["broker_service_epoch"],e["broker_connection_epoch"]) in f.epochs
                       for f in points)
            ]
            # A later nonterminal status could indicate reactivation; refuse
            # to treat a prior cancel callback as the final working state.
            stable = matched and not any(
                e["ts_ms"] > max(v["ts_ms"] for v in matched)
                and e.get("status") not in TERMINALS
                for e in statuses[sent["req_id"]])
            if stable:
                witness = min(matched, key=lambda e: e["ts_ms"])
                broker_cancel_witnesses.append({
                    "command_id": sent["req_id"],
                    "order_id": sent["order_id"],
                    "venue_correlation_id": sent["venue_correlation_id"],
                    "broker_service_epoch": witness["broker_service_epoch"],
                    "broker_connection_epoch": witness["broker_connection_epoch"],
                    "original_broker_error_at_ms": witness["ts_ms"],
                    "original_execution_ids": [f.execution_id for f in points],
                    "basis": "ORIGINAL_IB_API_ERROR_202_CANCELLED_UNFILLED_REMAINDER",
                    "terminal_order_status_observed": False,
                    "zero_fill_imputed": False})
            else:
                # A native owner reconciliation may be proven by the Broker's
                # later completedOrder(Filled) and full-subject execution in
                # the SAME service+connection epoch. CompletedOrder order_id=0
                # does not inherit the numeric order ID; the original owner
                # terminal binds correlation+order ID independently.
                corr = sent["venue_correlation_id"]
                proof = None
                for owner_event in owner_terminal_by_correlation.get(corr, []):
                    if any(e["ts_ms"] > owner_event["ts_ms"] and
                           e.get("status") not in TERMINALS
                           for e in statuses[sent["req_id"]]):
                        continue
                    for completed in completed_by_correlation.get(corr, []):
                        epoch = (completed["broker_service_epoch"],
                                 completed["broker_connection_epoch"])
                        if (completed["ts_ms"] > owner_event["ts_ms"] or
                                not any(epoch in f.epochs for f in points)):
                            continue
                        proof = (owner_event, completed, epoch)
                        break
                    if proof is not None:
                        break
                if proof is None:
                    unsettled.append(sent["req_id"])
                else:
                    owner_event, completed, epoch = proof
                    owner_terminal_witnesses.append({
                        "command_id": sent["req_id"],
                        "original_order_id": sent["order_id"],
                        "venue_correlation_id": corr,
                        "native_owner_terminal_at_ms": owner_event["ts_ms"],
                        "broker_completed_at_ms": completed["ts_ms"],
                        "broker_service_epoch": epoch[0],
                        "broker_connection_epoch": epoch[1],
                        "original_execution_ids": [f.execution_id for f in points],
                        "basis": "NATIVE_OWNER_RECONCILED_AND_BROKER_COMPLETED_FILLED_SAME_EPOCH_EXEC_DETAILS",
                        "completed_order_numeric_id": 0,
                        "zero_fill_imputed": False,
                        "broker_orders_sent": 0})
    return {"book": book, "fees_complete": fees_complete, "fee_missing_execution_ids": fee_missing,
            "fee_conflicting_execution_ids": sorted(fee_conflicts),
            "fees_known_by_currency": {c: amount(v) for c, v in sorted(fee_currencies.items())},
            "settlement_pending_command_ids": unsettled, "executions": executions,
            "broker_cancel_witnesses_total": len(broker_cancel_witnesses),
            "broker_cancel_witnesses": broker_cancel_witnesses[:128],
            "owner_terminal_witnesses_total": len(owner_terminal_witnesses),
            "owner_terminal_witnesses": owner_terminal_witnesses[:128],
            "command_count": len(sends), "execution_count": len(fills),
            "status_subject_witnesses_total": len(recovered_status_witnesses),
            "status_subject_witnesses": [recovered_status_witnesses[k] for k in sorted(recovered_status_witnesses)][:128]}


def instrument_report(events: list[dict], period: dict, instrument: str, observation: dict, now_ms: int,
                      driver_state: dict | None = None) -> dict:
    result = {"instrument": instrument, "currency": "USD", "ledger_complete": False, "valuation_complete": False,
              "position": None, "broker_position": observation.get("quantity"), "entry_cost_basis": None,
              "realized_gross_pnl": None, "unrealized_gross_pnl": None, "gross_total_pnl": None,
              "fees_paid": None, "fees_complete": False, "realized_net_pnl": None,
              "unrealized_net_pnl": None, "net_total_pnl": None, "all_in_account_net_pnl": None,
              "net_scope": "PERIOD_STRATEGY_AFTER_REPORTED_EXECUTION_COMMISSIONS",
              "excluded_costs": ["financing", "tax", "unreported_non_execution_charges", "future_exit_costs"],
              "cost_basis_source": "OPENING_PERIOD_MARK_THEN_WEIGHTED_BROKER_EXECUTIONS",
              "mark_price": None, "mark_source": None, "mark_observed_at_ms": None,
              "mark_valid_until_ms": None, "reason_codes": []}
    if driver_state is not None:
        result["legacy_driver"] = {"position": driver_state.get("position"),
                                   "realized_gross_pnl_only": driver_state.get("gross_pnl"),
                                   "pnl_basis": driver_state.get("pnl_basis"),
                                   "pending_command_id": (driver_state.get("pending") or {}).get("command_id")}
    try:
        with localcontext() as context:
            context.prec = 40
            ledger = replay(events, period, instrument, now_ms)
            book = ledger.pop("book")
            # Keep the view bounded; accounting always consumes the full bounded journal.
            # This is only a display tail, not a truncated accounting input.
            ledger["execution_details_total"] = len(ledger["executions"])
            ledger["execution_details_truncated"] = len(ledger["executions"]) > 128
            ledger["executions"] = ledger["executions"][-128:]
            for key in ("fee_missing_execution_ids", "fee_conflicting_execution_ids", "settlement_pending_command_ids"):
                ledger[key + "_total"] = len(ledger[key])
                ledger[key] = ledger[key][:128]
            result.update(ledger)
            result.update(ledger_complete=True, position=amount(book.quantity), entry_cost_basis=amount(book.cost) if book.quantity else None,
                          realized_gross_pnl=amount(book.realized), fees_paid=amount(book.paid_fees) if ledger["fees_complete"] else None,
                          remaining_entry_fees=amount(book.open_fees) if ledger["fees_complete"] else None)
            if driver_state is not None:
                result["driver_realized_gross_difference"] = amount(number(driver_state.get("gross_pnl")) - book.realized)
                if abs(number(result["driver_realized_gross_difference"])) > Decimal("0.00001"):
                    result["reason_codes"].append("DRIVER_REALIZED_GROSS_DIFFERS_FROM_EXECUTION_LEDGER")
            if ledger["fees_complete"]:
                result["realized_net_pnl"] = amount(book.realized - (book.paid_fees - book.open_fees))
            else:
                result["reason_codes"].append("FEES_INCOMPLETE_OR_UNCONVERTED")
            if (observation.get("authoritative") is not True or not positive_int(observation.get("sampled_at_ms")) or
                    not 0 <= now_ms - observation["sampled_at_ms"] <= 10000):
                raise EvidenceError(observation.get("reason_code") or "AUTHORITATIVE_POSITION_UNAVAILABLE")
            quantity = number(observation.get("quantity"))
            if abs(quantity - book.quantity) > INSTRUMENTS[instrument]:
                raise EvidenceError("BROKER_LEDGER_POSITION_MISMATCH")
            if driver_state is not None and abs(number(driver_state.get("position")) - quantity) > INSTRUMENTS[instrument]:
                result["reason_codes"].append("DRIVER_POSITION_LAGS_BROKER_EXECUTIONS")
            if book.quantity == 0:
                unrealized = ZERO
                result["mark_source"] = "NOT_REQUIRED_AUTHORITATIVE_FLAT"
            else:
                quote = observation.get("quote") or {}
                if (quote.get("source") != "IB" or quote.get("authoritative") is not True or
                        quote.get("instrument") != instrument or quote.get("stale") is not False or
                        not positive_int(quote.get("observed_at_ms")) or not positive_int(quote.get("stale_after_ms")) or
                        not quote["observed_at_ms"] <= now_ms < quote["stale_after_ms"] or
                        now_ms - quote["observed_at_ms"] > 10000):
                    raise EvidenceError("MARK_MISSING_STALE_OR_FUTURE")
                epoch = observation.get("connection_epoch")
                sub = quote.get("subscription_id", "").split(":")
                if not positive_int(epoch) or len(sub) != 4 or sub[:2] != ["IB", str(epoch)]:
                    raise EvidenceError("QUOTE_CONNECTION_EPOCH_MISMATCH")
                bid, ask = number(quote.get("bid")), number(quote.get("ask"))
                if bid <= 0 or ask < bid:
                    raise EvidenceError("INVALID_OR_CROSSED_MARK")
                mark = bid if book.quantity > 0 else ask
                unrealized = (mark - book.cost) * book.quantity
                result.update(mark_price=amount(mark), mark_source="IB_BID" if book.quantity > 0 else "IB_ASK",
                              mark_observed_at_ms=quote["observed_at_ms"], mark_valid_until_ms=quote["stale_after_ms"])
            total = book.realized + unrealized
            result.update(valuation_complete=True, unrealized_gross_pnl=amount(unrealized), gross_total_pnl=amount(total))
            if ledger["fees_complete"]:
                result.update(unrealized_net_pnl=amount(unrealized - book.open_fees),
                              net_total_pnl=amount(total - book.paid_fees))
    except (EvidenceError, InvalidOperation, OverflowError, TypeError, KeyError) as error:
        result["reason_codes"].append(str(error) if isinstance(error, EvidenceError) else "MALFORMED_PNL_EVIDENCE")
    return result


def build_report(events: list[dict], period: dict, observations: dict, now_ms: int,
                 driver_states: dict | None = None) -> dict:
    validate_period(period)
    if now_ms < period["started_at_ms"]:
        raise EvidenceError("PERIOD_IS_IN_FUTURE")
    rows = [instrument_report(events, period, i, observations.get(i, {}), now_ms,
                              (driver_states or {}).get(i)) for i in INSTRUMENTS]
    totals: dict[str, Any] = {"currency": "USD"}
    for name in ("realized_gross_pnl", "unrealized_gross_pnl", "gross_total_pnl", "fees_paid",
                 "realized_net_pnl", "unrealized_net_pnl", "net_total_pnl"):
        totals[name] = amount(sum((number(r[name]) for r in rows), ZERO)) if all(r[name] is not None for r in rows) else None
    totals["fees_complete"] = all(r["fees_complete"] for r in rows)
    totals["all_in_account_net_pnl"] = None
    deadlines = [r["mark_valid_until_ms"] for r in rows if r["mark_valid_until_ms"] is not None]
    return {"schema": "heptatrader.pnl.v1", "scope": "STRATEGY_PERIOD_NOT_ACCOUNT_NAV", "currency": "USD",
            "period_id": period["period_id"], "period_started_at_ms": period["started_at_ms"],
            "period_kind": period["kind"], "observed_at_ms": now_ms,
            "valid_until_ms": min([now_ms + 5000] + deadlines), "mutation_authorized": False,
            "complete": all(r["ledger_complete"] and r["valuation_complete"] for r in rows),
            "components": rows, "totals": totals,
            "account_nav": None, "account_nav_reason": "NO_COMPLETE_ACCOUNT_CASH_FLOW_AND_FX_NAV_PRODUCER",
            "display_rule": "CURRENT_PNL_IS_TOTAL_NEVER_REALIZED_ONLY"}
