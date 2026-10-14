"""Broker-free slow-forecast / fresh-quote boundary for a NEW research policy.

Never calls a broker, grants execution authority, changes a quote expiry, or
rewrites a forecast. Callers obtain timestamps from their original source and
local journal; importing this module does not install a worker or select a model.
"""
from __future__ import annotations
from dataclasses import dataclass
import math


class BoundaryRejected(ValueError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise BoundaryRejected(reason)


def timestamp(value: int) -> bool:
    return type(value) is int and value >= 0


def positive(value: float) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


@dataclass(frozen=True)
class Quote:
    instrument: str
    epoch: str
    receipt_sha256: str
    observed_at_ms: int
    received_at_ms: int
    expires_at_ms: int
    bid: float
    ask: float

    def __post_init__(self) -> None:
        require(bool(self.instrument) and bool(self.epoch) and digest(self.receipt_sha256), 'QUOTE_IDENTITY')
        require(all(timestamp(x) for x in (self.observed_at_ms, self.received_at_ms, self.expires_at_ms)), 'QUOTE_TIME')
        require(self.observed_at_ms <= self.received_at_ms < self.expires_at_ms, 'QUOTE_TIME_ORDER')
        require(positive(self.bid) and positive(self.ask) and self.bid <= self.ask, 'QUOTE_PRICE')


@dataclass(frozen=True)
class Policy:
    policy_id: str
    model_sha256: str
    horizon_ms: int
    max_input_age_ms: int
    max_repricing_bps: float
    minimum_edge_bps: float

    def __post_init__(self) -> None:
        require(bool(self.policy_id) and digest(self.model_sha256), 'POLICY_IDENTITY')
        require(type(self.horizon_ms) is int and self.horizon_ms > 0 and
                type(self.max_input_age_ms) is int and self.max_input_age_ms > 0, 'POLICY_AGE')
        require(all(type(x) in (float, int) and math.isfinite(x) and x >= 0
                    for x in (self.max_repricing_bps, self.minimum_edge_bps)), 'POLICY_PRICE_BOUNDS')


@dataclass(frozen=True)
class Forecast:
    policy_id: str
    model_sha256: str
    input_quote: Quote
    started_at_ms: int
    produced_at_ms: int
    committed_at_ms: int
    predicted_mid: float

    def __post_init__(self) -> None:
        require(bool(self.policy_id) and digest(self.model_sha256), 'FORECAST_IDENTITY')
        require(all(timestamp(x) for x in (self.started_at_ms,self.produced_at_ms,self.committed_at_ms)), 'FORECAST_TIME')
        require(self.input_quote.received_at_ms <= self.started_at_ms < self.input_quote.expires_at_ms,
                'INPUT_NOT_FRESH_WHEN_MODEL_STARTED')
        require(self.started_at_ms <= self.produced_at_ms <= self.committed_at_ms, 'FORECAST_CAUSAL_ORDER')
        require(positive(self.predicted_mid), 'FORECAST_PRICE')


@dataclass(frozen=True)
class Decision:
    action: str
    edge_bps: float
    forecast_input_sha256: str
    execution_quote_sha256: str
    decided_at_ms: int
    expires_at_ms: int
    authorization_effect: str = 'NONE'


def decide(forecast: Forecast, quote: Quote, policy: Policy, now_ms: int) -> Decision:
    """Reprice a previously committed forecast on a genuinely NEW source quote.

    Separate forecast age from execution-quote expiry. An expired old input is
    never relabeled as new evidence; the old quote is only a historical feature.
    minimum_edge_bps is a declared research threshold, not observed net profit.
    """
    require(timestamp(now_ms), 'DECISION_TIME')
    require((forecast.policy_id,forecast.model_sha256) == (policy.policy_id,policy.model_sha256), 'POLICY_MODEL_MISMATCH')
    original=forecast.input_quote
    require((original.instrument,original.epoch) == (quote.instrument,quote.epoch), 'QUOTE_SCOPE_CHANGED')
    require(original.receipt_sha256 != quote.receipt_sha256, 'ORIGINAL_QUOTE_REUSED')
    require(forecast.committed_at_ms < quote.observed_at_ms <= quote.received_at_ms <= now_ms,
            'FORECAST_NOT_COMMITTED_BEFORE_NEW_QUOTE')
    deadline=min(quote.expires_at_ms, original.observed_at_ms+policy.horizon_ms,
                 original.observed_at_ms+policy.max_input_age_ms)
    require(now_ms < deadline, 'EXPIRED_QUOTE_OR_FORECAST')
    original_mid=(original.bid+original.ask)/2
    current_mid=(quote.bid+quote.ask)/2
    require(math.isfinite(original_mid) and math.isfinite(current_mid), 'PRICE_OVERFLOW')
    repricing=abs(current_mid/original_mid-1)*10000
    require(math.isfinite(repricing) and repricing <= policy.max_repricing_bps, 'REPRICING_LIMIT')
    buy_edge=(forecast.predicted_mid/quote.ask-1)*10000
    sell_edge=(1-forecast.predicted_mid/quote.bid)*10000
    require(math.isfinite(buy_edge) and math.isfinite(sell_edge), 'EDGE_OVERFLOW')
    action='HOLD'; edge=max(buy_edge,sell_edge)
    if buy_edge > policy.minimum_edge_bps:
        action='BUY'
    elif sell_edge > policy.minimum_edge_bps:
        action='SELL'
    return Decision(action,edge,original.receipt_sha256,quote.receipt_sha256,now_ms,deadline)


def require_durable_before_deadline(decision: Decision, persisted_at_ms: int) -> None:
    """Check the clock AFTER the caller's real journal fsync, never a stale clock.

    A late durable record is retained as expired, not sent or rebound to another
    quote. This function itself neither writes a journal nor submits an order.
    """
    require(timestamp(persisted_at_ms) and
            decision.decided_at_ms <= persisted_at_ms < decision.expires_at_ms,
            'DURABLE_DECISION_EXPIRED')
