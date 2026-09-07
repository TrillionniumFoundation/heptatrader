#!/usr/bin/env python3
"""Apply the exact reviewed simulator flatten-capacity reservation repair."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "HeptaTrade/simulator/deterministic_execution_venue.h",
    """        bool cancelRequested = false;\n        bool terminal = false;\n        std::string terminalStatus;\n""",
    """        bool cancelRequested = false;\n        bool terminal = false;\n        // True only for an order admitted while flatten-only was active.\n        // The reservation survives deactivation and later policy changes until\n        // the order reaches an authoritative terminal state.\n        bool flattenCapacityReserved = false;\n        std::string terminalStatus;\n""",
)

replace_once(
    "HeptaTrade/simulator/deterministic_execution_venue.cpp",
    """    const auto position = m_positions.find(context.symbol);\n    if (position != m_positions.end()) context.netPosition = position->second;\n""",
    """    const auto position = m_positions.find(context.symbol);\n    if (position != m_positions.end()) context.netPosition = position->second;\n    if (m_riskConfig.flattenOnly)\n    {\n        // Monetary pending exposure cannot protect a breached exit path because\n        // verified flatten orders intentionally bypass gross/loss limits. Keep\n        // a separate quantity reservation for every unresolved order that was\n        // admitted while flatten-only was active, including inactive orders and\n        // cancel requests that have not reached a terminal venue outcome.\n        double reservedQuantity = 0.0;\n        for (const auto& pending : m_orders)\n        {\n            const Order& reserved = pending.second;\n            if (reserved.terminal || !reserved.flattenCapacityReserved ||\n                reserved.instrument != context.symbol)\n                continue;\n            const bool directionMatches =\n                (context.netPosition > 0.0 && reserved.request.action == \"SELL\") ||\n                (context.netPosition < 0.0 && reserved.request.action == \"BUY\");\n            if (!directionMatches ||\n                !std::isfinite(reserved.request.totalQuantity) ||\n                reserved.request.totalQuantity <= 0.0)\n                return reject(\"SIM_FLATTEN_RESERVATION_INCONSISTENT\");\n            reservedQuantity += reserved.request.totalQuantity;\n            if (!std::isfinite(reservedQuantity))\n                return reject(\"SIM_FLATTEN_RESERVATION_INCONSISTENT\");\n        }\n        const double capacity = std::fabs(context.netPosition);\n        const double tolerance = std::max(1.0, capacity) *\n            64.0 * std::numeric_limits<double>::epsilon();\n        if (reservedQuantity > capacity + tolerance ||\n            (capacity <= tolerance && reservedQuantity > tolerance))\n            return reject(\"SIM_FLATTEN_RESERVATION_INCONSISTENT\");\n        if (context.netPosition > 0.0)\n            context.netPosition = std::max(0.0,\n                context.netPosition - reservedQuantity);\n        else if (context.netPosition < 0.0)\n            context.netPosition = std::min(0.0,\n                context.netPosition + reservedQuantity);\n    }\n""",
)

replace_once(
    "HeptaTrade/simulator/deterministic_execution_venue.cpp",
    """    stored.correlationId = correlationId;\n    stored.activated = activate;\n    m_orders[stored.id] = stored;\n""",
    """    stored.correlationId = correlationId;\n    stored.activated = activate;\n    stored.flattenCapacityReserved = m_riskConfig.flattenOnly;\n    m_orders[stored.id] = stored;\n""",
)

replace_once(
    "HeptaTrade/simulator/deterministic_execution_venue.cpp",
    """            if (!marketable) continue;\n            order.terminal = true;\n            order.terminalStatus = \"Filled\";\n""",
    """            if (!marketable) continue;\n            if (order.flattenCapacityReserved)\n            {\n                const auto current = m_positions.find(order.instrument);\n                const double currentPosition = current == m_positions.end() ?\n                    0.0 : current->second;\n                const bool preservesZeroBoundary =\n                    std::isfinite(currentPosition) &&\n                    std::isfinite(order.request.totalQuantity) &&\n                    order.request.totalQuantity > 0.0 &&\n                    ((order.request.action == \"SELL\" &&\n                      currentPosition > 0.0 &&\n                      order.request.totalQuantity <= currentPosition) ||\n                     (order.request.action == \"BUY\" &&\n                      currentPosition < 0.0 &&\n                      order.request.totalQuantity <= -currentPosition));\n                if (!preservesZeroBoundary)\n                {\n                    // Defence in depth for policy transitions or damaged\n                    // recovery state: never execute a formerly flatten-only\n                    // reservation after its reducible capacity has vanished.\n                    order.terminal = true;\n                    order.terminalStatus = \"Rejected\";\n                    ++m_generation;\n                    SimulatedOrderEvent rejected;\n                    rejected.orderId = order.id;\n                    rejected.instrument = order.instrument;\n                    rejected.side = order.request.action;\n                    rejected.status = \"Rejected\";\n                    rejected.remainingQuantity = order.request.totalQuantity;\n                    events.push_back(rejected);\n                    continue;\n                }\n            }\n            order.terminal = true;\n            order.terminalStatus = \"Filled\";\n""",
)

