#include "portfolio_risk_snapshot_builder.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>

namespace {
PortfolioRiskSnapshotBuildResult Reject(const char* code, const char* detail) {
    PortfolioRiskSnapshotBuildResult result;
    result.reasonCode = code ? code : "PORTFOLIO_RISK_SNAPSHOT_INVALID";
    result.detail = detail ? detail : "portfolio risk snapshot rejected";
    return result;
}

bool ValidSubject(const PreTradeRiskSubject& subject) {
    return !subject.portfolioId.empty() && !subject.account.empty() &&
        !subject.venue.empty() && !subject.baseCurrency.empty() &&
        !subject.instruments.empty() && subject.instruments.count("") == 0;
}

bool SameSubject(const PreTradeRiskSubject& left,
                 const PreTradeRiskSubject& right) {
    return ValidSubject(left) && ValidSubject(right) &&
        left.portfolioId == right.portfolioId &&
        left.account == right.account &&
        left.venue == right.venue &&
        left.baseCurrency == right.baseCurrency &&
        left.instruments == right.instruments;
}

bool ValidContract(const PreTradeRiskInstrumentContract& contract) {
    if (contract.specificationId.empty() || contract.specificationVersion == 0 ||
        contract.instrument.empty() || contract.quoteCurrency.empty() ||
        !std::isfinite(contract.multiplier) || contract.multiplier <= 0.0 ||
        contract.priceUnit != PreTradeRiskPriceUnit::QuoteCurrencyPerUnit)
        return false;
    switch (contract.kind) {
    case PreTradeRiskInstrumentKind::CashFx:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::BaseCurrencyUnits &&
            contract.multiplier == 1.0;
    case PreTradeRiskInstrumentKind::Stock:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::Shares &&
            contract.multiplier == 1.0;
    case PreTradeRiskInstrumentKind::Future:
    case PreTradeRiskInstrumentKind::Option:
        return contract.quantityUnit == PreTradeRiskQuantityUnit::Contracts;
    default:
        return false;
    }
}

bool SameContract(const PreTradeRiskInstrumentContract& left,
                  const PreTradeRiskInstrumentContract& right) {
    return ValidContract(left) && ValidContract(right) &&
        left.specificationId == right.specificationId &&
        left.specificationVersion == right.specificationVersion &&
        left.instrument == right.instrument && left.kind == right.kind &&
        left.quantityUnit == right.quantityUnit &&
        left.priceUnit == right.priceUnit &&
        left.multiplier == right.multiplier &&
        left.quoteCurrency == right.quoteCurrency;
}

bool Fresh(std::int64_t observedAtMs,
           const PortfolioRiskSnapshotBuildRequest& request) {
    return observedAtMs > 0 && observedAtMs <= request.evaluatedAtMs &&
        request.evaluatedAtMs - observedAtMs <= request.maxEvidenceAgeMs;
}

bool ValidSetIdentity(const PortfolioRiskSnapshotSetIdentity& identity,
                      const PortfolioRiskSnapshotBuildRequest& request) {
    return identity.complete &&
        SameSubject(identity.subject, request.subject) &&
        identity.connectionEpoch == request.connectionEpoch &&
        identity.generation == request.generation &&
        Fresh(identity.observedAtMs, request);
}

bool ValidPrice(const PreTradeRiskPriceEvidence& price,
                const PreTradeRiskInstrumentContract& contract,
                const std::string& expectedSource,
                const PortfolioRiskSnapshotBuildRequest& request) {
    return !expectedSource.empty() && price.sourceId == expectedSource &&
        price.instrument == contract.instrument &&
        price.currency == contract.quoteCurrency &&
        price.connectionEpoch == request.connectionEpoch &&
        price.generation == request.generation &&
        std::isfinite(price.price) && price.price > 0.0 &&
        Fresh(price.observedAtMs, request);
}

bool ValidFx(const PreTradeRiskFxEvidence& fx,
             const PreTradeRiskInstrumentContract& contract,
             const std::string& expectedSource,
             const PortfolioRiskSnapshotBuildRequest& request) {
    return !expectedSource.empty() && fx.sourceId == expectedSource &&
        fx.fromCurrency == contract.quoteCurrency &&
        fx.toCurrency == request.subject.baseCurrency &&
        fx.connectionEpoch == request.connectionEpoch &&
        fx.generation == request.generation &&
        std::isfinite(fx.rate) && fx.rate > 0.0 &&
        (fx.fromCurrency != fx.toCurrency || fx.rate == 1.0) &&
        Fresh(fx.observedAtMs, request);
}

bool AddNotional(double quantity,
                 const PreTradeRiskInstrumentContract& contract,
                 double price,
                 double fxRate,
                 double& total) {
    if (!std::isfinite(quantity) || quantity < 0.0 ||
        !std::isfinite(price) || price <= 0.0 ||
        !std::isfinite(fxRate) || fxRate <= 0.0)
        return false;
    const double amount = quantity * contract.multiplier * price * fxRate;
    if (!std::isfinite(amount) || amount < 0.0 ||
        total > std::numeric_limits<double>::max() - amount)
        return false;
    total += amount;
    return std::isfinite(total);
}

struct InstrumentAuthority {
    PreTradeRiskInstrumentContract contract;
    std::string quoteSource;
    std::string fxSource;
};
}

