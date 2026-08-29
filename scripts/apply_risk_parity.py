#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_range(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"{path}: start marker not found: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{path}: end marker not found: {end_marker!r}")
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


root = Path(__file__).resolve().parents[1]

# Runtime risk configuration -------------------------------------------------
header = root / "HeptaTrade/execution/execution_service_runtime_config.h"
replace_once(
    header,
    "    std::uint64_t simulatorQuoteTtlMs = 60000;\n"
    "    std::uint64_t simulatorQuoteRefreshIntervalMs = 10000;\n",
    "    std::uint64_t simulatorQuoteTtlMs = 60000;\n"
    "    std::uint64_t simulatorQuoteRefreshIntervalMs = 10000;\n"
    "\n"
    "    bool simulatorOrderSubmissionEnabled = true;\n"
    "    bool simulatorGlobalKillSwitch = false;\n"
    "    bool simulatorFlattenOnly = false;\n"
    "    double simulatorMaxOrderQuantity = 25000.0;\n"
    "    double simulatorMaxOrderNotional = 250000.0;\n"
    "    std::size_t simulatorMaxOrdersPerMinute = 30;\n"
    "    std::size_t simulatorMaxActiveOrders = 50;\n"
    "    double simulatorMaxGrossPosition = 100000.0;\n"
    "    double simulatorMaxPriceDeviationBps = 30.0;\n",
)

