#pragma once

#include <cmath>

// One source of truth for PAPER BTC cash-sized BUY admission. The resident
// policy remains the exact 25k USD order. Qualification may additionally use a
// deliberately disjoint tiny envelope; values between the two are rejected.
namespace ib_crypto_cash_envelope {
constexpr double ResidentNotionalLimit() { return 25000.0; }
constexpr double ResidentQuantityLimit() { return 1.0; }
constexpr double CanaryNotionalLimit() { return 150.0; }
constexpr double CanaryQuantityLimit() { return 0.0005; }

inline bool CashQuantityValid(double cash, double ask)
{
    if (!std::isfinite(cash) || cash <= 0.0 ||
        std::fabs(cash * 100.0 - std::round(cash * 100.0)) > 1e-7 ||
        !std::isfinite(ask) || ask <= 0.0)
        return false;
    const bool resident =
        std::fabs(cash - ResidentNotionalLimit()) <= 1e-7 &&
        cash / ask <= ResidentQuantityLimit();
    const bool canary =
        cash <= CanaryNotionalLimit() + 1e-7 &&
        cash / ask <= CanaryQuantityLimit() + 1e-15;
    return resident || canary;
}
} // namespace ib_crypto_cash_envelope