TEST_FUNCTION = r'''
void TestFlattenCapacityReservations()
{
    const InstrumentRef contract = Contract();
    auto flattenRisk = Risk();
    flattenRisk.flattenOnly = true;
    flattenRisk.maxOrderQuantity = 20.0;
    flattenRisk.maxOrderNotional = 0.0;
    flattenRisk.maxWorstCaseGrossNotional = 0.0;
    flattenRisk.maxDailyLoss = 0.0;
    flattenRisk.maxDrawdown = 0.0;
    flattenRisk.maxSnapshotAgeMs = 0;
    const auto exitOrder = [](const char* action, double quantity) {
        OrderIntent order = Order(quantity);
        order.action = action;
        return order;
    };

    {
        std::uint64_t now = 10000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long four = -1, six = -1, extra = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 4.0), "long-four", &four, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "long-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 0.01), "long-extra", &extra, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
        assert(venue.ActivateOrder(four));
        assert(venue.ActivateOrder(six));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "aggregate exact-capacity long exits must reach zero");
    }

    {
        std::uint64_t now = 11000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", -10.0}}, 0, reason));
        long four = -1, six = -1, extra = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 4.0), "short-four", &four, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 6.0), "short-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("BUY", 0.01), "short-extra", &extra, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
        assert(venue.ActivateOrder(four));
        assert(venue.ActivateOrder(six));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "aggregate exact-capacity short exits must reach zero");
    }

    {
        std::uint64_t now = 12000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        std::atomic<bool> start(false);
        std::atomic<int> accepted(0);
        long ids[2] = {-1, -1};
        const OrderIntent exact = exitOrder("SELL", 10.0);
        auto submit = [&](int index) {
            while (!start.load()) std::this_thread::yield();
            if (venue.PlaceOrderCorrelated(contract, exact,
                    "concurrent-flatten-" + std::to_string(index),
                    &ids[index], false))
                ++accepted;
        };
        std::thread first(submit, 0);
        std::thread second(submit, 1);
        start.store(true);
        first.join();
        second.join();
        assert(accepted.load() == 1 &&
               "only one concurrent exact flatten may reserve the position");
    }

    {
        std::uint64_t now = 13000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long cancelling = -1, blocked = -1, replacement = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "cancel-held",
            &cancelling, true));
        assert(venue.CancelOrder(cancelling));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 1.0), "before-cancel-terminal",
            &blocked, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK" &&
               "cancel request must not refund capacity before terminal confirmation");
        venue.Process();
        assert(venue.Position("EUR.USD") == 10.0);
        assert(venue.TerminalOrderStatuses().at(cancelling) == "Cancelled");
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "after-cancel-terminal",
            &replacement, false));
    }

    {
        std::uint64_t now = 14000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long six = -1, five = -1, four = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "policy-six", &six, false));
        auto unrestricted = flattenRisk;
        unrestricted.flattenOnly = false;
        venue.SetRiskConfig(unrestricted);
        venue.SetRiskConfig(flattenRisk);
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 5.0), "policy-five", &five, false));
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 4.0), "policy-four", &four, false));
    }

    {
        std::uint64_t now = 15000;
        DeterministicExecutionVenue venue([&]() { return now; });
        venue.SetRiskConfig(flattenRisk);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 0, reason));
        long reserved = -1, ordinary = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "guarded-reservation",
            &reserved, false));
        auto unrestricted = flattenRisk;
        unrestricted.flattenOnly = false;
        venue.SetRiskConfig(unrestricted);
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 10.0), "ordinary-reduction",
            &ordinary, true));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0);
        assert(venue.ActivateOrder(reserved));
        venue.Process();
        assert(venue.Position("EUR.USD") == 0.0 &&
               "fill-time guard must prevent a stale flatten reservation crossing zero");
        assert(venue.TerminalOrderStatuses().at(reserved) == "Rejected");
    }

    {
        std::uint64_t now = 16000;
        DeterministicExecutionVenue venue([&]() { return now; });
        auto monetary = Risk();
        monetary.flattenOnly = true;
        monetary.maxOrderQuantity = 20.0;
        monetary.maxOrderNotional = 1000.0;
        monetary.maxWorstCaseGrossNotional = 1000.0;
        monetary.maxSnapshotAgeMs = 1000;
        venue.SetRiskConfig(monetary);
        venue.SetQuote("EUR.USD", 1.1000, 1.1002);
        std::string reason;
        assert(venue.RestoreRiskState({{"EUR.USD", 10.0}}, 7, reason));
        long six = -1, five = -1;
        assert(venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 6.0), "monetary-six", &six, false));
        assert(!venue.PlaceOrderCorrelated(
            contract, exitOrder("SELL", 5.0), "monetary-five", &five, false));
        assert(venue.LastRejectReason() == "RISK_FLATTEN_ONLY_BLOCK");
    }
}

'''
replace_once(
    "tests/simulator_risk_runtime_tests.cpp",
    "void TestPreviewAndFinalAdmissionRejectSameRisk()\n",
    TEST_FUNCTION + "void TestPreviewAndFinalAdmissionRejectSameRisk()\n",
)
replace_once(
    "tests/simulator_risk_runtime_tests.cpp",
    """    TestDeterministicRiskReservationAndActivation();\n    TestPreviewAndFinalAdmissionRejectSameRisk();\n""",
    """    TestDeterministicRiskReservationAndActivation();\n    TestFlattenCapacityReservations();\n    TestPreviewAndFinalAdmissionRejectSameRisk();\n""",
)