config = root / "HeptaTrade/execution/execution_service_runtime_config.cpp"
replace_once(
    config,
    '#include "execution_service_runtime_config.h"\n',
    '#include "execution_service_runtime_config.h"\n'
    '#include "../risk/deterministic_risk_policy.h"\n',
)
replace_once(config, "#include <cctype>\n", "#include <cctype>\n#include <cmath>\n")
replace_once(
    config,
    "bool CanonicalAgentId(const std::string& value)\n",
    "bool ParseBool01(const std::string& value, bool& parsed)\n"
    "{\n"
    "    if (value == \"0\") { parsed = false; return true; }\n"
    "    if (value == \"1\") { parsed = true; return true; }\n"
    "    return false;\n"
    "}\n\n"
    "bool ParsePositiveDouble(const std::string& value, double& parsed)\n"
    "{\n"
    "    if (value.empty()) return false;\n"
    "    char* end = nullptr;\n"
    "    errno = 0;\n"
    "    const double number = std::strtod(value.c_str(), &end);\n"
    "    if (errno != 0 || end == value.c_str() || *end != '\\0' ||\n"
    "        !std::isfinite(number) || number <= 0.0) return false;\n"
    "    parsed = number;\n"
    "    return true;\n"
    "}\n\n"
    "bool CanonicalAgentId(const std::string& value)\n",
)
replace_once(
    config,
    "    reason.clear();\n    return true;\n}\n\nbool ExecutionServiceRuntimeConfig::FromEnvironment",
    "    DeterministicRiskLimits riskLimits;\n"
    "    riskLimits.orderSubmissionEnabled = simulatorOrderSubmissionEnabled;\n"
    "    riskLimits.globalKillSwitch = simulatorGlobalKillSwitch;\n"
    "    riskLimits.flattenOnly = simulatorFlattenOnly;\n"
    "    riskLimits.maxOrderQuantity = simulatorMaxOrderQuantity;\n"
    "    riskLimits.maxOrderNotional = simulatorMaxOrderNotional;\n"
    "    riskLimits.maxOrdersPerMinute = simulatorMaxOrdersPerMinute;\n"
    "    riskLimits.maxActiveOrders = simulatorMaxActiveOrders;\n"
    "    riskLimits.maxGrossPosition = simulatorMaxGrossPosition;\n"
    "    riskLimits.maxPriceDeviationBps = simulatorMaxPriceDeviationBps;\n"
    "    if (!DeterministicRiskPolicy::ValidateLimits(riskLimits, reason))\n"
    "        return false;\n"
    "    reason.clear();\n"
    "    return true;\n"
    "}\n\n"
    "bool ExecutionServiceRuntimeConfig::FromEnvironment",
)
replace_once(
    config,
    '        "HEPTA_EXECUTION_IO_TIMEOUT_MS",\n',
    '        "HEPTA_EXECUTION_IO_TIMEOUT_MS",\n'
    '        "HEPTA_SIM_ORDER_SUBMISSION_ENABLED",\n'
    '        "HEPTA_SIM_GLOBAL_KILL_SWITCH",\n'
    '        "HEPTA_SIM_FLATTEN_ONLY",\n'
    '        "HEPTA_SIM_MAX_ORDER_QTY",\n'
    '        "HEPTA_SIM_MAX_ORDER_NOTIONAL",\n'
    '        "HEPTA_SIM_MAX_ORDERS_PER_MINUTE",\n'
    '        "HEPTA_SIM_MAX_ACTIVE_ORDERS",\n'
    '        "HEPTA_SIM_MAX_GROSS_POSITION",\n'
    '        "HEPTA_SIM_MAX_PRICE_DEVIATION_BPS",\n',
)
replace_once(
    config,
    "    return config.Validate(reason);\n}\n",
    "    const std::string orderEnabled = ReadString(values,\n"
    "        \"HEPTA_SIM_ORDER_SUBMISSION_ENABLED\");\n"
    "    const std::string killSwitch = ReadString(values,\n"
    "        \"HEPTA_SIM_GLOBAL_KILL_SWITCH\");\n"
    "    const std::string flattenOnly = ReadString(values,\n"
    "        \"HEPTA_SIM_FLATTEN_ONLY\");\n"
    "    if ((!orderEnabled.empty() && !ParseBool01(orderEnabled,\n"
    "            config.simulatorOrderSubmissionEnabled)) ||\n"
    "        (!killSwitch.empty() && !ParseBool01(killSwitch,\n"
    "            config.simulatorGlobalKillSwitch)) ||\n"
    "        (!flattenOnly.empty() && !ParseBool01(flattenOnly,\n"
    "            config.simulatorFlattenOnly)))\n"
    "    {\n"
    "        reason = \"EXECUTION_SIMULATOR_RISK_BOOLEAN_INVALID\";\n"
    "        return false;\n"
    "    }\n"
    "    const std::string maxQty = ReadString(values, \"HEPTA_SIM_MAX_ORDER_QTY\");\n"
    "    const std::string maxNotional = ReadString(values,\n"
    "        \"HEPTA_SIM_MAX_ORDER_NOTIONAL\");\n"
    "    const std::string maxGross = ReadString(values,\n"
    "        \"HEPTA_SIM_MAX_GROSS_POSITION\");\n"
    "    const std::string maxDeviation = ReadString(values,\n"
    "        \"HEPTA_SIM_MAX_PRICE_DEVIATION_BPS\");\n"
    "    if ((!maxQty.empty() && !ParsePositiveDouble(maxQty,\n"
    "            config.simulatorMaxOrderQuantity)) ||\n"
    "        (!maxNotional.empty() && !ParsePositiveDouble(maxNotional,\n"
    "            config.simulatorMaxOrderNotional)) ||\n"
    "        (!maxGross.empty() && !ParsePositiveDouble(maxGross,\n"
    "            config.simulatorMaxGrossPosition)) ||\n"
    "        (!maxDeviation.empty() && !ParsePositiveDouble(maxDeviation,\n"
    "            config.simulatorMaxPriceDeviationBps)))\n"
    "    {\n"
    "        reason = \"EXECUTION_SIMULATOR_RISK_DECIMAL_INVALID\";\n"
    "        return false;\n"
    "    }\n"
    "    std::uint64_t riskInteger = 0;\n"
    "    const std::string maxRate = ReadString(values,\n"
    "        \"HEPTA_SIM_MAX_ORDERS_PER_MINUTE\");\n"
    "    if (!maxRate.empty())\n"
    "    {\n"
    "        if (!ParseUnsigned(maxRate, 1000000, riskInteger) || riskInteger == 0)\n"
    "        { reason = \"EXECUTION_SIMULATOR_RISK_RATE_INVALID\"; return false; }\n"
    "        config.simulatorMaxOrdersPerMinute =\n"
    "            static_cast<std::size_t>(riskInteger);\n"
    "    }\n"
    "    const std::string maxActive = ReadString(values,\n"
    "        \"HEPTA_SIM_MAX_ACTIVE_ORDERS\");\n"
    "    if (!maxActive.empty())\n"
    "    {\n"
    "        if (!ParseUnsigned(maxActive, 1000000, riskInteger) || riskInteger == 0)\n"
    "        { reason = \"EXECUTION_SIMULATOR_RISK_ACTIVE_INVALID\"; return false; }\n"
    "        config.simulatorMaxActiveOrders =\n"
    "            static_cast<std::size_t>(riskInteger);\n"
    "    }\n"
    "    return config.Validate(reason);\n"
    "}\n",
)