namespace {
PortfolioRiskSnapshotBuildResult BuildSnapshot(
    const PortfolioRiskSnapshotBuildRequest& request, bool includeAccount) {
    if (!ValidSubject(request.subject))
        return Reject("PORTFOLIO_RISK_SUBJECT_INVALID",
                      "portfolio subject must bind portfolio/account/venue/base currency and instruments");
    if (request.connectionEpoch == 0 || request.generation == 0 ||
        request.evaluatedAtMs <= 0 || request.maxEvidenceAgeMs <= 0)
        return Reject("PORTFOLIO_RISK_IDENTITY_INVALID",
                      "epoch, generation, evaluation time and evidence age must be positive");
    if (!request.positionsIdentity.complete ||
        request.positions.size() != request.subject.instruments.size())
        return Reject("PORTFOLIO_RISK_POSITION_SET_INCOMPLETE",
                      "a complete position snapshot with exactly one valuation row per authorized instrument is required");
    if (!ValidSetIdentity(request.positionsIdentity, request))
        return Reject("PORTFOLIO_RISK_POSITION_SET_IDENTITY_INVALID",
                      "position snapshot must bind the exact subject, epoch, generation and fresh observation time");
    if (!request.pendingOrdersIdentity.complete)
        return Reject("PORTFOLIO_RISK_PENDING_SET_INCOMPLETE",
                      "pending-order snapshot completeness must be explicit even when the set is empty");
    if (!ValidSetIdentity(request.pendingOrdersIdentity, request))
        return Reject("PORTFOLIO_RISK_PENDING_SET_IDENTITY_INVALID",
                      "pending-order snapshot must bind the exact subject, epoch, generation and fresh observation time");

    double currentGross = 0.0;
    double pendingBuy = 0.0;
    double pendingSell = 0.0;
    std::int64_t oldestObservedAtMs = std::min(
        request.positionsIdentity.observedAtMs,
        request.pendingOrdersIdentity.observedAtMs);
    std::map<std::string, InstrumentAuthority> authority;

    for (std::size_t index = 0; index < request.positions.size(); ++index) {
        const PortfolioRiskValuationInput& row = request.positions[index];
        if (!ValidContract(row.contract) ||
            request.subject.instruments.count(row.contract.instrument) != 1)
            return Reject("PORTFOLIO_RISK_CONTRACT_INVALID",
                          "position contract is unsupported or outside the authorized subject");
        if (authority.count(row.contract.instrument) != 0)
            return Reject("PORTFOLIO_RISK_POSITION_DUPLICATE",
                          "position valuation contains a duplicate instrument");
        if (row.authorizedQuoteSourceId.empty() || row.authorizedFxSourceId.empty() ||
            !ValidPrice(row.mark, row.contract, row.authorizedQuoteSourceId, request) ||
            !ValidFx(row.fx, row.contract, row.authorizedFxSourceId, request))
            return Reject("PORTFOLIO_RISK_VALUATION_EVIDENCE_INVALID",
                          "position quote/FX evidence is stale, untrusted or identity-mismatched");
        if (!std::isfinite(row.netQuantity))
            return Reject("PORTFOLIO_RISK_POSITION_QUANTITY_INVALID",
                          "position quantity must be finite");
        if (!AddNotional(std::fabs(row.netQuantity), row.contract,
                         row.mark.price, row.fx.rate, currentGross))
            return Reject("PORTFOLIO_RISK_NOTIONAL_OVERFLOW",
                          "position base-currency notional overflowed");
        InstrumentAuthority binding;
        binding.contract = row.contract;
        binding.quoteSource = row.authorizedQuoteSourceId;
        binding.fxSource = row.authorizedFxSourceId;
        authority.insert(std::make_pair(row.contract.instrument, binding));
        oldestObservedAtMs = std::min(oldestObservedAtMs,
            std::min(row.mark.observedAtMs, row.fx.observedAtMs));
    }

    std::set<std::string> pendingOrderIds;
    for (std::size_t index = 0; index < request.pendingOrders.size(); ++index) {
        const PortfolioRiskPendingOrderInput& order = request.pendingOrders[index];
        if (order.orderId.empty() || !pendingOrderIds.insert(order.orderId).second)
            return Reject("PORTFOLIO_RISK_PENDING_ORDER_IDENTITY_INVALID",
                          "pending orders require unique stable identities");
        const std::map<std::string, InstrumentAuthority>::const_iterator found =
            authority.find(order.contract.instrument);
        if (found == authority.end() || !SameContract(found->second.contract, order.contract) ||
            order.authorizedQuoteSourceId != found->second.quoteSource ||
            order.authorizedFxSourceId != found->second.fxSource)
            return Reject("PORTFOLIO_RISK_PENDING_CONTRACT_INVALID",
                          "pending order must use the exact authorized instrument/source binding");
        if ((order.side != "BUY" && order.side != "SELL") ||
            !std::isfinite(order.quantity) || order.quantity <= 0.0 ||
            !std::isfinite(order.limitPrice) || order.limitPrice <= 0.0)
            return Reject("PORTFOLIO_RISK_PENDING_ORDER_INVALID",
                          "pending order side, quantity and limit price must be valid");
        if (!ValidPrice(order.quote, order.contract,
                        order.authorizedQuoteSourceId, request) ||
            !ValidFx(order.fx, order.contract,
                     order.authorizedFxSourceId, request))
            return Reject("PORTFOLIO_RISK_PENDING_EVIDENCE_INVALID",
                          "pending order quote/FX evidence is stale, untrusted or identity-mismatched");
        const double riskPrice = std::max(order.limitPrice, order.quote.price);
        double& total = order.side == "BUY" ? pendingBuy : pendingSell;
        if (!AddNotional(order.quantity, order.contract,
                         riskPrice, order.fx.rate, total))
            return Reject("PORTFOLIO_RISK_NOTIONAL_OVERFLOW",
                          "pending base-currency notional overflowed");
        oldestObservedAtMs = std::min(oldestObservedAtMs,
            std::min(order.quote.observedAtMs, order.fx.observedAtMs));
    }

    const PortfolioRiskAccountInput& account = request.account;
    if (includeAccount && (!account.complete ||
        !SameSubject(account.subject, request.subject) ||
        account.baseCurrency != request.subject.baseCurrency ||
        account.connectionEpoch != request.connectionEpoch ||
        account.generation != request.generation ||
        !Fresh(account.observedAtMs, request) ||
        !std::isfinite(account.realizedPnl) ||
        !std::isfinite(account.unrealizedPnl) ||
        !std::isfinite(account.peakEquity) || account.peakEquity < 0.0 ||
        !std::isfinite(account.currentEquity) || account.currentEquity < 0.0 ||
        account.currentEquity > account.peakEquity))
        return Reject("PORTFOLIO_RISK_ACCOUNT_EVIDENCE_INVALID",
                      "account PnL/equity evidence is incomplete, stale, mixed-generation or inconsistent");
    if (includeAccount)
        oldestObservedAtMs = std::min(oldestObservedAtMs, account.observedAtMs);

    PortfolioRiskSnapshotBuildResult result;
    result.ok = true;
    result.reasonCode = includeAccount ? "PORTFOLIO_RISK_SNAPSHOT_OK" :
        "PORTFOLIO_RISK_EXPOSURE_OK";
    PreTradeRiskAuthoritativeSnapshot& snapshot = result.snapshot;
    snapshot.identity.subject = request.subject;
    snapshot.identity.present = true;
    snapshot.identity.complete = true;
    snapshot.identity.connectionEpoch = request.connectionEpoch;
    snapshot.identity.generation = request.generation;
    snapshot.identity.observedAtMs = oldestObservedAtMs;
    snapshot.identity.evaluatedAtMs = request.evaluatedAtMs;

    snapshot.exposure.subject = request.subject;
    snapshot.exposure.present = true;
    snapshot.exposure.connectionEpoch = request.connectionEpoch;
    snapshot.exposure.generation = request.generation;
    snapshot.exposure.currentGrossNotional = currentGross;
    snapshot.exposure.pendingBuyNotional = pendingBuy;
    snapshot.exposure.pendingSellNotional = pendingSell;

    if (!includeAccount) return result;

    snapshot.pnl.subject = request.subject;
    snapshot.pnl.present = true;
    snapshot.pnl.connectionEpoch = request.connectionEpoch;
    snapshot.pnl.generation = request.generation;
    snapshot.pnl.realizedPnl = account.realizedPnl;
    snapshot.pnl.unrealizedPnl = account.unrealizedPnl;

    snapshot.equity.subject = request.subject;
    snapshot.equity.present = true;
    snapshot.equity.connectionEpoch = request.connectionEpoch;
    snapshot.equity.generation = request.generation;
    snapshot.equity.peakEquity = account.peakEquity;
    snapshot.equity.currentEquity = account.currentEquity;
    return result;
}

} // namespace

PortfolioRiskSnapshotBuildResult PortfolioRiskSnapshotBuilder::Build(
    const PortfolioRiskSnapshotBuildRequest& request) {
    return BuildSnapshot(request, true);
}

PortfolioRiskSnapshotBuildResult PortfolioRiskSnapshotBuilder::BuildExposure(
    const PortfolioRiskSnapshotBuildRequest& request) {
    return BuildSnapshot(request, false);
}