replace_once(
    "docs/modules/simulator.md",
    """Preview and final order reservation evaluate the same risk function. Final evaluation and reservation hold the venue mutex, so concurrent orders see all earlier pending reservations. Before commit, owner projection emits only `order.reserved`, never accepted evidence. The coordinator activates a reservation only after durable `place_sent` and owner creation; event processing skips unactivated reservations. The final command result requires the durable activation receipt. Event sinks run outside the venue mutex. The lock order is coordinator then venue during reservation and activation; event publication releases the venue mutex before calling the coordinator.\n""",
    """Preview and final order reservation evaluate the same risk function. Final evaluation and reservation hold the venue mutex, so concurrent orders see all earlier pending reservations. Flatten-only admission additionally reserves reducible quantity, not merely money: every unresolved exit admitted under flatten-only consumes the same instrument's remaining long or short capacity, including inactive reservations and cancellation requests until a terminal confirmation. Aggregate reservations may reach but never cross zero. The reservation survives policy transitions, and a fill-time boundary check rejects a stale or inconsistent reservation rather than opening the opposite position. Before commit, owner projection emits only `order.reserved`, never accepted evidence. The coordinator activates a reservation only after durable `place_sent` and owner creation; event processing skips unactivated reservations. The final command result requires the durable activation receipt. Event sinks run outside the venue mutex. The lock order is coordinator then venue during reservation and activation; event publication releases the venue mutex before calling the coordinator.\n""",
)

replace_once(
    "docs/modules/simulator.md",
    """The risk/runtime suite verifies concurrent pending-order admission, preview/final parity, unsupported units, stale evidence, activation ordering and reentrant event sinks. It also starts the actual runtime composition, places and cancels through authenticated local IPC, observes automatically pumped events after quote TTL rollover, and replays terminal owner records. The production IPC portion requires a non-root Gateway identity, matching the daemon policy.\n""",
    """The risk/runtime suite verifies concurrent monetary and flatten-capacity admission, long and short partial/exact exits, duplicate exact exits, deferred activation, cancellation confirmation, policy transitions, fill-time zero-boundary enforcement, preview/final parity, unsupported units, stale evidence, activation ordering and reentrant event sinks. It also starts the actual runtime composition, places and cancels through authenticated local IPC, observes automatically pumped events after quote TTL rollover, and replays terminal owner records. The production IPC portion requires a non-root Gateway identity, matching the daemon policy.\n""",
)

replace_once(
    "scripts/verify_source_gap_closures.py",
    """            \"evidence.quantity * evidence.contract.multiplier * price * fx.rate\",\n            \"afterPosition >= 0.0\",\n""",
    """            \"evidence.quantity * evidence.contract.multiplier * price * fx.rate\",\n            \"flattenCapacityReserved\",\n            \"SIM_FLATTEN_RESERVATION_INCONSISTENT\",\n            \"afterPosition >= 0.0\",\n""",
)
replace_once(
    "scripts/verify_source_gap_closures.py",
    """            \"small short-to-long crossing must be blocked\",\n        ),\n""",
    """            \"small short-to-long crossing must be blocked\",\n        ),\n""",
)
replace_once(
    "scripts/verify_source_gap_closures.py",
    """    if \"pending exposure must count in worst-case gross\" not in tests:\n        errors.append(\n            \"PENDING-EXPOSURE-001: pending exposure hostile regression is missing\"\n        )\n""",
    """    if \"pending exposure must count in worst-case gross\" not in tests:\n        errors.append(\n            \"PENDING-EXPOSURE-001: pending exposure hostile regression is missing\"\n        )\n    require_tokens(\n        root,\n        \"tests/simulator_risk_runtime_tests.cpp\",\n        (\n            \"TestFlattenCapacityReservations\",\n            \"aggregate exact-capacity long exits must reach zero\",\n            \"aggregate exact-capacity short exits must reach zero\",\n            \"only one concurrent exact flatten may reserve the position\",\n            \"cancel request must not refund capacity before terminal confirmation\",\n            \"fill-time guard must prevent a stale flatten reservation crossing zero\",\n        ),\n        \"RISK-001\",\n        errors,\n    )\n""",
)

print("flatten-capacity reservation repair materialized")