# Simulator policy ----------------------------------------------------------
composition = root / "HeptaTrade/execution/execution_service_runtime_composition.cpp"
replace_once(
    composition,
    '#include "unix_execution_service_server.h"\n',
    '#include "unix_execution_service_server.h"\n'
    '#include "../risk/deterministic_risk_policy.h"\n',
)
replace_once(composition, "#include <unistd.h>\n", "#include <unistd.h>\n#include <vector>\n")
replace_once(
    composition,
    "    SimulatorPolicyAuthority(ExecutionCoordinator& coordinator,\n"
    "                             DeterministicExecutionVenue& venue)\n"
    "        : m_coordinator(coordinator), m_venue(venue)\n",
    "    SimulatorPolicyAuthority(ExecutionCoordinator& coordinator,\n"
    "                             DeterministicExecutionVenue& venue,\n"
    "                             const ExecutionServiceRuntimeConfig& config)\n"
    "        : m_coordinator(coordinator), m_venue(venue), m_config(config)\n",
)
replace_range(
    composition,
    "    ExecutionCommandResult ValidatePlaceEligibility(\n",
    "    ExecutionControlResult BeginControl",
    "    ExecutionCommandResult ValidatePlaceEligibility(\n"
    "        const PlaceOrderCommand& command) const\n"
    "    {\n"
    "        if (command.expiresAtMs <= 0 ||\n"
    "            OmsJournal::NowEpochMs() > command.expiresAtMs)\n"
    "            return Reject(command.context, \"TOOL_CALL_EXPIRED\",\n"
    "                \"order command expired before authoritative preview/place\", -1);\n"
    "        if (command.instrument.empty() || command.contract.symbol.empty() ||\n"
    "            command.timeInForce != \"DAY\" ||\n"
    "            (command.order.action != \"BUY\" && command.order.action != \"SELL\") ||\n"
    "            (command.order.orderType != \"MKT\" &&\n"
    "             command.order.orderType != \"LMT\") ||\n"
    "            !std::isfinite(command.order.totalQuantity) ||\n"
    "            command.order.totalQuantity <= 0.0)\n"
    "            return Reject(command.context, \"INVALID_ORDER\",\n"
    "                \"normalized order intent is invalid\", -1);\n"
    "        std::string blockReason;\n"
    "        if (m_coordinator.IsMutationBlocked(&blockReason))\n"
    "            return Reject(command.context, \"MUTATION_BLOCKED\", blockReason, -1);\n"
    "        if (m_coordinator.IsSessionOwnerFenced(\n"
    "                command.context.agentId, command.context.sessionId))\n"
    "            return Reject(command.context, \"SESSION_OWNER_FENCED\",\n"
    "                \"revoked or expired session owner cannot mutate\", -1);\n"
    "        if (m_coordinator.IsSessionOwnerRecoveryOnly(\n"
    "                command.context.agentId, command.context.sessionId))\n"
    "            return Reject(command.context, \"SESSION_RECOVERY_ONLY\",\n"
    "                \"root custodian disabled new entry for this session owner\", -1);\n"
    "\n"
    "        const std::uint64_t now =\n"
    "            static_cast<std::uint64_t>(OmsJournal::NowEpochMs());\n"
    "        const MarketQuoteSnapshot quote =\n"
    "            m_venue.GetQuoteSnapshot(command.instrument, now);\n"
    "        if (quote.state == MarketSubscriptionState::Unavailable)\n"
    "            return Reject(command.context, \"AUTHORITATIVE_QUOTE_UNAVAILABLE\",\n"
    "                \"Execution-owned quote subscription is unavailable\", -1);\n"
    "        if (!quote.IsFresh(now))\n"
    "            return Reject(command.context, \"AUTHORITATIVE_QUOTE_STALE\",\n"
    "                \"Execution-owned quote is not fresh\", -1);\n"
    "\n"
    "        const std::map<std::string, double> positions = m_venue.Positions();\n"
    "        double gross = 0.0;\n"
    "        for (std::map<std::string, double>::const_iterator it = positions.begin();\n"
    "             it != positions.end(); ++it) gross += std::fabs(it->second);\n"
    "        const double current = m_venue.Position(command.instrument);\n"
    "        const double signedQuantity = command.order.action == \"BUY\" ?\n"
    "            command.order.totalQuantity : -command.order.totalQuantity;\n"
    "        const double projected = gross - std::fabs(current) +\n"
    "            std::fabs(current + signedQuantity);\n"
    "        const double authoritativePrice = command.order.action == \"BUY\" ?\n"
    "            quote.ask : quote.bid;\n"
    "\n"
    "        std::vector<std::int64_t> attempts;\n"
    "        m_coordinator.GetPlaceSendAttemptTimes(\n"
    "            command.context.account, command.context.executionDomain,\n"
    "            static_cast<std::int64_t>(now) - 60000, attempts);\n"
    "\n"
    "        DeterministicRiskLimits limits;\n"
    "        limits.orderSubmissionEnabled = m_config.simulatorOrderSubmissionEnabled;\n"
    "        limits.globalKillSwitch = m_config.simulatorGlobalKillSwitch;\n"
    "        limits.flattenOnly = m_config.simulatorFlattenOnly;\n"
    "        limits.maxOrderQuantity = m_config.simulatorMaxOrderQuantity;\n"
    "        limits.maxOrderNotional = m_config.simulatorMaxOrderNotional;\n"
    "        limits.maxOrdersPerMinute = m_config.simulatorMaxOrdersPerMinute;\n"
    "        limits.maxActiveOrders = m_config.simulatorMaxActiveOrders;\n"
    "        limits.maxGrossPosition = m_config.simulatorMaxGrossPosition;\n"
    "        limits.maxPriceDeviationBps = m_config.simulatorMaxPriceDeviationBps;\n"
    "\n"
    "        DeterministicRiskContext risk;\n"
    "        risk.action = command.order.action;\n"
    "        risk.orderType = command.order.orderType;\n"
    "        risk.quantity = command.order.totalQuantity;\n"
    "        risk.valuationPrice = command.order.orderType == \"LMT\" ?\n"
    "            command.order.lmtPrice : authoritativePrice;\n"
    "        risk.submittedPrice = command.order.lmtPrice;\n"
    "        risk.referencePrice = authoritativePrice;\n"
    "        risk.ordersInLastMinute = attempts.size();\n"
    "        risk.activeOrderCount = m_venue.ActiveOrderIds().size();\n"
    "        risk.grossAbsolutePosition = gross;\n"
    "        risk.projectedGrossAbsolutePosition = projected;\n"
    "        risk.exposureReducing = projected < gross;\n"
    "        const DeterministicRiskDecision decision =\n"
    "            DeterministicRiskPolicy::Evaluate(limits, risk);\n"
    "        if (!decision.allow)\n"
    "            return Reject(command.context, decision.reasonCode,\n"
    "                decision.detail, -1);\n"
    "\n"
    "        ExecutionCommandResult accepted;\n"
    "        accepted.status = ExecutionCommandStatus::Accepted;\n"
    "        accepted.commandId = command.context.toolCallId;\n"
    "        return accepted;\n"
    "    }\n"
    "    ExecutionControlResult BeginControl",
)
replace_once(
    composition,
    "    ExecutionCoordinator& m_coordinator;\n"
    "    DeterministicExecutionVenue& m_venue;\n",
    "    ExecutionCoordinator& m_coordinator;\n"
    "    DeterministicExecutionVenue& m_venue;\n"
    "    const ExecutionServiceRuntimeConfig m_config;\n",
)
replace_once(
    composition,
    "    m_policyAuthority.reset(new SimulatorPolicyAuthority(*m_coordinator, m_venue));\n",
    "    m_policyAuthority.reset(new SimulatorPolicyAuthority(\n"
    "        *m_coordinator, m_venue, m_config));\n",
)

