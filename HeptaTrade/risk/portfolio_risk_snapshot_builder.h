#pragma once

#include "pre_trade_risk_engine.h"

#include <cstdint>
#include <string>
#include <vector>

struct PortfolioRiskValuationInput {
    PreTradeRiskInstrumentContract contract;
    std::string authorizedQuoteSourceId;
    std::string authorizedFxSourceId;
    double netQuantity = 0.0;
    PreTradeRiskPriceEvidence mark;
    PreTradeRiskFxEvidence fx;
};

struct PortfolioRiskPendingOrderInput {
    std::string orderId;
    PreTradeRiskInstrumentContract contract;
    std::string authorizedQuoteSourceId;
    std::string authorizedFxSourceId;
    std::string side;
    double quantity = 0.0;
    double limitPrice = 0.0;
    PreTradeRiskPriceEvidence quote;
    PreTradeRiskFxEvidence fx;
};

struct PortfolioRiskSnapshotSetIdentity {
    PreTradeRiskSubject subject;
    bool complete = false;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
};

struct PortfolioRiskAccountInput {
    PreTradeRiskSubject subject;
    bool complete = false;
    std::string baseCurrency;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t observedAtMs = 0;
    double realizedPnl = 0.0;
    double unrealizedPnl = 0.0;
    double peakEquity = 0.0;
    double currentEquity = 0.0;
};

struct PortfolioRiskSnapshotBuildRequest {
    PreTradeRiskSubject subject;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t generation = 0;
    std::int64_t evaluatedAtMs = 0;
    std::int64_t maxEvidenceAgeMs = 0;
    PortfolioRiskSnapshotSetIdentity positionsIdentity;
    PortfolioRiskSnapshotSetIdentity pendingOrdersIdentity;
    std::vector<PortfolioRiskValuationInput> positions;
    std::vector<PortfolioRiskPendingOrderInput> pendingOrders;
    PortfolioRiskAccountInput account;
};

struct PortfolioRiskSnapshotBuildResult {
    bool ok = false;
    std::string reasonCode;
    std::string detail;
    PreTradeRiskAuthoritativeSnapshot snapshot;
};

class PortfolioRiskSnapshotBuilder {
public:
    // Exposure-only assembly never asserts account PnL/equity presence. The
    // full Build contract below still requires independently complete account
    // evidence; callers cannot turn absent account facts into observed zero.
    static PortfolioRiskSnapshotBuildResult BuildExposure(
        const PortfolioRiskSnapshotBuildRequest& request);
    static PortfolioRiskSnapshotBuildResult Build(
        const PortfolioRiskSnapshotBuildRequest& request);
};
