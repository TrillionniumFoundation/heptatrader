#!/usr/bin/env python3
"""Account NAV, funding-adjusted return, and separately labelled liquidation scenarios.

No I/O or trading. Broker NAV already contains booked expenses and reported
accruals: NEVER subtract those expenses again. Missing source coverage is not zero.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import hashlib
import json
import re
from hepta_pnl_accounting import EvidenceError, amount, number, positive_int, validate_period

ZERO = Decimal(0)
CURRENCY = re.compile(r"[A-Z]{3}\Z")
ASSET_KEYS = ("StockMarketValue", "OptionMarketValue", "CorporateBondValue", "TBillValue",
              "TBondValue", "WarrantValue", "FundValue", "MutualFundValue", "MoneyMarketFundValue",
              "FutureOptionValue", "IssuerOptionValue", "Cryptocurrency")
NAV_KEYS = ("CashBalance", "TotalCashBalance", "AccruedCash", "NetDividend", "NetLiquidationByCurrency", "ExchangeRate")


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def near(left: Decimal, right: Decimal, tolerance: Decimal = Decimal("0.05")) -> bool:
    return abs(left - right) <= tolerance


def rounding_bound(value: str) -> Decimal:
    # Quantization bound for reported decimal precision, NOT a business tolerance.
    x = number(value)
    return Decimal(5).scaleb(x.as_tuple().exponent - 1)


def validate_preflight_scope(scope: dict) -> None:
    """A read-only observation identity, deliberately NOT a PnL period."""
    keys = {"schema", "account", "currency", "execution_domain", "observation_scope_id",
            "created_at_ms", "reset_sha256", "excluded_client_ids", "read_only"}
    if (set(scope) != keys or scope.get("schema") != "heptatrader.account-preflight-scope.v1" or
            scope.get("execution_domain") != "PAPER" or scope.get("currency") != "USD" or
            scope.get("read_only") is not True or
            not isinstance(scope.get("account"), str) or not re.fullmatch(r"DU[A-Z0-9_]{1,30}", scope["account"]) or
            not isinstance(scope.get("observation_scope_id"), str) or
            not re.fullmatch(r"obs-[a-z0-9-]{8,48}", scope["observation_scope_id"]) or
            not positive_int(scope.get("created_at_ms")) or
            not isinstance(scope.get("reset_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", scope["reset_sha256"]) or
            not isinstance(scope.get("excluded_client_ids"), list) or not 1 <= len(scope["excluded_client_ids"]) <= 16 or
            any(not positive_int(x) for x in scope["excluded_client_ids"]) or
            len(set(scope["excluded_client_ids"])) != len(scope["excluded_client_ids"])):
        raise EvidenceError("ACCOUNT_PREFLIGHT_SCOPE_INVALID")


def normalize_nav(raw: dict, period: dict) -> dict:
    """Account-period entry point. Preflight sources cannot become period evidence."""
    validate_period(period)
    if "observation_scope_id" in raw:
        raise EvidenceError("PREFLIGHT_IS_NOT_ACCOUNTING_PERIOD_EVIDENCE")
    return _normalize_account_download(raw, period, "period_id", ("heptatrader.account-download.v1", "heptatrader.account-download.v2"),
        "heptatrader.account-nav.v1", {x["broker_client_id"] for x in period["instruments"].values()})


def normalize_preflight_nav(raw: dict, scope: dict) -> dict:
    validate_preflight_scope(scope)
    if "period_id" in raw or "period_sha256" in raw:
        raise EvidenceError("ACCOUNT_PREFLIGHT_PERIOD_CONTAMINATION")
    nav = _normalize_account_download(raw, scope, "observation_scope_id",
        "heptatrader.account-preflight-download.v1", "heptatrader.account-preflight-nav.v1",
        set(scope["excluded_client_ids"]))
    nav.pop("period_sha256", None)
    nav.pop("journal_fingerprint", None)
    nav.update(scope_sha256=raw.get("scope_sha256"),
               reporting_scope="PREFLIGHT_NOT_ACCOUNTING_PERIOD", opening_nav_eligible=False)
    return nav


def validate_preflight_nav_evidence(nav: dict, raw: dict, scope: dict, at_ms: int) -> None:
    """Validate current preflight publication; never authorizes an opening or return."""
    expected = normalize_preflight_nav(raw, scope)
    if (expected != nav or not positive_int(at_ms) or
            not nav["observed_at_ms"] <= at_ms < nav["valid_until_ms"]):
        raise EvidenceError("ACCOUNT_PREFLIGHT_MISSING_STALE_OR_WRONG_SOURCE")


def _normalize_account_download(raw: dict, period: dict, identity_key: str,
                                raw_schema: str, nav_schema: str, excluded_clients: set) -> dict:
    # Paired ledger/full subscriptions keep ambiguous aggregate AccruedCash out
    # of currency ledgers. All existing component and FX comparisons are shared.
    requests = raw.get("request_ids", {})
    first, completed = raw.get("started_at_ms"), raw.get("observed_at_ms")
    if (raw.get("schema") not in ((raw_schema,) if isinstance(raw_schema, str) else raw_schema) or raw.get("read_only") is not True or
            raw.get("account") != period["account"] or raw.get(identity_key) != period[identity_key] or
            not positive_int(first) or not positive_int(completed) or not 0 <= completed - first <= 15000 or
            not isinstance(raw.get("connection_id"), str) or not raw["connection_id"] or
            not positive_int(raw.get("client_id")) or
            raw["client_id"] in excluded_clients or
            set(requests) != {"ledger", "account", "positions"} or
            any(not positive_int(x) for x in requests.values()) or len(set(requests.values())) != 3 or
            (not isinstance(raw.get("ends"), list) or any(not positive_int(x) for x in raw["ends"]) or
             not {requests["ledger"], requests["account"]}.issubset(set(raw["ends"]))) or
            raw.get("transport_errors") or raw.get("connection_lost_during_download") is not False):
        raise EvidenceError("ACCOUNT_DOWNLOAD_IDENTITY_OR_BARRIER_INVALID")
    rows = raw.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > 4096:
        raise EvidenceError("ACCOUNT_DOWNLOAD_ROWS_INVALID")
    # IB initial update-end callbacks are transport barriers, not a common
    # Broker valuation timestamp. Bind every new read to one request-ID cohort;
    # do not label local callback arrival as a simultaneous account mark.
    cohort = raw.get('capture_cohort')
    # V2 collector receipts must carry a verified transport cohort. Keep V1
    # accepted only for immutable, pre-upgrade opening NAV replay.
    if raw.get('schema') == 'heptatrader.account-download.v2' and cohort is None:
        raise EvidenceError('ACCOUNT_CAPTURE_COHORT_REQUIRED')
    if cohort is not None:
        if (not isinstance(cohort, dict) or set(cohort) != {
                'schema', 'request_end_at_ms', 'first_row_callback_at_ms',
                'last_row_callback_at_ms', 'broker_valuation_tick_attested', 'time_basis'} or
                cohort.get('schema') != 'heptatrader.account-capture-cohort.v1' or
                cohort.get('broker_valuation_tick_attested') is not False or
                cohort.get('time_basis') != 'LOCAL_CALLBACK_ARRIVAL_NOT_BROKER_VALUATION_TICK' or
                type(cohort.get('request_end_at_ms')) is not dict or
                set(cohort['request_end_at_ms']) != set(requests) or
                any(type(t) is not int or not first <= t <= completed
                    for t in cohort['request_end_at_ms'].values()) or
                type(cohort.get('first_row_callback_at_ms')) is not int or
                type(cohort.get('last_row_callback_at_ms')) is not int or
                not first <= cohort['first_row_callback_at_ms'] <=
                    cohort['last_row_callback_at_ms'] <= completed or
                cohort['last_row_callback_at_ms'] - cohort['first_row_callback_at_ms'] > 2000 or
                cohort['first_row_callback_at_ms'] != min(r.get('observed_at_ms', 0) for r in rows) or
                cohort['last_row_callback_at_ms'] != max(r.get('observed_at_ms', 0) for r in rows) or
                any(r.get('observed_at_ms', 0) > cohort['request_end_at_ms'][key]
                    for key in ('ledger', 'account')
                    for r in rows if r.get('request_id') == requests[key])):
            raise EvidenceError('ACCOUNT_CAPTURE_COHORT_TIME_OR_IDENTITY_INVALID')
    ledgers: dict[str, dict] = {}
    aggregate = {}
    aggregate_keys = {"AccountCode", "AccountReady", "NetLiquidation", "AccruedDividend", "NLVAndMarginInReview"}
    for row in rows:
        if (row.get("account") != period["account"] or row.get("model") != "" or
                row.get("request_id") not in (requests["ledger"], requests["account"]) or
                not positive_int(row.get("observed_at_ms")) or not first <= row["observed_at_ms"] <= completed):
            raise EvidenceError("ACCOUNT_ROW_SCOPE_OR_TIME_INVALID")
        key, currency, value = row.get("key"), row.get("currency"), row.get("value")
        if not isinstance(key, str) or not isinstance(value, str) or len(value) > 256:
            raise EvidenceError("ACCOUNT_ROW_VALUE_INVALID")
        if row["request_id"] == requests["account"]:
            if key not in aggregate_keys:
                continue  # includes intentionally ambiguous unprefixed AccruedCash
            identity = key, currency
            if identity in aggregate and aggregate[identity] != value:
                raise EvidenceError("ACCOUNT_AGGREGATE_CONFLICT")
            aggregate[identity] = value
            continue
        key = key.removeprefix("$LEDGER-")
        if currency != "BASE" and (not isinstance(currency, str) or not CURRENCY.fullmatch(currency)):
            raise EvidenceError("ACCOUNT_CURRENCY_INVALID")
        ledger = ledgers.setdefault(currency, {})
        if key in ledger and ledger[key] != value:
            raise EvidenceError("ACCOUNT_LEDGER_CONFLICT")
        ledger[key] = value
    if aggregate.get(("AccountCode", "")) != period["account"] or aggregate.get(("AccountReady", ""), "").lower() != "true":
        raise EvidenceError("ACCOUNT_NOT_READY_OR_WRONG_ACCOUNT")
    if aggregate.get(("NLVAndMarginInReview", ""), "unknown").lower() != "false":
        raise EvidenceError("BROKER_NAV_UNDER_REVIEW")
    navs = [(currency, number(value)) for (key, currency), value in aggregate.items() if key == "NetLiquidation"]
    if len(navs) != 1 or navs[0][0] != period["currency"] or "BASE" not in ledgers:
        raise EvidenceError("ACCOUNT_BASE_CURRENCY_OR_NAV_MISSING")
    base_currency, nav = navs[0]
    # Some Gateway versions preserve the BASE sentinel in RealCurrency.
    # Resolve only that literal sentinel through the unique account-scoped
    # NetLiquidation currency and its independently identified currency ledger.
    # This is not permission to infer a missing/mismatched currency or relax
    # any component, aggregate or FX amount reconciliation below.
    base_identity = ledgers["BASE"].get("RealCurrency")
    if (base_identity not in (base_currency, "BASE") or len(ledgers) > 65 or
            (base_identity == "BASE" and
             ledgers.get(base_currency, {}).get("RealCurrency") != base_currency)):
        raise EvidenceError("ACCOUNT_LEDGER_BASE_IDENTITY_INVALID")
    values = {}
    for currency, ledger in ledgers.items():
        if ledger.get("AccountOrGroup") != period["account"]:
            raise EvidenceError("ACCOUNT_LEDGER_AGGREGATE_NOT_EXACT_ACCOUNT")
        required = (*NAV_KEYS, *ASSET_KEYS)
        if any(k not in ledger for k in required):
            raise EvidenceError("ACCOUNT_LEDGER_FIELD_MISSING:" + currency)
        converted = {k: number(ledger[k]) for k in required}
        if converted["ExchangeRate"] <= 0 or (currency in ("BASE", base_currency) and converted["ExchangeRate"] != 1):
            raise EvidenceError("ACCOUNT_FX_RATE_INVALID")
        if not near(converted["CashBalance"], converted["TotalCashBalance"]):
            raise EvidenceError("ACCOUNT_CASH_SCOPE_CONFLICT")
        # FuturesPNL is already in cash; do not add it a second time.
        reconstructed = converted["CashBalance"] + converted["AccruedCash"] + converted["NetDividend"] + sum((converted[k] for k in ASSET_KEYS), ZERO)
        if not near(reconstructed, converted["NetLiquidationByCurrency"]):
            raise EvidenceError("UNEXPLAINED_NAV_COMPONENT:" + currency)
        values[currency] = converted
    base = values["BASE"]
    dividend = aggregate.get(("AccruedDividend", base_currency))
    if dividend is None or not near(number(dividend), base["NetDividend"]):
        raise EvidenceError("ACCOUNT_DIVIDEND_ACCRUAL_SCOPE_CONFLICT")
    currencies = [c for c in values if c != "BASE"]
    if not currencies or base_currency not in currencies:
        raise EvidenceError("ACCOUNT_CURRENCY_SET_INCOMPLETE")
    with localcontext() as ctx:
        ctx.prec = 40
        translated = sum((values[c]["NetLiquidationByCurrency"] * values[c]["ExchangeRate"] for c in currencies), ZERO)
        translated_accrued = sum((values[c]["AccruedCash"] * values[c]["ExchangeRate"] for c in currencies), ZERO)
        # IB reports current ledger ExchangeRate values, while interest accruals
        # are translated into base currency using daily/transaction-time FX.
        # Therefore current-rate translation of AccruedCash is diagnostic only.
        # Reconcile the non-interest portion with current ledger rates, then use
        # the Broker's BASE AccruedCash exactly once. This preserves strict NAV
        # identity without inventing a current-rate interest conversion basis.
        translated_non_accrual = sum(((values[c]["NetLiquidationByCurrency"] - values[c]["AccruedCash"]) *
                                      values[c]["ExchangeRate"] for c in currencies), ZERO)
        base_non_accrual = base["NetLiquidationByCurrency"] - base["AccruedCash"]
        bound = Decimal("0.01")
        for c in currencies:
            if c != base_currency:  # USD identity conversion has exactly zero rate uncertainty.
                bound += abs(values[c]["NetLiquidationByCurrency"] - values[c]["AccruedCash"]) * rounding_bound(ledgers[c]["ExchangeRate"])
            bound += abs(values[c]["ExchangeRate"]) * (rounding_bound(ledgers[c]["NetLiquidationByCurrency"]) +
                                                        rounding_bound(ledgers[c]["AccruedCash"]))
        precision_bound = bound
        # IB emits FX translation and aggregate totals as separately rounded
        # fields, not an atomic valuation tick. Use an explicit, fixed $1
        # reconciliation envelope for current-rate components; never widen it
        # on a mismatch. Aggregate NAV must independently agree with BASE NAV.
        bound = Decimal("1.00")
        if not near(translated_non_accrual, base_non_accrual, bound) or not near(nav, base["NetLiquidationByCurrency"]):
            raise EvidenceError("ACCOUNT_NAV_FX_RECONCILIATION_FAILED")
        for field in ("CashBalance", "NetDividend"):
            check = sum((values[c][field] * values[c]["ExchangeRate"] for c in currencies), ZERO)
            if not near(check, base[field], bound):
                raise EvidenceError("ACCOUNT_COMPONENT_FX_RECONCILIATION_FAILED:" + field)
    positions = raw.get("positions", [])
    position_ids = set()
    if not isinstance(positions, list) or len(positions) > 256:
        raise EvidenceError("ACCOUNT_POSITION_BOUND")
    for p in positions:
        if (p.get("account") != period["account"] or p.get("model") != "" or
                p.get("request_id") != requests["positions"] or not positive_int(p.get("conid")) or
                p["conid"] in position_ids):
            raise EvidenceError("ACCOUNT_POSITION_IDENTITY_INVALID")
        number(p.get("quantity")); number(p.get("average_cost")); position_ids.add(p["conid"])
    return {"schema": nav_schema, "account": period["account"], "currency": base_currency,
            identity_key: period[identity_key], "source": "IB_ACCOUNT_UPDATES_MULTI_PAIRED_LEDGER",
            "authoritative": True, "mutation_authorized": False, "nav": amount(nav),
            "ledger_nav": amount(base["NetLiquidationByCurrency"]), "cash": amount(base["CashBalance"]),
            "accrued_interest": amount(base["AccruedCash"]), "dividend_accruals": amount(base["NetDividend"]),
            "marked_assets": amount(sum((base[k] for k in ASSET_KEYS), ZERO)),
            "nav_includes_reported_accruals": True, "booked_fees_already_in_cash": True,
            "translated_currency_nav": amount(translated), "fx_reconciliation_difference": amount(translated-base["NetLiquidationByCurrency"]),
            "translated_non_accrual_nav": amount(translated_non_accrual),
            "non_accrual_fx_reconciliation_difference": amount(translated_non_accrual-base_non_accrual),
            "accrued_interest_current_rate_translation": amount(translated_accrued),
            "accrued_interest_current_rate_translation_difference": amount(translated_accrued-base["AccruedCash"]),
            "accrued_interest_fx_basis": "BROKER_BASE_DAILY_OR_TRANSACTION_TIME_FX_NOT_CURRENT_LEDGER_RATE",
            "fx_reconciliation_acceptance_basis": "CURRENT_RATE_NON_ACCRUAL_PLUS_BROKER_BASE_ACCRUED_CASH",
            "fx_reconciliation_bound_usd": amount(bound), "fx_reported_precision_bound_usd": amount(precision_bound),
            "fx_translation_atomic_tick": False, "ledgers": {c: {k: amount(v) for k, v in x.items()} for c, x in values.items()},
            **({"capture_cohort": cohort} if cohort is not None else {}),
            "positions": positions, "positions_complete": requests["positions"] in raw.get("ends", []),
            "observed_at_ms": completed, "started_at_ms": first, "valid_until_ms": first + 30000,
            "connection_id": raw["connection_id"], "client_id": raw["client_id"],
            "raw_sha256": digest(raw), "journal_fingerprint": raw.get("journal_fingerprint"),
            "period_sha256": raw.get("period_sha256"), "producer_source_sha": raw.get("producer_source_sha")}


def validate_nav(nav: dict, period: dict, at_ms: int, historical: bool = False) -> None:
    if ("observation_scope_id" in nav or nav.get("schema") != "heptatrader.account-nav.v1" or nav.get("authoritative") is not True or
            nav.get("mutation_authorized") is not False or nav.get("account") != period["account"] or
            nav.get("currency") != period["currency"] or nav.get("period_id") != period["period_id"] or
            nav.get("source") != "IB_ACCOUNT_UPDATES_MULTI_PAIRED_LEDGER" or
            nav.get("nav_includes_reported_accruals") is not True or
            not positive_int(nav.get("observed_at_ms")) or nav["observed_at_ms"] > at_ms or
            not positive_int(nav.get("started_at_ms")) or not positive_int(nav.get("valid_until_ms")) or
            not nav["started_at_ms"] <= nav["observed_at_ms"] < nav["valid_until_ms"] or
            nav["valid_until_ms"] - nav["started_at_ms"] > 30000 or
            (not historical and at_ms >= nav["valid_until_ms"])):
        raise EvidenceError("ACCOUNT_NAV_MISSING_STALE_OR_WRONG_SCOPE")
    number(nav.get("nav")); number(nav.get("accrued_interest"))


def validate_nav_evidence(nav: dict, raw: dict, period: dict, at_ms: int) -> None:
    """A normalised cache is not its own source; reproduce it from the paired raw read."""
    validate_nav(nav, period, at_ms)
    if nav.get("raw_sha256") != digest(raw):
        raise EvidenceError("ACCOUNT_NAV_RAW_SOURCE_MISMATCH")
    if normalize_nav(raw, period) != nav:
        raise EvidenceError("ACCOUNT_NAV_NORMALIZED_SOURCE_CONFLICT")


def liquidation_estimate(nav: dict, model: dict | None) -> dict:
    result = {"estimated": True, "actual_expense": False, "mutation_authorized": False,
              "complete": False, "estimated_future_exit_commissions": None,
              "estimated_nav_after_liquidation": None, "reason_codes": []}
    if not model:
        result["reason_codes"] = ["EXIT_COST_MODEL_UNCONFIGURED"]; return result
    if (model.get("schema") != "heptatrader.exit-cost-model.v1" or model.get("currency") != "USD" or
            model.get("assumed_not_account_qualified") is not True or not model.get("version") or
            model.get("rebates_assumed") is not False):
        raise EvidenceError("EXIT_COST_MODEL_INVALID")
    if nav.get("positions_complete") is not True:
        result["reason_codes"] = ["EXIT_POSITION_SET_INCOMPLETE"]; return result
    ledgers = nav["ledgers"]
    noncash = [p for p in nav["positions"] if p["sec_type"] != "CASH" and number(p["quantity"]) != 0]
    rows = []
    unsupported = [p["sec_type"] + ":" + p["symbol"] for p in noncash if p["sec_type"] != "CRYPTO" or p["symbol"] != "BTC" or p["currency"] != "USD"]
    if len(noncash) > 1 or any(number(ledgers["BASE"][k]) != 0 for k in ASSET_KEYS if k != "Cryptocurrency"):
        unsupported.append("NON_BTC_ASSET_VALUATION")
    crypto_value = abs(number(ledgers["BASE"]["Cryptocurrency"]))
    if bool(crypto_value) != bool(noncash):
        unsupported.append("POSITION_AND_ASSET_VALUE_MISMATCH")
    if unsupported:
        result["reason_codes"] = ["EXIT_MODEL_UNSUPPORTED:" + x for x in unsupported]; return result
    def fee(notional, spec):
        rate, minimum = number(spec["rate"]), number(spec["minimum_usd"])
        if not 0 <= rate <= 1 or not 0 <= minimum <= 10000:
            raise EvidenceError("EXIT_COST_RATE_INVALID")
        return max(notional * rate, minimum) if notional else ZERO
    if crypto_value:
        rows.append({"asset": "BTC.USD", "basis": "BROKER_CRYPTO_MARKED_VALUE", "notional_usd": amount(crypto_value),
                     "commission_usd": amount(fee(crypto_value, model["BTC.USD"]))})
    # CASH positions reported by reqPositionsMulti can be virtual FX. Only
    # actual non-USD CashBalance is converted here, once. Tiny tails are not hidden.
    for currency, ledger in ledgers.items():
        if currency in ("BASE", "USD"): continue
        notional = abs(number(ledger["CashBalance"]) * number(ledger["ExchangeRate"]))
        if notional:
            rows.append({"asset": currency + "_CASH_TO_USD", "basis": "POSTED_CASH_BALANCE",
                         "notional_usd": amount(notional), "commission_usd": amount(fee(notional, model["FX"]))})
    upfront = sum((number(r["commission_usd"]) for r in rows), ZERO)
    gross_turnover = sum((number(r["notional_usd"]) for r in rows), ZERO)
    scenarios = []
    for name, spec in model["scenarios"].items():
        impact = number(spec["all_in_price_impact_bps"])
        count = spec["new_order_count_multiplier"]
        if not 0 <= impact <= 1000 or not positive_int(count) or count > 10:
            raise EvidenceError("EXIT_SCENARIO_INVALID")
        price_impact = gross_turnover * impact / 10000
        total_cost = upfront * count + price_impact
        scenarios.append({"name": name, "assumed_new_orders_per_asset": count,
                          "assumed_spread_plus_slippage_bps": amount(impact), "commissions": amount(upfront * count),
                          "spread_plus_slippage": amount(price_impact), "total_estimated_cost": amount(total_cost),
                          "estimated_nav_after_liquidation": amount(number(nav["nav"]) - total_cost)})
    if not scenarios: raise EvidenceError("EXIT_SCENARIOS_MISSING")
    result.update(complete=True, model_version=model["version"], assumed_not_account_qualified=True,
                  estimated_future_exit_commissions=amount(upfront), rows=rows, scenarios=scenarios,
                  reason_codes=["INDICATIVE_NOT_EXECUTABLE_QUOTE", "NO_FUTURE_REBATE_ASSUMED", "DUST_CONVERSION_FEASIBILITY_NOT_PROVEN"],
                  excluded=["future_financing", "personal_income_tax", "unreported_charges"],
                  estimated_nav_after_liquidation=scenarios[0]["estimated_nav_after_liquidation"])
    return result


def account_report(period: dict, nav: dict | None, activity: dict | None, model: dict | None,
                   first_observation: dict | None, at_ms: int) -> dict:
    """Keep current live NAV separate from statement-period returns and model costs."""
    validate_period(period)
    result = {"schema": "heptatrader.account-pnl.v1", "account": period["account"], "currency": "USD",
              "period_id": period["period_id"], "period_started_at_ms": period["started_at_ms"],
              "observed_at_ms": at_ms, "mutation_authorized": False, "nav": None, "nav_complete": False,
              "broker_reported_account_net_pnl": None, "all_in_account_net_pnl": None,
              "personal_income_tax": None, "unreported_costs": None,
              "posted_financing": None, "posted_withholding_tax": None, "posted_transaction_tax": None,
              "posted_other_fees": None, "reason_codes": [], "complete": False}
    try:
        if nav is None: raise EvidenceError("ACCOUNT_NAV_NOT_COLLECTED")
        validate_nav(nav, period, at_ms)
        result.update(nav=nav["nav"], nav_complete=True, nav_observed_at_ms=nav["observed_at_ms"],
                      nav_valid_until_ms=nav["valid_until_ms"], cash=nav["cash"], marked_assets=nav["marked_assets"],
                      accrued_interest=nav["accrued_interest"], dividend_accruals=nav["dividend_accruals"],
                      nav_includes_reported_accruals=True, ledgers=nav["ledgers"])
        try:
            result["future_exit_costs"] = liquidation_estimate(nav, model)
        except (EvidenceError, KeyError, TypeError) as error:
            result["future_exit_costs"] = {"estimated": True, "actual_expense": False, "complete": False,
                "estimated_future_exit_commissions": None, "estimated_nav_after_liquidation": None,
                "reason_codes": [str(error) if isinstance(error, EvidenceError) else "EXIT_MODEL_INVALID"]}
        if first_observation:
            validate_nav(first_observation, period, at_ms, historical=True)
            result["observed_nav_change"] = {"from_ms": first_observation["observed_at_ms"],
                "value": amount(number(nav["nav"])-number(first_observation["nav"])),
                "is_profit": False, "reason": "NOT_ADJUSTED_FOR_EXTERNAL_CASH_OR_POSITION_TRANSFERS"}
    except (EvidenceError, KeyError, TypeError) as error:
        result["reason_codes"].append(str(error) if isinstance(error, EvidenceError) else "ACCOUNT_INPUT_INVALID")
    # A current NLV minus an old cash-only reset is NOT a return. The reset did
    # not record aggregate NAV/accruals, and post-reset statement coverage is
    # needed to distinguish deposits from income. Never auto-reset either book.
    if activity:
        if (activity.get("schema") != "heptatrader.account-activity-index.v1" or activity.get("account") != period["account"]):
            raise EvidenceError("ACCOUNT_ACTIVITY_SCOPE_INVALID")
        statements = activity.get("statements", [])
        if not isinstance(statements, list) or len(statements) > 366:
            raise EvidenceError("ACCOUNT_ACTIVITY_BOUND")
        result["statement_periods"] = statements[-12:]
        result["statement_period_count"] = len(statements)
        # Only an exact period and exact observed NAV endpoint can establish
        # current account return. Overnight statements cannot cover today's gap.
        matching = [s for s in statements if s.get("from_ms") == period["started_at_ms"] and
                    s.get("to_ms") == result.get("nav_observed_at_ms") and s.get("complete") is True]
        if result["nav_complete"] and len(matching) == 1 and near(number(matching[0]["ending_nav"]), number(result["nav"])):
            s = matching[0]
            result["broker_reported_account_net_pnl"] = s["broker_reported_account_net_pnl"]
            result["posted_financing"] = s.get("components", {}).get("interest")
            result["posted_withholding_tax"] = s.get("components", {}).get("withholdingTax")
            result["posted_transaction_tax"] = s.get("components", {}).get("transactionTax")
            result["posted_other_fees"] = s.get("components", {}).get("otherFees")
        else:
            result["reason_codes"].append("STATEMENT_DOES_NOT_COVER_RESET_TO_LIVE_NAV")
    else:
        result["reason_codes"].append("BROKER_CASH_FLOW_AND_CHARGE_STATEMENTS_MISSING")
    if result["broker_reported_account_net_pnl"] is None:
        result["reason_codes"].append("RESET_OPENING_FULL_NAV_OR_EXTERNAL_FLOW_COVERAGE_MISSING")
    result["reason_codes"].append("EXTERNAL_UNREPORTED_LIABILITIES_AND_PERSONAL_TAX_UNPROVEN")
    result["account_return_formula"] = "ending_NAV - starting_NAV - net_external_cash_and_asset_transfers"
    result["no_double_charge"] = "Posted commissions/taxes/interest already in NAV; accrual settlements are not charged twice."
    result["all_in_scope_note"] = "Broker-reported return excludes unreported external liabilities and personal income tax; future exit costs are estimates, never incurred charges."
    return result


def reconcile_nav_return(period: dict, opening: dict, closing: dict, flows: dict,
                         liabilities: dict | None, at_ms: int) -> dict:
    """Funding-neutral broker return and optional evidenced outside-NAV liabilities.

    Inputs have explicit complete coverage, source digests and exact accounts.
    Coverage can prove an empty flow set; absence of a file cannot. Statement
    payments of already-accrued interest must not be subtracted from NAV again.
    """
    validate_period(period)
    validate_nav(opening, period, at_ms, historical=True)
    validate_nav(closing, period, at_ms)
    begin, end = period['started_at_ms'], closing['observed_at_ms']
    if not opening['started_at_ms'] <= begin <= opening['valid_until_ms'] or opening['observed_at_ms'] > begin:
        raise EvidenceError('RESET_OPENING_NAV_NOT_OBSERVED_AT_BOUNDARY')
    if (flows.get('schema') != 'heptatrader.external-flows.v1' or flows.get('account') != period['account'] or
            flows.get('currency') != period['currency'] or flows.get('source') != 'IMPORTED_IB_FLEX_ACTIVITY' or
            flows.get('complete') is not True or flows.get('cash_transfers_complete') is not True or
            flows.get('position_transfers_complete') is not True or
            not positive_int(flows.get('from_ms')) or not positive_int(flows.get('to_ms')) or
            not flows['from_ms'] <= begin < end <= flows['to_ms'] or
            not re.fullmatch('[0-9a-f]{64}', str(flows.get('source_sha256', '')))):
        raise EvidenceError('EXTERNAL_FLOW_COVERAGE_INCOMPLETE')
    rows = flows.get('events')
    if not isinstance(rows, list) or len(rows) > 50000: raise EvidenceError('EXTERNAL_FLOW_EVENT_BOUND')
    seen, funding = {}, ZERO
    for row in rows:
        if (row.get('account') != period['account'] or row.get('kind') not in ('CASH_TRANSFER', 'POSITION_TRANSFER') or
                not isinstance(row.get('id'), str) or not row['id'] or not positive_int(row.get('at_ms')) or
                not flows['from_ms'] <= row['at_ms'] < flows['to_ms'] or row.get('currency') != 'USD'):
            raise EvidenceError('EXTERNAL_FLOW_EVENT_SCOPE_INVALID')
        value = number(row.get('signed_value_usd'))
        signature = row['kind'], row['at_ms'], value
        if row['id'] in seen:
            if seen[row['id']] != signature: raise EvidenceError('EXTERNAL_FLOW_ID_CONFLICT')
            continue
        seen[row['id']] = signature
        if begin < row['at_ms'] <= end: funding += value
    pnl = number(closing['nav']) - number(opening['nav']) - funding
    result = {'broker_reported_account_net_pnl': amount(pnl), 'starting_nav': opening['nav'],
              'ending_nav': closing['nav'], 'net_external_contributions': amount(funding),
              'reported_account_complete': True, 'all_in_account_net_pnl': None,
              'accrued_interest_change': amount(number(closing['accrued_interest'])-number(opening['accrued_interest'])),
              'unbooked_liability_change': None, 'personal_income_tax': None,
              'source_flow_sha256': flows['source_sha256'], 'all_in_complete': False}
    if liabilities is None: return result
    if (liabilities.get('schema') != 'heptatrader.unbooked-liabilities.v1' or
            liabilities.get('account') != period['account'] or liabilities.get('period_id') != period['period_id'] or
            liabilities.get('currency') != 'USD' or liabilities.get('complete') is not True or
            liabilities.get('personal_tax_coverage_complete') is not True or
            liabilities.get('other_cost_coverage_complete') is not True or
            liabilities.get('all_values_excluded_from_broker_nav') is not True or
            not positive_int(liabilities.get('from_ms')) or not positive_int(liabilities.get('to_ms')) or
            not liabilities['from_ms'] <= begin < end <= liabilities['to_ms'] or
            not re.fullmatch('[0-9a-f]{64}', str(liabilities.get('source_sha256', '')))):
        raise EvidenceError('UNBOOKED_LIABILITIES_OR_PERSONAL_TAX_UNPROVEN')
    # These are changes of liabilities during the period, not lifetime balances.
    # A source provider must attest non-overlap with already-booked Broker items.
    tax = number(liabilities.get('personal_tax_liability_change'))
    other = number(liabilities.get('other_unbooked_cost_change'))
    result.update(all_in_account_net_pnl=amount(pnl-tax-other), personal_income_tax=amount(tax),
                  unbooked_liability_change=amount(other), all_in_complete=True,
                  source_liability_sha256=liabilities['source_sha256'])
    return result