risk_block_start = '        else if (command.query == "risk.get_limits")\n'
risk_block_end = (
    "        else\n"
    "        {\n"
    "            result.status = ExecutionCommandStatus::Rejected;\n"
    "            result.reasonCode = \"AUTHORITATIVE_READ_QUERY_UNSUPPORTED\";\n")
new_risk_block = (
    '        else if (command.query == "risk.get_limits")\n'
    "        {\n"
    "            std::string blockReason;\n"
    "            const bool blocked = m_coordinator.IsMutationBlocked(&blockReason);\n"
    "            const std::map<std::string, double> positions = m_venue.Positions();\n"
    "            double grossAbsolutePosition = 0.0;\n"
    "            for (std::map<std::string, double>::const_iterator it =\n"
    "                     positions.begin(); it != positions.end(); ++it)\n"
    "                grossAbsolutePosition += std::fabs(it->second);\n"
    "            output << \"{\\\"source\\\":\\\"SIMULATOR\\\",\\\"authoritative\\\":true,\"\n"
    "                   << \"\\\"mutation_blocked\\\":\" << (blocked ? \"true\" : \"false\")\n"
    "                   << \",\\\"reason\\\":\\\"\" << EscapeJson(blockReason) << \"\\\",\"\n"
    "                   << \"\\\"order_submission_enabled\\\":\"\n"
    "                   << (m_config.simulatorOrderSubmissionEnabled ? \"true\" : \"false\")\n"
    "                   << \",\\\"global_kill_switch\\\":\"\n"
    "                   << (m_config.simulatorGlobalKillSwitch ? \"true\" : \"false\")\n"
    "                   << \",\\\"flatten_only\\\":\"\n"
    "                   << (m_config.simulatorFlattenOnly ? \"true\" : \"false\")\n"
    "                   << \",\\\"max_order_quantity\\\":\"\n"
    "                   << m_config.simulatorMaxOrderQuantity\n"
    "                   << \",\\\"max_order_notional\\\":\"\n"
    "                   << m_config.simulatorMaxOrderNotional\n"
    "                   << \",\\\"max_orders_per_minute\\\":\"\n"
    "                   << m_config.simulatorMaxOrdersPerMinute\n"
    "                   << \",\\\"max_active_orders\\\":\"\n"
    "                   << m_config.simulatorMaxActiveOrders\n"
    "                   << \",\\\"max_gross_position\\\":\"\n"
    "                   << m_config.simulatorMaxGrossPosition\n"
    "                   << \",\\\"max_price_deviation_bps\\\":\"\n"
    "                   << m_config.simulatorMaxPriceDeviationBps\n"
    "                   << \",\\\"gross_absolute_position\\\":\"\n"
    "                   << grossAbsolutePosition\n"
    "                   << \",\\\"active_order_count\\\":\"\n"
    "                   << m_venue.ActiveOrderIds().size() << \"}\";\n"
    "        }\n"
)
replace_range(composition, risk_block_start, risk_block_end, new_risk_block + risk_block_end)

