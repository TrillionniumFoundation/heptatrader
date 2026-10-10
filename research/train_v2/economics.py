"""Read-only hypothetical cost scenarios; never an executed-PnL claim."""
from decimal import Decimal, InvalidOperation
from .contracts import ContractError, digest, is_digest


def bounded(value, *, positive=False):
    if isinstance(value, bool):
        raise ContractError("COST_NONFINITE")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractError("COST_NONFINITE") from exc
    if not number.is_finite() or abs(number) > 1000000 or (positive and number <= 0):
        raise ContractError("COST_NONFINITE_OR_NEGATIVE")
    return number


def verify_cost_receipt(cost, period_sha256):
    required = {
        "schema": "heptatrader.ib-cost-evidence.offline.v1",
        "instrument": "EUR.USD",
        "period_source_sha256": period_sha256,
        "exact_paper_account_scoped": True,
        "source": "ORIGINAL_CHAIN_VERIFIED_PAPER_LIVE_QUOTES_AND_OMS_COMMISSION_CALLBACKS",
        "actual_slippage_verified": False,
        "actual_fill_timestamp_attested": False,
        "historical_bar_bid_ask_used_as_executable_spread": False,
        "commission_callbacks_are_reported_not_broker_flex_statements": True,
        "trade_authority": False,
        "orders_sent": 0,
        "broker_requests_sent": 0,
        "fx_fee_missing_or_unproved_executions": 0,
    }
    if (not isinstance(cost, dict) or not is_digest(period_sha256)
            or any(cost.get(key) != value for key, value in required.items())
            or not is_digest(cost.get("source_prefix_digest"))
            or not is_digest(cost.get("oms_sha256"))
            or type(cost.get("quote_count")) is not int
            or cost["quote_count"] <= 0
            or type(cost.get("source_prefix_count")) is not int
            or cost["source_prefix_count"] <= 0
            or type(cost.get("fx_fee_complete_unique_executions")) is not int
            or cost["fx_fee_complete_unique_executions"] < 50):
        raise ContractError("COST_EVIDENCE_NOT_FULLY_BOUND")
    try:
        spread = [bounded(cost["quote_spread_full_bps"][n], positive=True)
                  for n in ("p50", "p90", "p99")]
        commission = [bounded(cost["fx_commission_one_way_bps"][n], positive=True)
                      for n in ("p50", "p90", "p99")]
    except (KeyError, TypeError) as exc:
        raise ContractError("COST_QUANTILE_MISSING") from exc
    if spread != sorted(spread) or commission != sorted(commission):
        raise ContractError("COST_QUANTILE_ORDER")
    return spread, commission


def modeled_net_edge(gross_bps, cost, period_sha256):
    """Fixed hypothetical forecast under three cost environments."""
    spreads, commissions = verify_cost_receipt(cost, period_sha256)
    gross = bounded(gross_bps)
    scenarios = []
    for slip, spread in ((Decimal("0.5"), spreads[1]),
                         (Decimal("1"), spreads[1]),
                         (Decimal("2"), spreads[2])):
        total = spread + commissions[1] * 2 + slip * 2
        scenarios.append({
            "assumed_slippage_bps_per_side": str(slip),
            "observed_spread_full_bps": str(spread),
            "reported_commission_roundtrip_bps": str(commissions[1] * 2),
            "total_hypothetical_cost_bps": str(total),
            "hypothetical_net_edge_bps": str(gross - total),
            "beats_no_trade_hypothetically": gross > total,
        })
    pass_all = all(s["beats_no_trade_hypothetically"] for s in scenarios)
    report = {
        "schema": "heptatrader.train-v2-economic-cost-frontier.v1",
        "period_sha256": period_sha256,
        "cost_receipt_id": digest(cost),
        "gross_expected_edge_bps": str(gross),
        "scenarios": scenarios,
        "assessment": ("UNQUALIFIED_POSITIVE_MODELED_EDGE" if pass_all
                       else "RESEARCH_ABSTAIN_COST_GATE"),
        "no_trade_reference_net_bps": "0",
        "actual_executed_profit_attested": False,
        "actual_slippage_attested": False,
        "financing_and_cashflow_complete": False,
        "automatic_selection": False,
        "automatic_training": False,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return {**report, "receipt_id": digest(report)}