# IB PAPER calls the same common policy after venue-specific validation -------
ib_profile = root / "HeptaTrade/execution/ib_paper_execution_profile.cpp"
replace_once(
    ib_profile,
    '#include "ib_paper_execution_profile.h"\n',
    '#include "ib_paper_execution_profile.h"\n'
    '#include "../risk/deterministic_risk_policy.h"\n',
)
replace_once(
    ib_profile,
    "}\n\nconst char* IbPaperExecutionProfileConfig::AuthorizationCredentialName()\n",
    "std::string MapCommonRiskReason(const std::string& code)\n"
    "{\n"
    "    if (code == \"RISK_ORDER_QUANTITY_LIMIT\" ||\n"
    "        code == \"RISK_ORDER_QUANTITY_INVALID\")\n"
    "        return \"IB_PAPER_MAX_ORDER_QUANTITY_EXCEEDED\";\n"
    "    if (code == \"RISK_ORDER_NOTIONAL_LIMIT\" ||\n"
    "        code == \"RISK_VALUATION_PRICE_INVALID\")\n"
    "        return \"IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED\";\n"
    "    if (code == \"RISK_ORDER_RATE_LIMIT\")\n"
    "        return \"IB_PAPER_ORDER_RATE_EXCEEDED\";\n"
    "    if (code == \"RISK_ACTIVE_ORDER_LIMIT\")\n"
    "        return \"IB_PAPER_MAX_ACTIVE_ORDERS_EXCEEDED\";\n"
    "    if (code == \"RISK_GROSS_POSITION_LIMIT\" ||\n"
    "        code == \"RISK_POSITION_SNAPSHOT_INVALID\")\n"
    "        return \"IB_PAPER_MAX_GROSS_POSITION_EXCEEDED\";\n"
    "    return code;\n"
    "}\n"
    "}\n\n"
    "const char* IbPaperExecutionProfileConfig::AuthorizationCredentialName()\n",
)
ib_text = ib_profile.read_text(encoding="utf-8")
method_start = ib_text.find("bool IbPaperExecutionGuard::AllowPlaceAtAuthoritativePrice(")
method_end = ib_text.find("bool IbPaperExecutionGuard::AllowCancel(", method_start)
if method_start < 0 or method_end < 0:
    raise SystemExit("IB risk method markers missing")
segment = ib_text[method_start:method_end]
body_start = segment.find("    const double quantity = std::fabs(command.order.totalQuantity);\n")
body_end_marker = "    reason.clear();\n    return true;\n}\n\n"
body_end = segment.rfind(body_end_marker)
if body_start < 0 or body_end < 0:
    raise SystemExit("IB risk body anchors missing")
replacement = """    const double quantity = std::fabs(command.order.totalQuantity);
    if (command.timeInForce != "DAY" ||
        (command.order.action != "BUY" && command.order.action != "SELL"))
    {
        reason = "IB_PAPER_ORDER_INTENT_INVALID";
        return false;
    }
    if (m_config.UsesExternalLimitDay())
    {
        if (!(command.order.totalQuantity > 0.0) ||
            command.order.auxPrice != 0.0 || command.order.outsideRth ||
            !command.order.orderRef.empty())
        {
            reason = "IB_PAPER_EXTERNAL_ORDER_FIELDS_INVALID";
            return false;
        }
        if (command.order.orderType != "LMT")
        {
            reason = "IB_PAPER_EXTERNAL_LIMIT_ORDERS_ONLY";
            return false;
        }
        if (!std::isfinite(command.order.lmtPrice) ||
            command.order.lmtPrice <= 0.0)
        {
            reason = "IB_PAPER_EXTERNAL_LIMIT_PRICE_REQUIRED";
            return false;
        }
    }
    else
    {
        if (command.order.orderType != "MKT")
        {
            reason = "IB_PAPER_MARKET_ORDERS_ONLY";
            return false;
        }
        if (command.order.lmtPrice != 0.0)
        {
            reason = "IB_PAPER_MARKET_ORDER_LIMIT_PRICE_FORBIDDEN";
            return false;
        }
    }
    if (!std::isfinite(command.referencePrice) || command.referencePrice <= 0.0)
    {
        reason = "IB_PAPER_REFERENCE_PRICE_REQUIRED";
        return false;
    }
    if (!std::isfinite(authoritativePrice) || authoritativePrice <= 0.0)
    {
        reason = m_config.UsesExternalLimitDay() ?
            "IB_PAPER_AUTHORITATIVE_PRICE_REQUIRED" :
            "IB_PAPER_MAX_ORDER_NOTIONAL_EXCEEDED";
        return false;
    }
    if (m_config.UsesExternalLimitDay() &&
        (command.order.lmtPrice != authoritativePrice ||
         command.referencePrice != authoritativePrice))
    {
        reason = "IB_PAPER_EXTERNAL_LIMIT_PRICE_MISMATCH";
        return false;
    }

    PruneRateWindow(nowMs);
    DeterministicRiskLimits limits;
    limits.maxOrderQuantity = m_config.maxOrderQuantity;
    limits.maxOrderNotional = m_config.maxOrderNotional;
    limits.maxOrdersPerMinute = m_config.maxOrdersPerMinute;
    limits.maxActiveOrders = m_config.maxActiveOrders;
    limits.maxGrossPosition = m_config.maxGrossPosition;
    limits.maxPriceDeviationBps = 0.0;

    DeterministicRiskContext context;
    context.action = command.order.action;
    context.orderType = command.order.orderType;
    context.quantity = quantity;
    context.valuationPrice = authoritativePrice;
    context.submittedPrice = command.order.lmtPrice;
    context.referencePrice = authoritativePrice;
    context.ordersInLastMinute = m_acceptedPlaceTimesMs.size();
    context.activeOrderCount = snapshot.activeOrderCount;
    context.grossAbsolutePosition = snapshot.grossAbsolutePosition;
    context.projectedGrossAbsolutePosition =
        snapshot.grossAbsolutePosition + quantity;
    context.exposureReducing = false;
    const DeterministicRiskDecision decision =
        DeterministicRiskPolicy::Evaluate(limits, context);
    if (!decision.allow)
    {
        reason = MapCommonRiskReason(decision.reasonCode);
        return false;
    }
    reason.clear();
    return true;
}

"""
segment = segment[:body_start] + replacement + segment[body_end + len(body_end_marker):]
ib_profile.write_text(
    ib_text[:method_start] + segment + ib_text[method_end:], encoding="utf-8")

# Test registration and modern C++ level ------------------------------------
tests_cmake = root / "tests/CMakeLists.txt"
replace_once(tests_cmake, "        CXX_STANDARD 11\n", "        CXX_STANDARD 17\n")
replace_once(
    tests_cmake,
    "add_custom_target(hepta_core_test_binaries\n",
    "add_executable(hepta_deterministic_risk_policy_tests\n"
    "    deterministic_risk_policy_tests.cpp)\n"
    "target_link_libraries(hepta_deterministic_risk_policy_tests\n"
    "    hepta_risk_core)\n"
    "hepta_register_core_test(hepta_deterministic_risk_policy_tests)\n\n"
    "add_custom_target(hepta_core_test_binaries\n",
)

# Keep the new CMake generator expressions as single arguments.
trade_cmake = root / "HeptaTrade/CMakeLists.txt"
text = trade_cmake.read_text(encoding="utf-8")
text = text.replace(
    "$<$<CXX_COMPILER_ID:GNU,Clang>:-Wall;-Wextra;-Wpedantic>",
    '"$<$<COMPILE_LANG_AND_ID:CXX,GNU,Clang>:-Wall;-Wextra;-Wpedantic>"',
)
text = text.replace(
    "$<$<CONFIG:Release>:-fno-rtti;-ffunction-sections;-fdata-sections>",
    '"$<$<CONFIG:Release>:-fno-rtti;-ffunction-sections;-fdata-sections>"',
)
text = text.replace(
    "$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections>",
    '"$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections>"',
)
text = text.replace(
    "$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections;-fno-rtti>",
    '"$<$<CONFIG:Release>:-ffunction-sections;-fdata-sections;-fno-rtti>"',
)
trade_cmake.write_text(text, encoding="utf-8")

# Documentation index -------------------------------------------------------
docs_index = root / "docs/README.md"
replace_once(
    docs_index,
    "| `RECONCILE-RULES.md` | uncertain recovery 与 authoritative reconciliation |\n",
    "| `RECONCILE-RULES.md` | uncertain recovery 与 authoritative reconciliation |\n"
    "| `RISK-MODEL.md` | Simulator/IB PAPER 共用的确定性风险语义 |\n",
)
