#include "ib_fx_multi_strategy.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <iomanip>
#include <cstdlib>
#include <cerrno>
#include <unordered_map>
#include <cctype>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace {
bool IsTerminalStatus(const std::string& status) {
    return status == "FILLED" || status == "CANCELLED" || status == "APICANCELLED" || status == "INACTIVE" || status == "REJECTED";
}

bool AtomicRenameReplace(const std::string& tmpPath, const std::string& targetPath) {
#ifdef _WIN32
    if (MoveFileExA(tmpPath.c_str(), targetPath.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) == 0) {
        return false;
    }
    return true;
#else
    return std::rename(tmpPath.c_str(), targetPath.c_str()) == 0;
#endif
}

bool IsEnvEnabled(const char* key) {
    const char* p = std::getenv(key);
    if (p == nullptr || p[0] == '\0') return false;
    return std::string(p) == "1" || std::string(p) == "true" || std::string(p) == "TRUE";
}

std::string JoinNames(const std::vector<std::string>& names) {
    std::ostringstream oss;
    for (size_t i = 0; i < names.size(); ++i) {
        if (i) oss << "+";
        oss << names[i];
    }
    return oss.str();
}

void UpperAsciiInPlace(std::string& value) {
    for (char& c : value) {
        c = (char)std::toupper((unsigned char)c);
    }
}

double ClampDouble(double value, double lo, double hi) {
    return std::max(lo, std::min(hi, value));
}

int ClampInt(int value, int lo, int hi) {
    return std::max(lo, std::min(hi, value));
}

double StrategySignalIntervalSeconds(const IbFxStrategyParams& cfg) {
    if (cfg.signalIntervalMs > 0) {
        return std::max(0.001, static_cast<double>(cfg.signalIntervalMs) / 1000.0);
    }
    return std::max(1.0, static_cast<double>(cfg.signalIntervalSec));
}

double StrategyVolatilityReferenceSeconds(const IbFxStrategyParams& cfg) {
    if (cfg.name == "fx_scalping") return 2.0;
    if (cfg.name == "fx_trend") return 5.0;
    if (cfg.name == "fx_momentum_burst") return 1.0;
    if (cfg.name == "fx_market_making") return 1.0;
    if (cfg.name == "fx_mean_revert") return 1.0;
    return StrategySignalIntervalSeconds(cfg);
}

double NormalizeVolatilityBpsForReferenceInterval(double rawVolBps, const IbFxStrategyParams& cfg) {
    if (!(rawVolBps > 0.0)) return 0.0;
    // Keep legacy tuning anchored to each strategy's historical default sampling
    // cadence so changing signalIntervalSec does not silently redefine min/max vol gates.
    const double referenceSec = StrategyVolatilityReferenceSeconds(cfg);
    const double intervalSec = StrategySignalIntervalSeconds(cfg);
    return rawVolBps * std::sqrt(referenceSec / std::max(0.001, intervalSec));
}

int ScalpConfirmWindowSamples(const IbFxStrategyParams& cfg) {
    return ClampInt(std::max(3, cfg.fast + 2), 3, std::max(3, cfg.slow));
}

int ScalpBaseSlopeWindowSamples(const IbFxStrategyParams& cfg) {
    return std::max(6, cfg.slow);
}

int ScalpMaxSlopeWindowSamples(const IbFxStrategyParams& cfg) {
    const int baseWindow = ScalpBaseSlopeWindowSamples(cfg);
    return ClampInt(static_cast<int>(std::llround(static_cast<double>(baseWindow) * 1.8)),
                    6, std::max(6, baseWindow * 3));
}

int ScalpMaxEffectiveConfirms(const IbFxStrategyParams& cfg) {
    return std::max(1, cfg.confirmSamples + 1);
}

std::size_t RequiredScalpSamples(const IbFxStrategyParams& cfg, int effectiveConfirms, int analysisWindow) {
    const int confirms = std::max(1, effectiveConfirms);
    const int lookback = std::max(4, cfg.driftLookbackSamples);
    const std::size_t warmupNeed = static_cast<std::size_t>(std::max(cfg.warmupSamples, lookback + confirms));
    const std::size_t analysisNeed = static_cast<std::size_t>(std::max(3, analysisWindow) + confirms - 1);
    const std::size_t confirmNeed = static_cast<std::size_t>(ScalpConfirmWindowSamples(cfg) + confirms - 1);
    return std::max(warmupNeed, std::max(analysisNeed, confirmNeed));
}

std::size_t RequiredSampleKeep(const IbFxStrategyParams& cfg) {
    std::size_t keep = static_cast<std::size_t>(std::max(cfg.slow * 4, cfg.slow + 4));
    if (cfg.name != "fx_scalping") return keep;
    keep = std::max(keep, RequiredScalpSamples(cfg, ScalpMaxEffectiveConfirms(cfg), ScalpMaxSlopeWindowSamples(cfg)));
    return keep;
}

std::string FormatOrderType(const std::string& orderType) {
    if (orderType.empty()) return "MKT";
    std::string out = orderType;
    UpperAsciiInPlace(out);
    return out;
}

std::string FormatLmtKey(const IbFxOrderIntent& intent) {
    const std::string type = FormatOrderType(intent.orderType);
    if (type != "LMT") return type;
    std::ostringstream oss;
    oss.setf(std::ios::fixed);
    oss << std::setprecision(8) << intent.lmtPrice;
    return type + ":" + oss.str();
}

std::string NormalizeStatus(const std::string& s) {
    std::string out = s;
    UpperAsciiInPlace(out);
    return out;
}

bool IsSameExecutionIntent(const IbFxOrderIntent& a, const IbFxOrderIntent& b) {
    if (FormatOrderType(a.orderType) != FormatOrderType(b.orderType)) return false;
    if (FormatOrderType(a.orderType) != "LMT") return true;
    return std::fabs(a.lmtPrice - b.lmtPrice) < 1e-9;
}

std::string NormalizeInstrumentKey(std::string x) {
    UpperAsciiInPlace(x);
    for (auto& c : x) if (c == '/') c = '.';
    x.erase(std::remove(x.begin(), x.end(), '.'), x.end());
    return x;
}

bool IsSameInstrumentKey(const std::string& a, const std::string& b) {
    return NormalizeInstrumentKey(a) == NormalizeInstrumentKey(b);
}

double SignedQtyForStrategy(const IbFxOrderIntent& intent, const std::string& strategy) {
    double out = 0.0;
    for (const auto& leg : intent.legs) {
        if (leg.strategy == strategy) out += leg.signedQty;
    }
    if (std::abs(out) > 1e-9) return out;
    if (intent.strategy == strategy && intent.qty > 0.0) {
        if (intent.side == "BUY") return intent.qty;
        if (intent.side == "SELL") return -intent.qty;
    }
    return 0.0;
}

void NormalizeStrategyParams(IbFxStrategyParams& cfg) {
    if (cfg.instrument.empty()) cfg.instrument = "USD.CNH";
    if (cfg.fast < 1) cfg.fast = 1;
    if (cfg.slow < cfg.fast + 1) cfg.slow = cfg.fast + 1;
    if (cfg.signalIntervalSec < 1) cfg.signalIntervalSec = 1;
    if (cfg.signalIntervalMs < 0) cfg.signalIntervalMs = 0;
    if (cfg.maxPosition < 1.0) cfg.maxPosition = 1.0;
    if (cfg.minOrderQty < 1.0) cfg.minOrderQty = 1.0;
    if (cfg.minOrderQty > cfg.maxPosition) cfg.minOrderQty = cfg.maxPosition;
    if (cfg.spreadThresholdBps < 0.0) cfg.spreadThresholdBps = 0.0;
    if (cfg.minVolatilityBps < 0.0) cfg.minVolatilityBps = 0.0;
    if (cfg.holdTimeoutSec < 0) cfg.holdTimeoutSec = 0;
    if (cfg.takeProfitBps < 0.0) cfg.takeProfitBps = 0.0;
    if (cfg.stopLossBps < 0.0) cfg.stopLossBps = 0.0;
    if (cfg.cooldownSec < 0) cfg.cooldownSec = 0;
    if (cfg.entrySpreadMultiplier < 0.0) cfg.entrySpreadMultiplier = 0.0;
    if (cfg.entryBufferBps < 0.0) cfg.entryBufferBps = 0.0;
    if (cfg.signalExitBps < 0.0) cfg.signalExitBps = 0.0;
    if (cfg.signalDecayFraction < 0.0) cfg.signalDecayFraction = 0.0;
    if (cfg.signalDecayFraction > 1.0) cfg.signalDecayFraction = 1.0;
    if (cfg.maxVolatilityBps < 0.0) cfg.maxVolatilityBps = 0.0;
    if (cfg.confirmSamples < 1) cfg.confirmSamples = 1;
    if (cfg.warmupSamples < 5) cfg.warmupSamples = 5;
    if (cfg.driftLookbackSamples < 4) cfg.driftLookbackSamples = 4;
    if (cfg.minEntrySnr < 0.0) cfg.minEntrySnr = 0.0;
    if (cfg.confirmMomentumSnr < 0.0) cfg.confirmMomentumSnr = 0.0;
    if (cfg.slopeDecayRatio < 0.0) cfg.slopeDecayRatio = 0.0;
    if (cfg.rmsGrowthRatio < 0.0) cfg.rmsGrowthRatio = 0.0;
    if (cfg.optimalStopDiscount < 0.0) cfg.optimalStopDiscount = 0.0;
    if (cfg.optimalStopBoundaryScale <= 0.0) cfg.optimalStopBoundaryScale = 1.0;
    if (cfg.entryCostScale < 0.2) cfg.entryCostScale = 0.2;
    if (cfg.entryCostScale > 3.0) cfg.entryCostScale = 3.0;
    if (cfg.entryCostCapBps < 0.0) cfg.entryCostCapBps = 0.0;
    if (cfg.exitFlipConfirmSec < 0) cfg.exitFlipConfirmSec = 0;
    if (cfg.maxLossStreak < 0) cfg.maxLossStreak = 0;
    if (cfg.lossStreakCooldownSec < 0) cfg.lossStreakCooldownSec = 0;
    if (cfg.estRoundTripCostUsd < 0.0) cfg.estRoundTripCostUsd = 0.0;
    if (cfg.minEdgeCostMultiple < 0.0) cfg.minEdgeCostMultiple = 0.0;
    if (cfg.minHoldBeforeFlipSec < 0) cfg.minHoldBeforeFlipSec = 0;
    if (cfg.reverseExitSnrMult < 0.0) cfg.reverseExitSnrMult = 0.0;
    if (cfg.breakevenArmBps < 0.0) cfg.breakevenArmBps = 0.0;
    if (cfg.breakevenFloorBps < 0.0) cfg.breakevenFloorBps = 0.0;
    if (cfg.trailingArmBps < 0.0) cfg.trailingArmBps = 0.0;
    if (cfg.trailingGivebackBps < 0.0) cfg.trailingGivebackBps = 0.0;
    if (cfg.stallStepBps < 0.0) cfg.stallStepBps = 0.0;
    if (cfg.quoteTickSize <= 0.0) cfg.quoteTickSize = 0.00001;
    if (cfg.quoteImproveTicks < 0) cfg.quoteImproveTicks = 0;
}
}

void IbFxMultiStrategyEngine::Configure(const std::vector<IbFxStrategyParams>& strategies) {
    Configure(strategies, Options{});
}

void IbFxMultiStrategyEngine::Configure(const std::vector<IbFxStrategyParams>& strategies, const Options& options) {
    m_options = options;
    m_runtimes.clear();
    m_intents.clear();
    m_decisionAudits.clear();
    m_orderIntentById.clear();
    if (const char* p = std::getenv("HEPTA_IB_PENDING_STALL_SEC")) {
        const int v = std::atoi(p);
        if (v >= 1 && v <= 300) m_pendingStallSec = v;
    }
    if (const char* p = std::getenv("HEPTA_IB_STRATEGY_OWNERSHIP_SEC")) {
        const int v = std::atoi(p);
        if (v >= 1 && v <= 3600) m_strategyOwnershipSec = v;
    }
    for (const auto& s : strategies) {
        Runtime rt;
        rt.cfg = s;
        NormalizeStrategyParams(rt.cfg);
        m_runtimes.push_back(rt);
    }
}

bool IbFxMultiStrategyEngine::Empty() const { return m_runtimes.empty(); }

void IbFxMultiStrategyEngine::OnTick(double price, std::time_t nowTs) {
    if (price <= 0.0) return;
    OnQuote(price, price, nowTs);
}

void IbFxMultiStrategyEngine::OnQuote(double bid, double ask, std::time_t nowTs) {
    if (bid <= 0.0 || ask <= 0.0) return;
    const double mid = (bid + ask) * 0.5;
    if (mid <= 0.0) return;

    PruneStaleReconciledResidualIntents(nowTs);

    const auto nowSteady = std::chrono::steady_clock::now();
    const long long nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    for (auto& rt : m_runtimes) {
        rt.lastBid = bid;
        rt.lastAsk = ask;
        rt.lastMid = mid;

        auto updateLatestVolBps = [&](Runtime& state) {
            const std::size_t lookback = static_cast<std::size_t>(std::max(4, state.cfg.driftLookbackSamples));
            if (state.samples.size() < lookback + 1) return;
            const std::size_t endIdx = state.samples.size() - 1;
            const std::size_t startIdx = endIdx - lookback;
            double meanLogReturn = 0.0;
            std::size_t count = 0;
            for (std::size_t i = startIdx + 1; i <= endIdx; ++i) {
                const double prev = state.samples[i - 1];
                const double curr = state.samples[i];
                if (!(prev > 0.0) || !(curr > 0.0)) return;
                meanLogReturn += std::log(curr / prev);
                ++count;
            }
            if (count < 2) return;
            meanLogReturn /= static_cast<double>(count);
            double varLogReturn = 0.0;
            for (std::size_t i = startIdx + 1; i <= endIdx; ++i) {
                const double prev = state.samples[i - 1];
                const double curr = state.samples[i];
                const double r = std::log(curr / prev);
                const double d = r - meanLogReturn;
                varLogReturn += d * d;
            }
            varLogReturn /= static_cast<double>(std::max<std::size_t>(1, count - 1));
            state.latestVolBps = std::sqrt(std::max(0.0, varLogReturn)) * 10000.0;
        };

        bool shouldSample = false;
        const long long sampleGateMs = (rt.cfg.signalIntervalMs > 0)
            ? static_cast<long long>(rt.cfg.signalIntervalMs)
            : (static_cast<long long>(rt.cfg.signalIntervalSec) * 1000LL);
        if (m_options.useSteadySignalClock) {
            if (!rt.hasLastSampleSteady) shouldSample = true;
            else {
                const auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(nowSteady - rt.lastSampleSteady).count();
                shouldSample = elapsedMs >= sampleGateMs;
            }
        } else {
            shouldSample = (rt.lastSampleTs == 0 || (nowMs - (rt.lastSampleTs * 1000LL)) >= sampleGateMs);
        }

        if (shouldSample) {
            rt.samples.push_back(mid);
            const std::size_t keep = RequiredSampleKeep(rt.cfg);
            while (rt.samples.size() > keep) rt.samples.pop_front();
            rt.lastSampleTs = nowTs;
            rt.lastSampleSteady = nowSteady;
            rt.hasLastSampleSteady = true;
            updateLatestVolBps(rt);
        }

        static const double kMinMidMoveBps = std::max(0.0, []() {
            const char* p = std::getenv("HEPTA_IB_MEANINGFUL_MOVE_BPS");
            return (p && p[0]) ? std::atof(p) : 0.0;
        }());
        static const double kMinSpreadMoveBps = std::max(0.0, []() {
            const char* p = std::getenv("HEPTA_IB_MEANINGFUL_SPREAD_MOVE_BPS");
            return (p && p[0]) ? std::atof(p) : 0.0;
        }());
        const double spreadBpsNow = (mid > 0.0 && ask > bid) ? ((ask - bid) / mid * 10000.0) : 0.0;
        if (kMinMidMoveBps > 0.0 || kMinSpreadMoveBps > 0.0) {
            if (rt.lastEvalMid > 0.0) {
                const double midMoveBps = (rt.lastEvalMid > 0.0 && mid > 0.0)
                    ? (std::abs(std::log(mid / rt.lastEvalMid)) * 10000.0)
                    : 0.0;
                const double spreadMoveBps = std::abs(spreadBpsNow - rt.lastEvalSpreadBps);
                if (midMoveBps < kMinMidMoveBps && spreadMoveBps < kMinSpreadMoveBps) {
                    continue;
                }
            }
        }

        const auto evalStart = std::chrono::steady_clock::now();
        EvaluateRuntime(rt, mid, nowTs);
        rt.lastEvalMid = mid;
        rt.lastEvalSpreadBps = spreadBpsNow;
        const auto evalEnd = std::chrono::steady_clock::now();
        const long long evalUs = std::chrono::duration_cast<std::chrono::microseconds>(evalEnd - evalStart).count();
        rt.lastEvalUs = evalUs;
        rt.evalCount += 1;
        rt.evalTotalUs += evalUs;
        if (evalUs > rt.evalMaxUs) rt.evalMaxUs = evalUs;
        if (rt.hasLastEvalSteady) {
            const long long cycleMs = std::chrono::duration_cast<std::chrono::milliseconds>(evalStart - rt.lastEvalSteady).count();
            rt.lastCycleMs = cycleMs;
            rt.cycleTotalMs += cycleMs;
            if (cycleMs > rt.cycleMaxMs) rt.cycleMaxMs = cycleMs;
        }
        rt.lastEvalSteady = evalStart;
        rt.hasLastEvalSteady = true;
    }
}

std::vector<IbFxOrderIntent> IbFxMultiStrategyEngine::DrainIntents() {
    std::vector<IbFxOrderIntent> raw;
    raw.swap(m_intents);
    if (raw.size() <= 1) {
        return raw;
    }

    auto resizeIntentQty = [](IbFxOrderIntent& intent, double newQty) {
        const double oldQty = intent.qty;
        if (!(oldQty > 1e-9)) {
            intent.qty = 0.0;
            intent.legs.clear();
            intent.riskCost = 0.0;
            intent.signalStrength = 0.0;
            return;
        }
        if (!(newQty > 1e-9)) {
            intent.qty = 0.0;
            intent.legs.clear();
            intent.riskCost = 0.0;
            intent.signalStrength = 0.0;
            return;
        }
        const double ratio = ClampDouble(newQty / oldQty, 0.0, 1.0);
        intent.qty = newQty;
        intent.riskCost *= ratio;
        for (auto& leg : intent.legs) {
            leg.signedQty *= ratio;
        }
    };

    std::unordered_map<std::string, std::vector<std::size_t>> buysByKeyIdx;
    std::unordered_map<std::string, std::vector<std::size_t>> sellsByKeyIdx;
    std::unordered_map<std::string, std::string> displayInstrumentByKey;
    std::vector<std::string> instrumentKeyOrder;
    instrumentKeyOrder.reserve(raw.size());

    for (std::size_t i = 0; i < raw.size(); ++i) {
        if (!(raw[i].qty > 1e-9)) continue;
        const std::string key = NormalizeInstrumentKey(raw[i].instrument);
        if (displayInstrumentByKey.find(key) == displayInstrumentByKey.end()) {
            displayInstrumentByKey[key] = raw[i].instrument;
            instrumentKeyOrder.push_back(key);
        }
        if (raw[i].side == "BUY") buysByKeyIdx[key].push_back(i);
        else if (raw[i].side == "SELL") sellsByKeyIdx[key].push_back(i);
    }

    for (const auto& key : instrumentKeyOrder) {
        auto itBuyIdx = buysByKeyIdx.find(key);
        auto itSellIdx = sellsByKeyIdx.find(key);
        if (itBuyIdx == buysByKeyIdx.end() || itSellIdx == sellsByKeyIdx.end()) continue;

        auto& buyIdx = itBuyIdx->second;
        auto& sellIdx = itSellIdx->second;
        if (buyIdx.empty() || sellIdx.empty()) continue;

        double totalBuyQty = 0.0;
        double totalSellQty = 0.0;
        std::vector<std::string> buyNames;
        std::vector<std::string> sellNames;
        for (std::size_t idx : buyIdx) {
            totalBuyQty += raw[idx].qty;
            buyNames.push_back(raw[idx].strategy);
        }
        for (std::size_t idx : sellIdx) {
            totalSellQty += raw[idx].qty;
            sellNames.push_back(raw[idx].strategy);
        }
        if (!(totalBuyQty > 1e-9 && totalSellQty > 1e-9)) continue;

        std::size_t bi = 0;
        std::size_t si = 0;
        double nettedQty = 0.0;
        while (bi < buyIdx.size() && si < sellIdx.size()) {
            IbFxOrderIntent& buyIntent = raw[buyIdx[bi]];
            IbFxOrderIntent& sellIntent = raw[sellIdx[si]];
            if (!(buyIntent.qty > 1e-9)) { ++bi; continue; }
            if (!(sellIntent.qty > 1e-9)) { ++si; continue; }

            const double matchedQty = std::min(buyIntent.qty, sellIntent.qty);
            resizeIntentQty(buyIntent, buyIntent.qty - matchedQty);
            resizeIntentQty(sellIntent, sellIntent.qty - matchedQty);
            nettedQty += matchedQty;

            if (!(buyIntent.qty > 1e-9)) ++bi;
            if (!(sellIntent.qty > 1e-9)) ++si;
        }

        const double residualBuyQty = std::max(0.0, totalBuyQty - nettedQty);
        const double residualSellQty = std::max(0.0, totalSellQty - nettedQty);
        m_decisionAudits.push_back(
            "netted_opposite_conflict instrument=" + displayInstrumentByKey[key] +
            " buy=" + JoinNames(buyNames) +
            " sell=" + JoinNames(sellNames) +
            " matched_qty=" + std::to_string(nettedQty) +
            " residual_buy_qty=" + std::to_string(residualBuyQty) +
            " residual_sell_qty=" + std::to_string(residualSellQty));
    }

    std::unordered_map<std::string, std::vector<IbFxOrderIntent>> buysByInstrument;
    std::unordered_map<std::string, std::vector<IbFxOrderIntent>> sellsByInstrument;
    std::vector<std::string> instrumentOrder;
    instrumentOrder.reserve(raw.size());

    for (const auto& it : raw) {
        if (!(it.qty > 1e-9)) continue;
        const std::string key = NormalizeInstrumentKey(it.instrument);
        if (buysByInstrument.find(key) == buysByInstrument.end() && sellsByInstrument.find(key) == sellsByInstrument.end()) {
            instrumentOrder.push_back(key);
        }
        if (it.side == "BUY") buysByInstrument[key].push_back(it);
        else if (it.side == "SELL") sellsByInstrument[key].push_back(it);
    }

    std::vector<IbFxOrderIntent> out;
    out.reserve(raw.size());
    for (const auto& key : instrumentOrder) {
        auto itBuy = buysByInstrument.find(key);
        auto itSell = sellsByInstrument.find(key);
        const std::vector<IbFxOrderIntent> emptyVec;
        const std::vector<IbFxOrderIntent>& buys = (itBuy != buysByInstrument.end()) ? itBuy->second : emptyVec;
        const std::vector<IbFxOrderIntent>& sells = (itSell != sellsByInstrument.end()) ? itSell->second : emptyVec;
        const std::string instrument = (displayInstrumentByKey.count(key) > 0) ? displayInstrumentByKey[key] : key;

        const std::vector<IbFxOrderIntent>* srcPtr = (!buys.empty() ? &buys : &sells);
        if (!buys.empty() && !sells.empty()) {
            double buyQty = 0.0;
            double sellQty = 0.0;
            std::vector<std::string> buyNames;
            std::vector<std::string> sellNames;
            for (const auto& it : buys) {
                buyQty += it.qty;
                buyNames.push_back(it.strategy);
            }
            for (const auto& it : sells) {
                sellQty += it.qty;
                sellNames.push_back(it.strategy);
            }
            m_decisionAudits.push_back(
                "residual_opposite_conflict instrument=" + instrument +
                " buy=" + JoinNames(buyNames) +
                " sell=" + JoinNames(sellNames) +
                " buy_qty=" + std::to_string(buyQty) +
                " sell_qty=" + std::to_string(sellQty));
            if (std::abs(buyQty - sellQty) <= 1e-9) {
                continue;
            }
            srcPtr = (buyQty > sellQty) ? &buys : &sells;
        }

        const std::vector<IbFxOrderIntent>& src = *srcPtr;
        if (src.empty()) {
            continue;
        }
        if (src.size() == 1) {
            out.push_back(src.front());
            continue;
        }

        std::unordered_map<std::string, std::vector<IbFxOrderIntent>> groups;
        std::vector<std::string> groupOrder;
        for (const auto& it : src) {
            const std::string key = FormatLmtKey(it);
            if (groups.find(key) == groups.end()) {
                groupOrder.push_back(key);
            }
            groups[key].push_back(it);
        }

        if (groupOrder.size() > 1) {
            std::vector<std::string> groupNames;
            for (const auto& key : groupOrder) {
                groupNames.push_back(key);
            }
            m_decisionAudits.push_back(
                "split_same_direction_execution_groups instrument=" + instrument +
                " side=" + src.front().side +
                " keys=" + JoinNames(groupNames));
        }

        for (const auto& key : groupOrder) {
            const std::vector<IbFxOrderIntent>& group = groups[key];
            if (group.empty()) continue;

            if (group.size() == 1) {
                out.push_back(group.front());
                continue;
            }

            IbFxOrderIntent merged;
            merged.instrument = instrument;
            merged.side = group.front().side;
            merged.orderType = FormatOrderType(group.front().orderType);
            merged.lmtPrice = (merged.orderType == "LMT") ? group.front().lmtPrice : 0.0;
            merged.referencePrice = 0.0;
            merged.qty = 0.0;
            merged.signalStrength = 0.0;
            merged.riskCost = 0.0;

            std::vector<std::string> names;
            double weightedPx = 0.0;
            for (const auto& it : group) {
                if (!FormatLmtKey(it).empty() && !IsSameExecutionIntent(group.front(), it)) {
                    continue;
                }
                names.push_back(it.strategy);
                merged.qty += it.qty;
                weightedPx += it.referencePrice * it.qty;
                merged.signalStrength = std::max(merged.signalStrength, it.signalStrength);
                merged.riskCost += it.riskCost;
                for (const auto& leg : it.legs) merged.legs.push_back(leg);
            }
            merged.referencePrice = (merged.qty > 1e-9) ? (weightedPx / merged.qty) : group.front().referencePrice;
            merged.strategy = "merged:" + JoinNames(names);
            merged.reason = "same_direction_merge";
            out.push_back(merged);

            m_decisionAudits.push_back(
                "merged_same_direction instrument=" + instrument +
                " side=" + merged.side +
                " strategy=" + merged.strategy +
                " qty=" + std::to_string(merged.qty) +
                " order_type=" + merged.orderType +
                " exec_key=" + key);
        }
    }

    return out;
}

std::vector<std::string> IbFxMultiStrategyEngine::DrainDecisionAudits() {
    std::vector<std::string> out;
    out.swap(m_decisionAudits);
    return out;
}

std::vector<IbFxMultiStrategyEngine::StrategyTimingSummary> IbFxMultiStrategyEngine::GetTimingSummaries() const {
    std::vector<StrategyTimingSummary> out;
    out.reserve(m_runtimes.size());
    for (const auto& rt : m_runtimes) {
        StrategyTimingSummary s;
        s.name = rt.cfg.name;
        s.evalCount = rt.evalCount;
        s.avgEvalUs = (rt.evalCount > 0) ? (static_cast<double>(rt.evalTotalUs) / static_cast<double>(rt.evalCount)) : 0.0;
        s.maxEvalUs = rt.evalMaxUs;
        const long long cycleCount = rt.evalCount > 1 ? (rt.evalCount - 1) : 0;
        s.avgCycleMs = (cycleCount > 0) ? (static_cast<double>(rt.cycleTotalMs) / static_cast<double>(cycleCount)) : 0.0;
        s.maxCycleMs = rt.cycleMaxMs;
        s.lastEvalUs = rt.lastEvalUs;
        s.lastCycleMs = rt.lastCycleMs;
        out.push_back(s);
    }
    return out;
}

void IbFxMultiStrategyEngine::OnOrderPlaced(long orderId, const IbFxOrderIntent& intent) {
    const std::time_t nowTs = std::time(nullptr);
    for (const auto& leg : intent.legs) {
        Runtime* rt = FindRuntime(leg.strategy);
        if (!rt) continue;
        rt->ordersSent += 1;
        rt->hasPending = true;
        rt->pendingOrderId = orderId;
        rt->lastTradeTs = nowTs;
        rt->cooldownUntil = nowTs + rt->cfg.cooldownSec;
        const double projectedPos = rt->netPosition + leg.signedQty;
        if (projectedPos > 1e-9) rt->positionIntent = "LONG";
        else if (projectedPos < -1e-9) rt->positionIntent = "SHORT";
        else rt->positionIntent = "FLAT";
    }
    m_orderIntentById[orderId] = intent;
}

void IbFxMultiStrategyEngine::OnOrderRejected(const IbFxOrderIntent& intent) {
    const std::time_t nowTs = std::time(nullptr);
    for (const auto& leg : intent.legs) {
        Runtime* rt = FindRuntime(leg.strategy);
        if (!rt) continue;
        rt->rejects += 1;
        rt->cooldownUntil = std::max<std::time_t>(rt->cooldownUntil, nowTs + rt->cfg.cooldownSec);
        if (rt->hasPending && rt->pendingOrderId > 0) {
            m_decisionAudits.push_back(
                std::string("reject_preserve_pending_binding strategy=") + rt->cfg.name +
                " instrument=" + rt->cfg.instrument +
                " current_order_id=" + std::to_string(rt->pendingOrderId) +
                " rejected_intent_side=" + intent.side +
                " rejected_intent_qty=" + std::to_string(intent.qty));
        }
    }
}

bool IbFxMultiStrategyEngine::HasPendingOrders() const {
    for (const auto& rt : m_runtimes) {
        if (rt.hasPending || rt.pendingOrderId > 0) return true;
    }
    for (const auto& kv : m_orderIntentById) {
        if (!kv.second.reconciledByPosition) return true;
    }
    return false;
}

void IbFxMultiStrategyEngine::OnOrderStatus(long orderId, const std::string& status, double avgPrice, double filledQty, double remainingQty) {
    auto it = m_orderIntentById.find(orderId);
    if (it == m_orderIntentById.end()) return;

    const std::string normalizedStatus = NormalizeStatus(status);
    const std::time_t nowTs = std::time(nullptr);
    auto markRuntimeOrderPending = [&](Runtime* rt) {
        if (!rt) return;
        const bool keepExistingBinding = (rt->hasPending && rt->pendingOrderId > 0 && rt->pendingOrderId != orderId);
        if (keepExistingBinding) {
            m_decisionAudits.push_back(
                std::string("preserve_pending_binding strategy=") + rt->cfg.name +
                " instrument=" + rt->cfg.instrument +
                " current_order_id=" + std::to_string(rt->pendingOrderId) +
                " status_order_id=" + std::to_string(orderId));
            return;
        }
        rt->lastTradeTs = nowTs;
        rt->hasPending = true;
        rt->pendingOrderId = orderId;
    };
    auto bindRuntimeOrderPendingNoClock = [&](Runtime* rt) {
        if (!rt) return;
        const bool keepExistingBinding = (rt->hasPending && rt->pendingOrderId > 0 && rt->pendingOrderId != orderId);
        if (keepExistingBinding) {
            m_decisionAudits.push_back(
                std::string("preserve_pending_binding strategy=") + rt->cfg.name +
                " instrument=" + rt->cfg.instrument +
                " current_order_id=" + std::to_string(rt->pendingOrderId) +
                " status_order_id=" + std::to_string(orderId));
            return;
        }
        rt->hasPending = true;
        rt->pendingOrderId = orderId;
    };
    auto clearRuntimeOrderPending = [&](Runtime* rt) {
        if (!rt) return;
        if (rt->pendingOrderId == orderId) {
            rt->hasPending = false;
            rt->pendingOrderId = -1;
        }
    };
    const double observedAvgFillPrice = (avgPrice > 0.0) ? avgPrice : it->second.lastStatusAvgFillPrice;
    if (avgPrice > 0.0) {
        it->second.lastStatusAvgFillPrice = avgPrice;
    }

    double observedFilledQty = filledQty;
    if (observedFilledQty <= 0.0 && remainingQty >= 0.0 && it->second.qty > 0.0) {
        observedFilledQty = std::max(0.0, it->second.qty - remainingQty);
    }
    if (normalizedStatus == "FILLED" && it->second.qty > 0.0) {
        if (observedFilledQty <= 0.0 || observedFilledQty + 1e-9 < it->second.qty) {
            observedFilledQty = it->second.qty;
        }
    }
    observedFilledQty = std::max(0.0, observedFilledQty);
    if (it->second.qty > 0.0) {
        observedFilledQty = std::min(observedFilledQty, it->second.qty);
    }
    if (observedFilledQty < it->second.lastStatusFilledQty) {
        observedFilledQty = it->second.lastStatusFilledQty;
    }
    it->second.lastStatusFilledQty = observedFilledQty;
    if (remainingQty >= 0.0) {
        it->second.lastStatusRemainingQty = remainingQty;
    }

    const bool isPartialStatus = (normalizedStatus == "PARTIALLYFILLED");
    const bool isFilledStatus = (normalizedStatus == "FILLED");
    const bool isFillProgressStatus = (isPartialStatus || isFilledStatus);
    double newlyFilledQty = 0.0;
    if (isFillProgressStatus) {
        newlyFilledQty = observedFilledQty - it->second.appliedFilledQty;
        if (newlyFilledQty < 1e-9) {
            newlyFilledQty = 0.0;
        }
        if (it->second.qty > 0.0) {
            newlyFilledQty = std::min(newlyFilledQty, std::max(0.0, it->second.qty - it->second.appliedFilledQty));
        }
    }

    if (isFillProgressStatus && newlyFilledQty > 1e-9) {
        if (isPartialStatus) {
            it->second.seenPartiallyFilled = true;
        }
        if (isFilledStatus) {
            it->second.seenFilled = true;
        }

        const double fillScale = (it->second.qty > 1e-9) ? (newlyFilledQty / it->second.qty) : 1.0;
        double deltaFillPrice = (observedAvgFillPrice > 0.0) ? observedAvgFillPrice : it->second.referencePrice;
        if (!it->second.reconciledByPosition && observedAvgFillPrice > 0.0 && newlyFilledQty > 1e-9) {
            const double observedFilledNotional = observedAvgFillPrice * observedFilledQty;
            const double deltaNotional = observedFilledNotional - it->second.appliedFilledNotional;
            if (deltaNotional > 0.0) {
                deltaFillPrice = deltaNotional / newlyFilledQty;
            }
        }
        for (const auto& leg : it->second.legs) {
            Runtime* rt = FindRuntime(leg.strategy);
            if (!rt) continue;

            if (isFilledStatus) {
                rt->fills += 1;
            }

            const bool wasExternalBaseline = rt->externalBaseline;
            const bool wasReconciledByPosition = it->second.reconciledByPosition;

            const double signedQty = leg.signedQty * fillScale;
            if (std::abs(signedQty) <= 1e-9) {
                continue;
            }
            const double prevPos = rt->netPosition;
            const double p = (deltaFillPrice > 0.0 ? deltaFillPrice : it->second.referencePrice);
            const std::string fillReason = it->second.reason;
            const std::string fillOrderType = it->second.orderType.empty() ? "MKT" : it->second.orderType;
            const double prevAvgEntry = rt->avgEntryPrice;
            const std::time_t prevOpenPositionTs = rt->openPositionTs;
            const std::string prevEntryReason = rt->activeEntryReason;
            const std::string prevEntryOrderType = rt->activeEntryOrderType;
            const bool opposite = (prevPos > 0.0 && signedQty < 0.0) || (prevPos < 0.0 && signedQty > 0.0);
            const double prevAbsPos = std::abs(prevPos);
            const double absSignedQty = std::abs(signedQty);
            const bool isFlip = (prevAbsPos > 1e-9 && opposite && absSignedQty > prevAbsPos + 1e-9);
            const bool isPartialCloseOnly = (prevAbsPos > 1e-9 && opposite && absSignedQty < prevAbsPos - 1e-9);
            const double closeQty = opposite ? std::min(prevAbsPos, absSignedQty) : 0.0;
            bool closedTrade = false;
            double closedQty = 0.0;

            if (wasReconciledByPosition) {
                if (!isFilledStatus) {
                    markRuntimeOrderPending(rt);
                    rt->strategyOwnsBaselineUntil = std::max<std::time_t>(rt->strategyOwnsBaselineUntil, nowTs + m_strategyOwnershipSec);
                    continue;
                }

                rt->lastTradeTs = nowTs;
                clearRuntimeOrderPending(rt);
                rt->strategyOwnsBaselineUntil = nowTs + m_strategyOwnershipSec;
                rt->externalBaseline = false;
                const std::string auditBasisSource = rt->basisSource;
                const std::string auditPositionSource = rt->positionSource;
                const std::string auditTimeSource = rt->timeSource;
                const bool haveReconSnapshot = leg.reconciledSnapshotTaken;
                const double reconciledPrevPos = haveReconSnapshot ? leg.reconciledStartPosition : 0.0;
                const double trustedEntryPx =
                    (haveReconSnapshot && leg.reconciledStartAvgEntryPrice > 0.0)
                        ? leg.reconciledStartAvgEntryPrice
                        : rt->avgEntryPrice;
                const std::string prevBasisSource =
                    (haveReconSnapshot && !leg.reconciledStartBasisSource.empty())
                        ? leg.reconciledStartBasisSource
                        : rt->basisSource;
                const std::string prevPositionSource =
                    (haveReconSnapshot && !leg.reconciledStartPositionSource.empty())
                        ? leg.reconciledStartPositionSource
                        : rt->positionSource;
                const std::string prevTimeSource =
                    (haveReconSnapshot && !leg.reconciledStartTimeSource.empty())
                        ? leg.reconciledStartTimeSource
                        : rt->timeSource;
                const std::time_t reconciledOpenPositionTs =
                    (haveReconSnapshot && leg.reconciledStartOpenPositionTs > 0)
                        ? leg.reconciledStartOpenPositionTs
                        : prevOpenPositionTs;
                const std::string reconciledEntryReason =
                    (haveReconSnapshot && !leg.reconciledStartEntryReason.empty())
                        ? leg.reconciledStartEntryReason
                        : prevEntryReason;
                const std::string reconciledEntryOrderType =
                    (haveReconSnapshot && !leg.reconciledStartEntryOrderType.empty())
                        ? leg.reconciledStartEntryOrderType
                        : prevEntryOrderType;
                const std::time_t reconciledFirstExitSignalTs =
                    (haveReconSnapshot && leg.reconciledStartFirstExitSignalTs > 0)
                        ? leg.reconciledStartFirstExitSignalTs
                        : rt->firstExitSignalTs;
                const std::string reconciledFirstExitSignalReason =
                    (haveReconSnapshot && !leg.reconciledStartFirstExitSignalReason.empty())
                        ? leg.reconciledStartFirstExitSignalReason
                        : rt->firstExitSignalReason;

                bool statsFinalized = false;
                std::string finalizeMode = "confirm_only";
                if (std::abs(signedQty) > 1e-9) {
                    const bool nowFlat = (std::abs(rt->netPosition) < 1e-9);
                    const bool signedQtyOpposesPosition =
                        ((reconciledPrevPos > 1e-9 && signedQty < -1e-9) ||
                         (reconciledPrevPos < -1e-9 && signedQty > 1e-9));
                    const bool hasTrustedBasis = (trustedEntryPx > 0.0 && prevBasisSource == "strategy_fill");
                    const double closeQtyFromIntent =
                        (std::abs(reconciledPrevPos) > 1e-9)
                            ? std::min(std::abs(reconciledPrevPos), std::abs(signedQty))
                            : std::abs(signedQty);

                    if (nowFlat && signedQtyOpposesPosition && hasTrustedBasis && closeQtyFromIntent > 1e-9) {
                        const double legPnl = (reconciledPrevPos > 0.0)
                            ? ((p - trustedEntryPx) * closeQtyFromIntent)
                            : ((trustedEntryPx - p) * closeQtyFromIntent);
                        rt->realizedPnl += legPnl;
                        rt->cycleRealizedPnl += legPnl;

                        long long holdSec = 0;
                        if (reconciledOpenPositionTs > 0 && nowTs > reconciledOpenPositionTs) {
                            holdSec = static_cast<long long>(nowTs - reconciledOpenPositionTs);
                            rt->totalHoldSec += holdSec;
                        }
                        const long long timeToExitSignalSec =
                            (reconciledFirstExitSignalTs > 0 && reconciledOpenPositionTs > 0 && reconciledFirstExitSignalTs >= reconciledOpenPositionTs)
                                ? static_cast<long long>(reconciledFirstExitSignalTs - reconciledOpenPositionTs)
                                : holdSec;

                        rt->closedTrades += 1;
                        if (rt->cycleRealizedPnl > 0.0) {
                            rt->winningTrades += 1;
                            rt->consecutiveLosses = 0;
                        } else if (rt->cycleRealizedPnl < 0.0) {
                            rt->consecutiveLosses += 1;
                            if (rt->cfg.maxLossStreak > 0 && rt->cfg.lossStreakCooldownSec > 0 && rt->consecutiveLosses >= rt->cfg.maxLossStreak) {
                                rt->lossCooldownUntil = std::max<std::time_t>(rt->lossCooldownUntil, nowTs + rt->cfg.lossStreakCooldownSec);
                            }
                        } else {
                            rt->consecutiveLosses = 0;
                        }

                        m_decisionAudits.push_back(
                            std::string("trade_blotter_late_reconcile strategy=") + rt->cfg.name +
                            " instrument=" + rt->cfg.instrument +
                            " entry_reason=" + (reconciledEntryReason.empty() ? std::string("unknown") : reconciledEntryReason) +
                            " entry_type=" + (reconciledEntryOrderType.empty() ? std::string("unknown") : reconciledEntryOrderType) +
                            " exit_signal_reason=" + (reconciledFirstExitSignalReason.empty() ? std::string("unknown") : reconciledFirstExitSignalReason) +
                            " exit_fill_reason=" + (fillReason.empty() ? std::string("unknown") : fillReason) +
                            " exit_type=" + fillOrderType +
                            " basis_src=" + prevBasisSource +
                            " pos_src=" + prevPositionSource +
                            " time_src=" + prevTimeSource +
                            " qty=" + std::to_string(closeQtyFromIntent) +
                            " entry_px=" + std::to_string(trustedEntryPx) +
                            " exit_px=" + std::to_string(p) +
                            " time_to_exit_signal_sec=" + std::to_string(timeToExitSignalSec) +
                            " time_to_flat_fill_sec=" + std::to_string(holdSec) +
                            " pnl=" + std::to_string(rt->cycleRealizedPnl));

                        rt->cycleRealizedPnl = 0.0;
                        rt->avgEntryPrice = 0.0;
                        rt->entryTs = 0;
                        rt->openPositionTs = 0;
                        rt->peakFavorablePnlBps = 0.0;
                        rt->basisSource = "none";
                        rt->positionSource = "flat";
                        rt->timeSource = "none";
                        rt->activeEntryReason.clear();
                        rt->activeEntryOrderType.clear();
                        rt->activeExitReason = fillReason;
                        rt->activeExitOrderType = fillOrderType;
                        rt->firstExitSignalTs = 0;
                        rt->timeoutExitSignalTs = 0;
                        rt->firstExitSignalReason.clear();
                        rt->pendingExitSignalTs = 0;
                        rt->pendingExitReason.clear();
                        rt->positionIntent = "FLAT";
                        statsFinalized = true;
                        finalizeMode = "flat_close";
                    }
                }

                m_decisionAudits.push_back(
                    std::string("late_broker_fill_after_reconcile strategy=") + rt->cfg.name +
                    " instrument=" + rt->cfg.instrument +
                    " order_id=" + std::to_string(orderId) +
                    " avg_px=" + std::to_string(p) +
                    " basis_src=" + auditBasisSource +
                    " pos_src=" + auditPositionSource +
                    " time_src=" + auditTimeSource +
                    " stats_finalized=" + (statsFinalized ? std::string("1") : std::string("0")) +
                    " mode=" + finalizeMode);
                continue;
            }

            if (std::abs(prevPos) > 1e-9 && opposite && closeQty > 0.0 && rt->avgEntryPrice > 0.0 && rt->basisSource == "strategy_fill") {
                const double legPnl = (prevPos > 0.0)
                    ? ((p - rt->avgEntryPrice) * closeQty)
                    : ((rt->avgEntryPrice - p) * closeQty);
                rt->realizedPnl += legPnl;
                rt->cycleRealizedPnl += legPnl;
                closedQty = closeQty;
            }

            const auto emitClosedTrade = [&](const double closedQtyForLog) {
                const std::string prevBasisSource = rt->basisSource;
                const std::string prevPositionSource = rt->positionSource;
                const std::string prevTimeSource = rt->timeSource;
                const bool trustedClosedTrade = (prevBasisSource == "strategy_fill" && prevAvgEntry > 0.0);
                rt->netPosition = 0.0;
                rt->avgEntryPrice = 0.0;
                rt->entryTs = 0;
                rt->peakFavorablePnlBps = 0.0;
                rt->basisSource = "none";
                rt->positionSource = "flat";
                rt->timeSource = "none";
                long long holdSec = 0;
                if (rt->openPositionTs > 0 && nowTs > rt->openPositionTs) {
                    holdSec = static_cast<long long>(nowTs - rt->openPositionTs);
                    if (trustedClosedTrade) {
                        rt->totalHoldSec += holdSec;
                    }
                }
                const long long timeToExitSignalSec = (rt->firstExitSignalTs > 0 && prevOpenPositionTs > 0 && rt->firstExitSignalTs >= prevOpenPositionTs)
                    ? static_cast<long long>(rt->firstExitSignalTs - prevOpenPositionTs)
                    : holdSec;
                rt->openPositionTs = 0;
                if (trustedClosedTrade) {
                    rt->closedTrades += 1;
                    if (rt->cycleRealizedPnl > 0.0) {
                        rt->winningTrades += 1;
                        rt->consecutiveLosses = 0;
                    } else if (rt->cycleRealizedPnl < 0.0) {
                        rt->consecutiveLosses += 1;
                        if (rt->cfg.maxLossStreak > 0 && rt->cfg.lossStreakCooldownSec > 0 && rt->consecutiveLosses >= rt->cfg.maxLossStreak) {
                            rt->lossCooldownUntil = std::max<std::time_t>(rt->lossCooldownUntil, nowTs + rt->cfg.lossStreakCooldownSec);
                            m_decisionAudits.push_back(
                                std::string("loss_streak_guard strategy=") + rt->cfg.name +
                                " instrument=" + rt->cfg.instrument +
                                " losses=" + std::to_string(rt->consecutiveLosses) +
                                " cooldown_sec=" + std::to_string(rt->cfg.lossStreakCooldownSec));
                        }
                    } else {
                        rt->consecutiveLosses = 0;
                    }
                    m_decisionAudits.push_back(
                        std::string("trade_blotter strategy=") + rt->cfg.name +
                        " instrument=" + rt->cfg.instrument +
                        " entry_reason=" + (prevEntryReason.empty() ? std::string("unknown") : prevEntryReason) +
                        " entry_type=" + (prevEntryOrderType.empty() ? std::string("unknown") : prevEntryOrderType) +
                        " exit_signal_reason=" + (rt->firstExitSignalReason.empty() ? std::string("unknown") : rt->firstExitSignalReason) +
                        " exit_fill_reason=" + (fillReason.empty() ? std::string("unknown") : fillReason) +
                        " exit_type=" + fillOrderType +
                        " basis_src=" + prevBasisSource +
                        " pos_src=" + prevPositionSource +
                        " time_src=" + prevTimeSource +
                        " qty=" + std::to_string(closedQtyForLog > 0.0 ? closedQtyForLog : std::abs(prevPos)) +
                        " entry_px=" + std::to_string(prevAvgEntry) +
                        " exit_px=" + std::to_string(p) +
                        " time_to_exit_signal_sec=" + std::to_string(timeToExitSignalSec) +
                        " time_to_flat_fill_sec=" + std::to_string(holdSec) +
                        " pnl=" + std::to_string(rt->cycleRealizedPnl));
                } else {
                    m_decisionAudits.push_back(
                        std::string("trade_blotter_untrusted_close strategy=") + rt->cfg.name +
                        " instrument=" + rt->cfg.instrument +
                        " entry_reason=" + (prevEntryReason.empty() ? std::string("unknown") : prevEntryReason) +
                        " entry_type=" + (prevEntryOrderType.empty() ? std::string("unknown") : prevEntryOrderType) +
                        " exit_signal_reason=" + (rt->firstExitSignalReason.empty() ? std::string("unknown") : rt->firstExitSignalReason) +
                        " exit_fill_reason=" + (fillReason.empty() ? std::string("unknown") : fillReason) +
                        " exit_type=" + fillOrderType +
                        " basis_src=" + prevBasisSource +
                        " pos_src=" + prevPositionSource +
                        " time_src=" + prevTimeSource +
                        " qty=" + std::to_string(closedQtyForLog > 0.0 ? closedQtyForLog : std::abs(prevPos)) +
                        " entry_px=" + std::to_string(prevAvgEntry) +
                        " exit_px=" + std::to_string(p) +
                        " time_to_exit_signal_sec=" + std::to_string(timeToExitSignalSec) +
                        " time_to_flat_fill_sec=" + std::to_string(holdSec));
                }
                rt->cycleRealizedPnl = 0.0;
                rt->activeEntryReason.clear();
                rt->activeEntryOrderType.clear();
                rt->activeExitReason = fillReason;
                rt->activeExitOrderType = fillOrderType;
                rt->firstExitSignalTs = 0;
                rt->timeoutExitSignalTs = 0;
                rt->firstExitSignalReason.clear();
                rt->pendingExitSignalTs = 0;
                rt->pendingExitReason.clear();
                closedTrade = true;
            };

            const double newPos = prevPos + signedQty;
            const bool isClose = (std::abs(newPos) < 1e-9);
            if (isClose) {
                emitClosedTrade(closedQty > 0.0 ? closedQty : prevAbsPos);
            } else if (std::abs(prevPos) < 1e-9) {
                rt->netPosition = newPos;
                rt->avgEntryPrice = p;
                rt->entryTs = nowTs;
                rt->peakFavorablePnlBps = 0.0;
                rt->openPositionTs = nowTs;
                rt->cycleRealizedPnl = 0.0;
                rt->basisSource = "strategy_fill";
                rt->positionSource = "strategy_fill";
                rt->timeSource = "strategy_fill";
                rt->activeEntryReason = fillReason;
                rt->activeEntryOrderType = fillOrderType;
                rt->firstExitSignalTs = 0;
                rt->timeoutExitSignalTs = 0;
                rt->firstExitSignalReason.clear();
                rt->pendingExitSignalTs = 0;
                rt->pendingExitReason.clear();
            } else if ((prevPos > 0.0 && newPos > 0.0 && signedQty > 0.0) || (prevPos < 0.0 && newPos < 0.0 && signedQty < 0.0)) {
                const double addAbs = std::abs(signedQty);
                rt->netPosition = newPos;
                rt->positionSource = "strategy_fill";
                if (prevAbsPos + addAbs > 0.0) {
                    rt->avgEntryPrice = ((rt->avgEntryPrice * prevAbsPos) + (p * addAbs)) / (prevAbsPos + addAbs);
                    rt->basisSource = "strategy_fill";
                }
            } else if (isPartialCloseOnly) {
                rt->netPosition = newPos;
                rt->positionSource = "strategy_fill";
            } else if (isFlip) {
                emitClosedTrade(closeQty);
                rt->netPosition = newPos;
                rt->avgEntryPrice = p;
                rt->entryTs = nowTs;
                rt->peakFavorablePnlBps = 0.0;
                rt->openPositionTs = nowTs;
                rt->cycleRealizedPnl = 0.0;
                rt->basisSource = "strategy_fill";
                rt->positionSource = "strategy_fill";
                rt->timeSource = "strategy_fill";
                rt->activeEntryReason = fillReason;
                rt->activeEntryOrderType = fillOrderType;
                rt->firstExitSignalTs = 0;
                rt->timeoutExitSignalTs = 0;
                rt->firstExitSignalReason.clear();
                rt->pendingExitSignalTs = 0;
                rt->pendingExitReason.clear();
            } else {
                rt->netPosition = newPos;
                rt->avgEntryPrice = p;
                rt->entryTs = nowTs;
                rt->peakFavorablePnlBps = 0.0;
                rt->openPositionTs = nowTs;
                rt->cycleRealizedPnl = 0.0;
                rt->basisSource = "strategy_fill";
                rt->positionSource = "strategy_fill";
                rt->timeSource = "strategy_fill";
                rt->activeEntryReason = fillReason;
                rt->activeEntryOrderType = fillOrderType;
                rt->firstExitSignalTs = 0;
                rt->timeoutExitSignalTs = 0;
                rt->firstExitSignalReason.clear();
                rt->pendingExitSignalTs = 0;
                rt->pendingExitReason.clear();
            }

            if (rt->netPosition > 1e-9) rt->positionIntent = "LONG";
            else if (rt->netPosition < -1e-9) rt->positionIntent = "SHORT";
            else rt->positionIntent = "FLAT";
            rt->lastTradeTs = nowTs;

            rt->strategyOwnsBaselineUntil = nowTs + m_strategyOwnershipSec;
            rt->externalBaseline = false;
            if (wasExternalBaseline) {
                m_decisionAudits.push_back(std::string("baseline_source_switch instrument=") + rt->cfg.instrument + " source=strategy_fill");
            }
            if (closedTrade && rt->strategyOwnsBaselineUntil > nowTs) {
                m_decisionAudits.push_back(std::string("strategy_owns_baseline instrument=") + rt->cfg.instrument + " until=" + std::to_string(static_cast<long long>(rt->strategyOwnsBaselineUntil)));
            }
        }

        if (!it->second.reconciledByPosition) {
            it->second.appliedFilledQty += newlyFilledQty;
            if (it->second.qty > 0.0) {
                it->second.appliedFilledQty = std::min(it->second.appliedFilledQty, it->second.qty);
            }
            if (observedAvgFillPrice > 0.0 && observedFilledQty > 0.0) {
                it->second.appliedFilledNotional = observedAvgFillPrice * it->second.appliedFilledQty;
            } else if (deltaFillPrice > 0.0) {
                it->second.appliedFilledNotional += deltaFillPrice * newlyFilledQty;
            }
        }
    } else if (isPartialStatus) {
        it->second.seenPartiallyFilled = true;
    } else if (isFilledStatus) {
        it->second.seenFilled = true;
    }

    if (isPartialStatus) {
        if (newlyFilledQty > 1e-9 && !it->second.reconciledByPosition) {
            m_decisionAudits.push_back(
                std::string("partial_fill_accounted order_id=") + std::to_string(orderId) +
                " instrument=" + it->second.instrument +
                " delta_qty=" + std::to_string(newlyFilledQty) +
                " cum_qty=" + std::to_string(observedFilledQty));
        }
        for (const auto& leg : it->second.legs) {
            Runtime* rt = FindRuntime(leg.strategy);
            if (!rt) continue;
            if (newlyFilledQty > 1e-9) {
                markRuntimeOrderPending(rt);
                rt->strategyOwnsBaselineUntil = std::max<std::time_t>(rt->strategyOwnsBaselineUntil, nowTs + m_strategyOwnershipSec);
            } else {
                bindRuntimeOrderPendingNoClock(rt);
            }
        }
        return;
    }

    if (normalizedStatus == "CANCELLED" || normalizedStatus == "APICANCELLED" || normalizedStatus == "INACTIVE" || normalizedStatus == "REJECTED") {
        const bool isRejectedStatus = (normalizedStatus == "REJECTED");
        const bool hadPartialWithoutFullFill =
            (it->second.seenPartiallyFilled && !it->second.seenFilled && it->second.lastStatusFilledQty <= 1e-9);
        const bool hadExecutionProgress =
            it->second.reconciledByPosition ||
            it->second.appliedFilledQty > 1e-9 ||
            it->second.lastStatusFilledQty > 1e-9;
        for (const auto& leg : it->second.legs) {
            Runtime* rt = FindRuntime(leg.strategy);
            if (!rt) continue;
            if (isRejectedStatus) {
                rt->rejects += 1;
                rt->cooldownUntil = std::max<std::time_t>(rt->cooldownUntil, nowTs + rt->cfg.cooldownSec);
            } else {
                rt->cancels += 1;
            }
            if (hadExecutionProgress) {
                rt->lastTradeTs = nowTs;
            } else {
                rt->positionIntent = (rt->netPosition > 1e-9)
                    ? "LONG"
                    : ((rt->netPosition < -1e-9) ? "SHORT" : "FLAT");
            }
            clearRuntimeOrderPending(rt);
            if (hadPartialWithoutFullFill) {
                const long long extraCooldownSec = std::max(1LL, static_cast<long long>(m_pendingStallSec));
                rt->cooldownUntil = std::max<time_t>(rt->cooldownUntil, nowTs + extraCooldownSec);
                rt->strategyOwnsBaselineUntil = 0;
                m_decisionAudits.push_back(
                    std::string("partial_fill_ambiguous_cancel strategy=") + rt->cfg.name +
                    " instrument=" + rt->cfg.instrument +
                    " order_id=" + std::to_string(orderId) +
                    " cooldown_sec=" + std::to_string(extraCooldownSec));
            }
        }
    }


    if (IsTerminalStatus(normalizedStatus)) {
        for (const auto& leg : it->second.legs) {
            Runtime* rt = FindRuntime(leg.strategy);
            if (!rt) continue;
            clearRuntimeOrderPending(rt);
        }
        m_orderIntentById.erase(it);
    }
}

bool IbFxMultiStrategyEngine::GetOrderIntent(long orderId, IbFxOrderIntent& out) const {
    auto it = m_orderIntentById.find(orderId);
    if (it == m_orderIntentById.end()) return false;
    out = it->second;
    return true;
}

std::vector<IbFxMultiStrategyEngine::StrategySummary> IbFxMultiStrategyEngine::GetStrategySummaries(std::time_t nowTs) const {
    std::vector<StrategySummary> out;
    out.reserve(m_runtimes.size());
    for (const auto& rt : m_runtimes) {
        StrategySummary s;
        s.name = rt.cfg.name;
        s.instrument = rt.cfg.instrument;
        s.netPosition = rt.netPosition;
        s.avgEntryPrice = rt.avgEntryPrice;
        s.lastPrice = rt.lastMid;
        s.realizedPnl = rt.realizedPnl;
        if (std::abs(rt.netPosition) > 1e-9 && rt.avgEntryPrice > 0.0 && rt.lastMid > 0.0) {
            s.unrealizedPnl = (rt.netPosition > 0.0)
                ? ((rt.lastMid - rt.avgEntryPrice) * std::abs(rt.netPosition))
                : ((rt.avgEntryPrice - rt.lastMid) * std::abs(rt.netPosition));
        }
        s.totalPnl = s.realizedPnl + s.unrealizedPnl;
        const bool usdBaseFx = (rt.cfg.instrument.rfind("USD.", 0) == 0);
        const bool usdQuoteFx =
            (rt.cfg.instrument.size() >= 4 &&
             rt.cfg.instrument.substr(rt.cfg.instrument.size() - 4) == ".USD");
        const double conversionPx = (rt.lastMid > 0.0) ? rt.lastMid : rt.avgEntryPrice;
        const double closedCostUsd = static_cast<double>(rt.closedTrades) * std::max(0.0, rt.cfg.estRoundTripCostUsd);
        const double openCostUsd = (std::abs(rt.netPosition) > 1e-9) ? (0.5 * std::max(0.0, rt.cfg.estRoundTripCostUsd)) : 0.0;
        if (usdQuoteFx) {
            s.realizedPnlUsd = s.realizedPnl - closedCostUsd;
            s.unrealizedPnlUsd = s.unrealizedPnl - openCostUsd;
            s.estimatedCostsUsd = closedCostUsd + openCostUsd;
            s.totalPnlUsd = s.realizedPnlUsd + s.unrealizedPnlUsd;
        } else if (usdBaseFx && conversionPx > 0.0) {
            s.realizedPnlUsd = (s.realizedPnl / conversionPx) - closedCostUsd;
            s.unrealizedPnlUsd = (s.unrealizedPnl / conversionPx) - openCostUsd;
            s.estimatedCostsUsd = closedCostUsd + openCostUsd;
            s.totalPnlUsd = s.realizedPnlUsd + s.unrealizedPnlUsd;
        }
        s.ordersSent = rt.ordersSent;
        s.fills = rt.fills;
        s.rejects = rt.rejects;
        s.cancels = rt.cancels;
        s.fillRatePct = (rt.ordersSent > 0) ? (100.0 * static_cast<double>(rt.fills) / static_cast<double>(rt.ordersSent)) : 0.0;
        s.closedTrades = rt.closedTrades;
        s.winningTrades = rt.winningTrades;
        s.winRatePct = (rt.closedTrades > 0) ? (100.0 * static_cast<double>(rt.winningTrades) / static_cast<double>(rt.closedTrades)) : 0.0;

        long long holdSec = rt.totalHoldSec;
        long holdCount = rt.closedTrades;
        if (std::abs(rt.netPosition) > 1e-9 && rt.openPositionTs > 0 && nowTs > rt.openPositionTs) {
            holdSec += static_cast<long long>(nowTs - rt.openPositionTs);
            holdCount += 1;
        }
        s.avgHoldSec = (holdCount > 0) ? (static_cast<double>(holdSec) / static_cast<double>(holdCount)) : 0.0;
        s.externalBaseline = (rt.externalBaseline && std::abs(rt.netPosition) > 1e-9);
        s.basisSource = rt.basisSource;
        s.positionSource = rt.positionSource;
        s.timeSource = rt.timeSource;
        s.unrealizedBasisTrusted = (rt.basisSource == "strategy_fill");

        out.push_back(s);
    }
    return out;
}

IbFxMultiStrategyEngine::Runtime* IbFxMultiStrategyEngine::FindRuntime(const std::string& name) {
    for (auto& rt : m_runtimes) if (rt.cfg.name == name) return &rt;
    return nullptr;
}

const IbFxMultiStrategyEngine::Runtime* IbFxMultiStrategyEngine::FindRuntime(const std::string& name) const {
    for (const auto& rt : m_runtimes) if (rt.cfg.name == name) return &rt;
    return nullptr;
}

bool IbFxMultiStrategyEngine::IsRuntimeBoundToOrder(long orderId) const {
    if (orderId <= 0) return false;
    for (const auto& rt : m_runtimes) {
        if (rt.pendingOrderId == orderId) return true;
    }
    return false;
}

bool IbFxMultiStrategyEngine::IsStaleReconciledResidualIntent(const IbFxOrderIntent& intent, std::time_t nowTs) const {
    if (!intent.reconciledByPosition) return false;
    if (intent.reconciledAtTs <= 0 || nowTs <= intent.reconciledAtTs) return false;
    const long long ageSec = static_cast<long long>(nowTs - intent.reconciledAtTs);
    const long long graceSec = std::max<long long>(120, std::max<long long>(m_strategyOwnershipSec * 2, m_pendingStallSec * 4));
    return ageSec >= graceSec;
}

void IbFxMultiStrategyEngine::PruneStaleReconciledResidualIntents(std::time_t nowTs) {
    for (auto it = m_orderIntentById.begin(); it != m_orderIntentById.end(); ) {
        if (IsRuntimeBoundToOrder(it->first) || !IsStaleReconciledResidualIntent(it->second, nowTs)) {
            ++it;
            continue;
        }
        const long long ageSec = static_cast<long long>(nowTs - it->second.reconciledAtTs);
        m_decisionAudits.push_back(
            std::string("prune_stale_reconciled_intent order_id=") + std::to_string(it->first) +
            " instrument=" + it->second.instrument +
            " strategy=" + it->second.strategy +
            " age_sec=" + std::to_string(ageSec));
        it = m_orderIntentById.erase(it);
    }
}

double IbFxMultiStrategyEngine::ComputeSma(const std::deque<double>& samples, int window) {
    if (window <= 0 || samples.size() < static_cast<std::size_t>(window)) return 0.0;
    double sum = 0.0;
    for (int i = 0; i < window; ++i) {
        sum += samples[samples.size() - 1 - i];
    }
    return sum / static_cast<double>(window);
}

bool IbFxMultiStrategyEngine::HasManagedPositionBasis(const Runtime& rt) const {
    if (std::abs(rt.netPosition) <= 1e-9 || rt.avgEntryPrice <= 0.0) return false;
    if (rt.externalBaseline) return false;
    return rt.positionSource == "strategy_fill" ||
           rt.positionSource == "strategy_reconciled" ||
           rt.positionSource == "strategy_sync";
}

bool IbFxMultiStrategyEngine::HasManagedPositionClock(const Runtime& rt) const {
    if (!HasManagedPositionBasis(rt)) return false;
    return rt.timeSource == "strategy_fill" ||
           rt.timeSource == "reconcile" ||
           rt.timeSource == "strategy_sync";
}

bool IbFxMultiStrategyEngine::EstimateSlopeBps(const Runtime& rt, std::size_t endOffset, int window,
                                               double& outSlopeBps, double& outNoiseBps, double& outRmsStepBps) const {
    if (window < 3) return false;
    const std::size_t n = static_cast<std::size_t>(window);
    if (rt.samples.size() <= endOffset) return false;
    const std::size_t endIdx = rt.samples.size() - 1 - endOffset;
    if (endIdx + 1 < n) return false;
    const std::size_t start = endIdx - n + 1;

    const double meanX = (static_cast<double>(n) - 1.0) * 0.5;
    double sumY = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        sumY += rt.samples[start + i];
    }
    const double xMean = meanX;
    const double yMean = sumY / static_cast<double>(n);

    double numerator = 0.0;
    double denominator = 0.0;
    double sse = 0.0;
    for (std::size_t i = 0; i < n; ++i) {
        const double x = static_cast<double>(i) - xMean;
        const double y = rt.samples[start + i] - yMean;
        numerator += x * y;
        denominator += x * x;
    }
    if (std::fabs(denominator) < 1e-15) return false;
    const double slopePx = numerator / denominator;

    for (std::size_t i = 0; i < n; ++i) {
        const double x = static_cast<double>(i);
        const double predicted = yMean + slopePx * (x - xMean);
        const double err = rt.samples[start + i] - predicted;
        sse += err * err;
    }

    const double latest = rt.samples[endIdx];
    if (latest <= 0.0) return false;
    outSlopeBps = slopePx / latest * 10000.0;
    outRmsStepBps = std::sqrt(sse / static_cast<double>(n)) / latest * 10000.0;
    outNoiseBps = outRmsStepBps / std::max(1.0, std::sqrt(static_cast<double>(n)));
    return true;
}

void IbFxMultiStrategyEngine::SeedExternalBasisIfNeeded(Runtime& rt, double price, std::time_t nowTs) {
    if (std::abs(rt.netPosition) <= 1e-9 || rt.avgEntryPrice > 0.0 || price <= 0.0) return;
    // Only seed basis from market when the position is explicitly external.
    // If the strategy owns the baseline, missing basis should be fixed by order/fill
    // reconciliation rather than silently converting strategy inventory into
    // market-seeded external inventory.
    if (!rt.externalBaseline) return;
    rt.avgEntryPrice = price;
    rt.basisSource = "external_market";
    if (rt.entryTs <= 0) rt.entryTs = nowTs;
    if (rt.openPositionTs <= 0) rt.openPositionTs = nowTs;
    rt.timeSource = "external_seed";
    rt.peakFavorablePnlBps = 0.0;
    m_decisionAudits.push_back(std::string("sync_external_basis_from_market instrument=") + rt.cfg.instrument +
        " px=" + std::to_string(price));
}

bool IbFxMultiStrategyEngine::EstimateOptimalStoppingSignal(
    const Runtime& rt, std::size_t endOffset,
    double spreadBps, double entryGateBps,
    OptimalStoppingSignal& out) const {

    out = OptimalStoppingSignal{};
    const std::size_t lookback = static_cast<std::size_t>(std::max(4, rt.cfg.driftLookbackSamples));
    if (rt.samples.size() <= endOffset) return false;
    const std::size_t endIdx = rt.samples.size() - 1 - endOffset;
    if (endIdx + 1 < lookback + 1) return false;

    const std::size_t pxStart = endIdx - lookback;
    const std::size_t pxEnd = endIdx;
    std::vector<double> logReturns;
    logReturns.reserve(lookback);
    double logRef = 0.0;
    for (std::size_t i = pxStart; i <= pxEnd; ++i) {
        const double px = rt.samples[i];
        if (!(px > 0.0)) return false;
        logRef += std::log(px);
    }
    logRef /= static_cast<double>(pxEnd - pxStart + 1);

    for (std::size_t i = pxStart + 1; i <= pxEnd; ++i) {
        const double prev = rt.samples[i - 1];
        const double curr = rt.samples[i];
        if (!(prev > 0.0) || !(curr > 0.0)) return false;
        logReturns.push_back(std::log(curr / prev));
    }
    if (logReturns.size() < 2 || !std::isfinite(logRef)) return false;

    double meanLogReturn = 0.0;
    for (double v : logReturns) meanLogReturn += v;
    meanLogReturn /= static_cast<double>(logReturns.size());

    double varLogReturn = 0.0;
    for (double v : logReturns) {
        const double d = v - meanLogReturn;
        varLogReturn += d * d;
    }
    varLogReturn /= static_cast<double>(std::max<std::size_t>(1, logReturns.size() - 1));
    const double sigma = std::sqrt(std::max(0.0, varLogReturn));
    const double sigma2 = sigma * sigma;
    if (!(sigma2 > 1e-12)) return false;

    const double spot = rt.samples[endIdx];
    if (!(spot > 0.0)) return false;
    const double x = std::log(spot) - logRef;
    if (!std::isfinite(x)) return false;

    const double intervalSec = StrategySignalIntervalSeconds(rt.cfg);
    constexpr double kOptimalStopHorizonReferenceSec = 4.0;
    const double confirmHorizonSec = std::max(1.0, static_cast<double>(std::max(1, rt.cfg.confirmSamples))) * intervalSec;
    const double horizonSec = std::max(confirmHorizonSec, kOptimalStopHorizonReferenceSec);
    const double horizonSteps = std::max(1.0, horizonSec / std::max(0.001, intervalSec));
    const double horizonDriftBpsBase = meanLogReturn * horizonSteps * 10000.0;
    const double horizonVolBps = sigma * std::sqrt(horizonSteps) * 10000.0;
    const double continuationCostBps = std::max(0.0, std::max(entryGateBps, spreadBps));
    const double k = continuationCostBps / 10000.0;

    // Unified log-process perpetual stopping model.
    // X_t = log S_t follows dX_t = m dt + sigma dW_t, with m estimated by the
    // mean log return. Entry triggers when the log displacement from a rolling
    // log reference crosses the perpetual free boundary implied by (m, sigma, rho, k).
    const double rho = std::max(1e-6, rt.cfg.optimalStopDiscount / 10000.0);
    const double a = 0.5 * sigma2;
    const double b = meanLogReturn;
    const double c = -rho;
    const double disc = b * b - 4.0 * a * c;
    if (!(disc > 0.0)) return false;

    const double sqrtDisc = std::sqrt(disc);
    const double betaPlus = (-b + sqrtDisc) / (2.0 * a);
    const double betaMinus = (-b - sqrtDisc) / (2.0 * a);
    if (!(betaPlus > 1.0) || !(betaMinus < 0.0)) return false;

    const double boundaryScale = std::max(0.1, rt.cfg.optimalStopBoundaryScale);
    const double baseXLongStar = std::max(0.0, std::log(betaPlus / (betaPlus - 1.0)) + k);
    const double baseXShortStar = std::min(0.0, -std::log(betaMinus / (betaMinus - 1.0)) - k);
    const double xLongStar = std::max(0.0, baseXLongStar * boundaryScale);
    const double xShortStar = std::min(0.0, baseXShortStar * boundaryScale);
    if (!std::isfinite(xLongStar) || !std::isfinite(xShortStar)) return false;

    const double longGapBps = (x - xLongStar) * 10000.0;
    const double shortGapBps = (xShortStar - x) * 10000.0;
    const bool longSignal = (meanLogReturn > 0.0 && x >= xLongStar);
    const bool shortSignal = (meanLogReturn < 0.0 && x <= xShortStar);

    double horizonDriftBps = horizonDriftBpsBase;
    if (longSignal) {
        horizonDriftBps = std::max(horizonDriftBps, std::max(0.0, longGapBps));
    } else if (shortSignal) {
        horizonDriftBps = std::min(horizonDriftBps, -std::max(0.0, shortGapBps));
    }

    const double confidenceBase = (horizonVolBps > 1e-9)
        ? (std::fabs(horizonDriftBps) / horizonVolBps)
        : ((std::fabs(horizonDriftBps) > 0.0) ? 999.0 : 0.0);
    const double triggerGapBps = longSignal ? std::max(0.0, longGapBps) : (shortSignal ? std::max(0.0, shortGapBps) : 0.0);
    const double triggerConfidence = (horizonVolBps > 1e-9)
        ? (triggerGapBps / horizonVolBps)
        : ((triggerGapBps > 0.0) ? 999.0 : 0.0);
    const double confidence = std::max(confidenceBase, triggerConfidence);
    const double entryBoundaryBps = longSignal
        ? (xLongStar * 10000.0)
        : (shortSignal ? (-xShortStar * 10000.0) : (std::min(xLongStar, -xShortStar) * 10000.0));

    out.ready = std::isfinite(horizonDriftBps) && std::isfinite(horizonVolBps) && std::isfinite(entryBoundaryBps) && std::isfinite(confidence);
    out.driftBpsPerSample = meanLogReturn * 10000.0;
    out.volatilityBpsPerSample = sigma * 10000.0;
    out.horizonDriftBps = horizonDriftBps;
    out.horizonVolBps = horizonVolBps;
    out.entryBoundaryBps = std::fabs(entryBoundaryBps);
    out.confidence = confidence;
    out.longSignal = longSignal;
    out.shortSignal = shortSignal;
    return out.ready;
}

IbFxMultiStrategyEngine::ScalpExitSignals IbFxMultiStrategyEngine::BuildScalpExitSignals(
    Runtime& rt, double price, std::time_t nowTs,
    double momentumBps, double momentumSnr,
    double entryGateBps, double exitGateBps,
    double spreadBps, double confirmSnr,
    double minEntrySnr, bool volCapOk,
    const OptimalStoppingSignal* optimalSignal) const {

    ScalpExitSignals out;
    if (std::abs(rt.netPosition) <= 1e-9 || rt.avgEntryPrice <= 0.0) return out;
    if (!HasManagedPositionBasis(rt)) return out;

    out.pnlBps = (rt.netPosition > 0.0)
        ? ((price - rt.avgEntryPrice) / rt.avgEntryPrice * 10000.0)
        : ((rt.avgEntryPrice - price) / rt.avgEntryPrice * 10000.0);
    rt.peakFavorablePnlBps = std::max(rt.peakFavorablePnlBps, out.pnlBps);

    const bool hasOptimalSignal = (optimalSignal != nullptr && optimalSignal->ready);
    const double continuationDriftBps = hasOptimalSignal
        ? ((rt.netPosition > 0.0) ? optimalSignal->horizonDriftBps : -optimalSignal->horizonDriftBps)
        : ((rt.netPosition > 0.0) ? momentumBps : -momentumBps);
    const bool trendAligned = (continuationDriftBps > 0.0);
    const bool trendStillAlive = (continuationDriftBps >= std::max(exitGateBps, entryGateBps * 0.5));
    out.holdSeconds = (rt.entryTs > 0) ? (nowTs - rt.entryTs) : 0;

    out.takeProfit = (rt.cfg.takeProfitBps > 0.0 && out.pnlBps >= rt.cfg.takeProfitBps);
    out.stopLoss = (rt.cfg.stopLossBps > 0.0 && out.pnlBps <= -rt.cfg.stopLossBps);

    const bool breakevenArmed = (rt.cfg.breakevenArmBps > 0.0 && rt.peakFavorablePnlBps >= rt.cfg.breakevenArmBps);
    out.breakevenExit = breakevenArmed && (out.pnlBps <= rt.cfg.breakevenFloorBps);

    const bool trailingArmed = (rt.cfg.trailingArmBps > 0.0 && rt.peakFavorablePnlBps >= rt.cfg.trailingArmBps);
    out.trailingExit = trailingArmed && (out.pnlBps <= (rt.peakFavorablePnlBps - rt.cfg.trailingGivebackBps));

    const bool flipStopOnly = (out.pnlBps <= 0.0);
    const bool heldLongEnoughForFlip = (rt.cfg.minHoldBeforeFlipSec <= 0 || out.holdSeconds >= rt.cfg.minHoldBeforeFlipSec);
    const bool profitFlipAllowed = (!trailingArmed && heldLongEnoughForFlip);
    const bool flipHoldSatisfied = flipStopOnly || profitFlipAllowed;
    const bool reverseGateHit = hasOptimalSignal
        ? ((rt.netPosition > 0.0 && (optimalSignal->shortSignal || optimalSignal->horizonDriftBps <= -exitGateBps)) ||
           (rt.netPosition < 0.0 && (optimalSignal->longSignal || optimalSignal->horizonDriftBps >= exitGateBps)))
        : ((rt.netPosition > 0.0 && momentumBps <= -exitGateBps) || (rt.netPosition < 0.0 && momentumBps >= exitGateBps));
    const double reverseSnrReq = std::max(confirmSnr, std::max(minEntrySnr, confirmSnr * std::max(0.0, rt.cfg.reverseExitSnrMult)));
    const bool strongReverse = hasOptimalSignal ? (optimalSignal->confidence >= reverseSnrReq) : (momentumSnr >= reverseSnrReq);
    out.reverseSignal = flipHoldSatisfied && strongReverse && reverseGateHit;

    const bool continuationLost = hasOptimalSignal && !out.reverseSignal && out.holdSeconds > 0 && !trendStillAlive;
    const double decayFraction = ClampDouble(rt.cfg.signalDecayFraction, 0.25, 0.90);
    const double continuationConfidence = hasOptimalSignal ? optimalSignal->confidence : momentumSnr;
    const double decayConfidenceFloor = std::max(0.35, confirmSnr * decayFraction);
    const bool profitableGiveback =
        (out.pnlBps > 0.0 && rt.peakFavorablePnlBps > 0.0 && out.pnlBps <= rt.peakFavorablePnlBps * decayFraction);
    out.continuationExit = continuationLost &&
                           continuationConfidence < decayConfidenceFloor &&
                           (out.pnlBps <= std::max(0.0, entryGateBps) || profitableGiveback);

    const bool momentumDecay = (!trendAligned && out.holdSeconds > 0 &&
        ((hasOptimalSignal && optimalSignal->confidence < 1.0) || (!hasOptimalSignal && momentumSnr < 1.0)) &&
        !trendStillAlive);
    const double decayScale = 1.0 - 0.5 * (rt.cfg.signalDecayFraction - 0.50);
    const std::time_t softLimit = (rt.cfg.holdTimeoutSec > 0) ? static_cast<std::time_t>(rt.cfg.holdTimeoutSec * 2.0 * decayScale) : 0;
    const std::time_t hardLimit = (rt.cfg.holdTimeoutSec > 0) ? static_cast<std::time_t>(rt.cfg.holdTimeoutSec * 3.0 * decayScale) : 0;
    const std::time_t microLimit = (rt.cfg.holdTimeoutSec > 0) ? static_cast<std::time_t>(rt.cfg.holdTimeoutSec * 5.0 * decayScale) : 0;
    const bool softDecay = (out.holdSeconds > 0 && rt.cfg.holdTimeoutSec > 0 && out.holdSeconds >= softLimit && !trendAligned && (!trendStillAlive || momentumDecay));
    out.hardTimeExit = (out.holdSeconds > 0 && rt.cfg.holdTimeoutSec > 0 && out.holdSeconds >= hardLimit && !trendAligned && !trendStillAlive && out.pnlBps < 0.0);
    const bool microChop = (out.holdSeconds > 0 && rt.cfg.holdTimeoutSec > 0 && out.holdSeconds >= microLimit && !trendAligned && out.pnlBps <= 0.0);
    out.softTimeExit = microChop || (softDecay && (out.pnlBps <= 0.0 || momentumDecay || !volCapOk));
    const double spreadStretchBps = std::max(exitGateBps * 2.0,
        (rt.cfg.spreadThresholdBps > 0.0 ? rt.cfg.spreadThresholdBps * 1.5 : 0.0));
    out.spreadStretch = (spreadBps > spreadStretchBps && out.holdSeconds > 0 && (out.pnlBps <= 0.0 || !trendStillAlive));
    return out;
}

IbFxMultiStrategyEngine::ScalpEntryDecision IbFxMultiStrategyEngine::BuildScalpEntryDecision(
    const Runtime& rt, double price,
    bool hasQuote, bool spreadOk, bool volOk, bool volCapOk,
    double spreadBps, int effectiveConfirms,
    double entryGateBps, double confirmSnr,
    double minEntrySnr, double entryPadBps,
    bool forceMarketScalp) const {

    ScalpEntryDecision out;
    const int confirmWindow = ScalpConfirmWindowSamples(rt.cfg);
    const std::size_t minWarmup = RequiredScalpSamples(rt.cfg, effectiveConfirms, confirmWindow);
    const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
    if (rt.samples.size() < minWarmup) {
        out.warmupBlocked = true;
        return out;
    }
    if (!volCapOk) {
        out.reason = "scalp_vol_cap_gate";
        return out;
    }
    if (!hasQuote || spreadBps <= 0.0) {
        out.reason = "scalp_quote_gate";
        return out;
    }

    OptimalStoppingSignal currentSignal;
    if (!EstimateOptimalStoppingSignal(rt, 0, spreadBps, entryGateBps, currentSignal) || !currentSignal.ready) {
        out.warmupBlocked = true;
        return out;
    }

    const bool reverseEntrySignal =
        IsEnvEnabled("HEPTA_IB_SCALP_REVERSE_ENTRY_SIGNAL") ||
        IsEnvEnabled("HEPTA_IB_FX_SCALPING_REVERSE_ENTRY_SIGNAL");
    const bool enterLong = reverseEntrySignal ? currentSignal.shortSignal : currentSignal.longSignal;
    const bool enterShort = reverseEntrySignal ? currentSignal.longSignal : currentSignal.shortSignal;
    if (!enterLong && !enterShort) {
        out.reason = "scalp_optstop_not_ready";
        return out;
    }

    const double grossEdgeBps = std::fabs(currentSignal.horizonDriftBps);
    const double minEdgeBps = std::max(std::max(0.0, rt.cfg.minSignalBps), std::max(0.0, entryGateBps) * std::max(0.0, rt.cfg.minEdgeCostMultiple));
    if (grossEdgeBps + 1e-9 < minEdgeBps) {
        out.costGated = true;
        out.reason = "scalp_edge_cost_gate";
        return out;
    }
    if (currentSignal.confidence + 1e-9 < std::max(0.0, minEntrySnr)) {
        out.reason = "scalp_min_entry_snr";
        return out;
    }

    if (!spreadOk) {
        const double spreadRatio = (rt.cfg.spreadThresholdBps > 1e-9)
            ? (spreadBps / rt.cfg.spreadThresholdBps)
            : 1.0;
        const bool extremeSpread = (rt.cfg.spreadThresholdBps > 0.0 && spreadRatio > 2.0);
        const double spreadPenalty = 1.0 + std::max(0.0, spreadRatio - 1.0);
        const double requiredWideSpreadConfidence = std::max(std::max(0.0, minEntrySnr), std::max(0.0, confirmSnr)) * spreadPenalty;
        const double requiredWideSpreadEdgeBps = std::max(minEdgeBps, std::max(0.0, spreadBps)) * (1.0 + 0.5 * std::max(0.0, spreadRatio - 1.0));
        const bool wideSpreadOverride = !extremeSpread &&
            currentSignal.confidence + 1e-9 >= requiredWideSpreadConfidence &&
            grossEdgeBps + 1e-9 >= requiredWideSpreadEdgeBps;
        if (!wideSpreadOverride) {
            out.reason = "scalp_spread_gate";
            return out;
        }
    }

    if (!volOk) {
        const double volRatio = (rt.cfg.minVolatilityBps > 1e-9)
            ? ClampDouble(gateVolBps / rt.cfg.minVolatilityBps, 0.0, 1.0)
            : 1.0;
        const double lowVolPenalty = 1.0 + (1.0 - volRatio) * 0.75;
        const double requiredLowVolConfidence = std::max(std::max(0.0, minEntrySnr), std::max(0.0, confirmSnr)) * lowVolPenalty;
        const double requiredLowVolEdgeBps = std::max(minEdgeBps, std::max(0.0, entryGateBps)) * (1.0 + (1.0 - volRatio) * 0.90);
        const bool lowVolOverride =
            currentSignal.confidence + 1e-9 >= requiredLowVolConfidence &&
            grossEdgeBps + 1e-9 >= requiredLowVolEdgeBps;
        if (!lowVolOverride) {
            out.reason = "scalp_min_vol_gate";
            return out;
        }
    }

    double baseSlopeBps = 0.0;
    double baseNoiseBps = 0.0;
    double baseRmsBps = 0.0;
    const bool haveBaseSlope = EstimateSlopeBps(rt, 0, confirmWindow, baseSlopeBps, baseNoiseBps, baseRmsBps);

    int optstopConfirmCount = 1;
    int slopeConfirmCount = 0;
    int slopeSamples = 0;
    if (haveBaseSlope) {
        ++slopeSamples;
        if ((enterLong && baseSlopeBps > 0.0) || (enterShort && baseSlopeBps < 0.0)) {
            ++slopeConfirmCount;
        }
    }

    const int requiredConfirms = std::max(1, effectiveConfirms);
    for (int offset = 1; offset < requiredConfirms; ++offset) {
        OptimalStoppingSignal histSignal;
        if (!EstimateOptimalStoppingSignal(rt, static_cast<std::size_t>(offset), spreadBps, entryGateBps, histSignal) || !histSignal.ready) {
            out.reason = "scalp_confirm_window";
            return out;
        }
        const bool histAligned = enterLong ? histSignal.longSignal : histSignal.shortSignal;
        if (!histAligned) {
            out.reason = "scalp_confirm_direction";
            return out;
        }
        if (histSignal.confidence + 1e-9 < std::max(0.0, confirmSnr)) {
            out.reason = "scalp_confirm_snr";
            return out;
        }
        ++optstopConfirmCount;

        double histSlopeBps = 0.0;
        double histNoiseBps = 0.0;
        double histRmsBps = 0.0;
        if (EstimateSlopeBps(rt, static_cast<std::size_t>(offset), confirmWindow, histSlopeBps, histNoiseBps, histRmsBps)) {
            ++slopeSamples;
            if ((enterLong && histSlopeBps > 0.0) || (enterShort && histSlopeBps < 0.0)) {
                ++slopeConfirmCount;
            }
            if (haveBaseSlope && baseRmsBps > 1e-9 && rt.cfg.rmsGrowthRatio > 0.0 && histRmsBps > (baseRmsBps * rt.cfg.rmsGrowthRatio)) {
                out.reason = "scalp_rms_growth_gate";
                return out;
            }
        }
    }

    const double requiredRatio = ClampDouble(rt.cfg.slopeDecayRatio, 0.0, 1.0);
    if (requiredConfirms > 0) {
        const double optstopRatio = static_cast<double>(optstopConfirmCount) / static_cast<double>(requiredConfirms);
        if (optstopRatio + 1e-9 < requiredRatio) {
            out.reason = "scalp_confirm_ratio_gate";
            return out;
        }
    }
    if (slopeSamples > 0) {
        const double slopeRatio = static_cast<double>(slopeConfirmCount) / static_cast<double>(slopeSamples);
        if (slopeRatio + 1e-9 < requiredRatio) {
            out.reason = "scalp_slope_decay_gate";
            return out;
        }
    }

    const double finalEntryPadBps = std::max(entryPadBps, rt.cfg.entryBufferBps);
    if (enterLong) {
        out.shouldEnter = true;
        out.targetPos = rt.cfg.maxPosition;
        out.reason = reverseEntrySignal ? "scalp_entry_long_contra" : "scalp_entry_long_optstop";
        if (!forceMarketScalp && rt.lastAsk > 0.0 && rt.lastBid > 0.0 && rt.lastAsk >= rt.lastBid) {
            out.orderType = "LMT";
            out.lmtPrice = rt.lastAsk * (1.0 + finalEntryPadBps / 10000.0);
        } else {
            out.orderType = "MKT";
        }
    } else if (enterShort) {
        out.shouldEnter = true;
        out.targetPos = -rt.cfg.maxPosition;
        out.reason = reverseEntrySignal ? "scalp_entry_short_contra" : "scalp_entry_short_optstop";
        if (!forceMarketScalp && rt.lastBid > 0.0 && rt.lastAsk > 0.0 && rt.lastAsk >= rt.lastBid) {
            out.orderType = "LMT";
            out.lmtPrice = rt.lastBid * (1.0 - finalEntryPadBps / 10000.0);
        } else {
            out.orderType = "MKT";
        }
    }

    return out;
}

void IbFxMultiStrategyEngine::EvaluateRuntime(Runtime& rt, double price, std::time_t nowTs) {
    auto emitReason = [&](const std::string& reason) {
        if (rt.lastNoTradeReasonTs == 0 || (nowTs - rt.lastNoTradeReasonTs) >= 5) {
            rt.lastNoTradeReasonTs = nowTs;
            m_decisionAudits.push_back(std::string("no_trade_reason strategy=") + rt.cfg.name + " reason=" + reason);
        }
    };
    if (rt.hasPending) {
        if (m_pendingStallSec > 0 && rt.lastTradeTs > 0 && (nowTs - rt.lastTradeTs) >= m_pendingStallSec) {
            bool preservePending = false;
            if (rt.pendingOrderId > 0) {
                auto itPending = m_orderIntentById.find(rt.pendingOrderId);
                if (itPending != m_orderIntentById.end()) {
                    const bool hasExecutionProgress =
                        itPending->second.seenPartiallyFilled ||
                        itPending->second.lastStatusFilledQty > 1e-9 ||
                        itPending->second.appliedFilledQty > 1e-9 ||
                        itPending->second.reconciledByPosition;
                    if (hasExecutionProgress) {
                        preservePending = true;
                        m_decisionAudits.push_back(
                            std::string("pending_stall_preserve_progress strategy=") + rt.cfg.name +
                            " order_id=" + std::to_string(rt.pendingOrderId) +
                            " stall_sec=" + std::to_string((long long)(nowTs - rt.lastTradeTs)) +
                            " filled_qty=" + std::to_string(itPending->second.lastStatusFilledQty) +
                            " applied_qty=" + std::to_string(itPending->second.appliedFilledQty) +
                            " reconciled=" + (itPending->second.reconciledByPosition ? std::string("1") : std::string("0")));
                    }
                }
            }
            if (preservePending) {
                emitReason("pending_active");
                return;
            }
            rt.hasPending = false;
            rt.pendingOrderId = -1;
            m_decisionAudits.push_back(std::string("pending_stall_reset strategy=") + rt.cfg.name + " stall_sec=" + std::to_string((long long)(nowTs - rt.lastTradeTs)));
        } else {
            emitReason("pending_active");
            return;
        }
    }
    if (rt.lossCooldownUntil != 0) {
        if (nowTs >= rt.lossCooldownUntil) {
            rt.lossCooldownUntil = 0;
            rt.consecutiveLosses = 0;
        } else {
            emitReason("loss_cooldown");
            return;
        }
    }
    if (rt.samples.size() < static_cast<std::size_t>(rt.cfg.slow)) { emitReason("insufficient_samples"); return; }

    const auto nowSteady = std::chrono::steady_clock::now();
    const long long nowMs = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    if (m_options.useSteadySignalClock) {
        const long long gateMs = (rt.cfg.signalIntervalMs > 0) ? static_cast<long long>(rt.cfg.signalIntervalMs) : (static_cast<long long>(rt.cfg.signalIntervalSec) * 1000LL);
        if (gateMs > 0 && rt.hasLastSignalSteady) {
            const long long elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(nowSteady - rt.lastSignalSteady).count();
            if (elapsedMs < gateMs) return;
        }
    } else {
        if (rt.cfg.signalIntervalMs > 0) {
            if (rt.lastSignalMs > 0 && (nowMs - rt.lastSignalMs) < rt.cfg.signalIntervalMs) return;
        } else {
            if (rt.lastSignalTs != 0 && nowTs - rt.lastSignalTs < rt.cfg.signalIntervalSec) return;
        }
    }

    rt.lastSignalTs = nowTs;
    rt.lastSignalMs = nowMs;
    rt.lastSignalSteady = nowSteady;
    rt.hasLastSignalSteady = true;

    const bool useSmaSignal =
        (rt.cfg.name == "fx_trend" || rt.cfg.name == "fx_momentum_burst" || rt.cfg.name == "fx_market_making" || rt.cfg.name == "fx_mean_revert");
    double fast = 0.0;
    double slow = 0.0;
    double diffBps = 0.0;
    if (useSmaSignal) {
        fast = ComputeSma(rt.samples, rt.cfg.fast);
        slow = ComputeSma(rt.samples, rt.cfg.slow);
        if (fast <= 0.0 || slow <= 0.0) return;
        diffBps = (fast - slow) / slow * 10000.0;
    } else if (rt.cfg.name != "fx_scalping") {
        return;
    }

    double signalStrength = 0.0;
    double targetPos = rt.netPosition;
    std::string reason;
    std::string explicitOrderType;
    double explicitLmtPrice = 0.0;

    const double spreadBps = (price > 0.0 && rt.lastAsk > 0.0 && rt.lastBid > 0.0) ? (std::fabs(rt.lastAsk - rt.lastBid) / price * 10000.0) : 0.0;
    const bool reliableQuote = (rt.lastBid > 0.0 && rt.lastAsk > 0.0 && rt.lastAsk >= rt.lastBid && spreadBps > 0.0);
    const char* pForceMarketScalp = std::getenv("HEPTA_IB_SCALP_FORCE_MKT");
    const bool forceMarketScalp = (pForceMarketScalp && (std::atoi(pForceMarketScalp) != 0));

    if (rt.cfg.name == "fx_scalping") {
        const bool hasQuote = (rt.lastBid > 0.0 && rt.lastAsk > 0.0 && rt.lastAsk >= rt.lastBid);
        const bool spreadOk = hasQuote && spreadBps > 0.0 && (rt.cfg.spreadThresholdBps <= 0.0 || spreadBps <= rt.cfg.spreadThresholdBps);
        const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
        const bool volOk = (gateVolBps >= rt.cfg.minVolatilityBps);
        const bool volCapOk = (rt.cfg.maxVolatilityBps <= 0.0 || gateVolBps <= rt.cfg.maxVolatilityBps);
        const bool highVol = (rt.cfg.maxVolatilityBps > 0.0)
                                ? (gateVolBps > std::max(rt.cfg.minVolatilityBps, rt.cfg.maxVolatilityBps * 0.65))
                                : (gateVolBps > rt.cfg.minVolatilityBps * 1.6);
        const bool lowVol = (rt.cfg.minVolatilityBps > 0.0 && gateVolBps > 0.0 && gateVolBps < rt.cfg.minVolatilityBps * 0.85);
        const int confirms = std::max(1, rt.cfg.confirmSamples);
        const int effectiveConfirms = std::max(1, highVol ? (confirms + 1) : (lowVol ? std::max(1, confirms - 1) : confirms));
        const double volWindowRatio = (rt.cfg.minVolatilityBps > 0.0 && gateVolBps > 0.0)
                                         ? (rt.cfg.minVolatilityBps / std::max(gateVolBps, 1e-9))
                                         : 1.0;
        const double slopeWindowMul = ClampDouble(0.8 + 0.5 * ClampDouble(volWindowRatio, 0.5, 2.0), 0.8, 1.8);
        const int signalWindowBase = ScalpBaseSlopeWindowSamples(rt.cfg);
        const int slopeWindow = ClampInt(static_cast<int>(std::llround(static_cast<double>(signalWindowBase) * slopeWindowMul)), 6, std::max(6, signalWindowBase * 3));
        const std::size_t scalpMinSamples = RequiredScalpSamples(rt.cfg, effectiveConfirms, slopeWindow);
        if (rt.samples.size() < scalpMinSamples) {
            emitReason("optimal_stop_warmup");
            return;
        }

        const double quoteCostBps = hasQuote ? spreadBps : 0.0;
        const double spreadAwareEntryBps = quoteCostBps * std::max(0.0, rt.cfg.entrySpreadMultiplier);
        double entryGateBps = spreadAwareEntryBps * std::max(0.2, rt.cfg.entryCostScale);
        if (rt.cfg.entryCostCapBps > 0.0) {
            entryGateBps = std::min(entryGateBps, rt.cfg.entryCostCapBps);
        }
        const double entryPadBps = std::max(rt.cfg.entryBufferBps, spreadAwareEntryBps * 0.25);
        const double exitGateBps = std::max(rt.cfg.signalExitBps, std::max(quoteCostBps, entryGateBps) * 0.75);
        const double momentumSnrScale = highVol ? 1.2 : (lowVol ? 0.8 : 1.0);
        const double confirmSnr = std::max(0.0, rt.cfg.confirmMomentumSnr) * momentumSnrScale;
        const double minEntrySnr = std::max(0.0, rt.cfg.minEntrySnr) * momentumSnrScale;

        if (std::abs(rt.netPosition) < 1e-9 && rt.pendingExitSignalTs > 0) {
            rt.pendingExitSignalTs = 0;
            rt.pendingExitReason.clear();
        }

        auto clearPendingFlipExit = [&]() {
            rt.pendingExitSignalTs = 0;
            rt.pendingExitReason.clear();
        };

        auto requestFlipExit = [&]() -> bool {
            if (rt.cfg.exitFlipConfirmSec <= 0) {
                clearPendingFlipExit();
                return true;
            }
            if (rt.pendingExitReason != "scalp_signal_flip") {
                rt.pendingExitReason = "scalp_signal_flip";
                rt.pendingExitSignalTs = nowTs;
                return false;
            }
            return (nowTs - rt.pendingExitSignalTs) >= rt.cfg.exitFlipConfirmSec;
        };

        auto setExitSignal = [&](const std::string& exitReason, const std::string& orderType) {
            if (rt.firstExitSignalTs <= 0) {
                rt.firstExitSignalTs = nowTs;
                rt.firstExitSignalReason = exitReason;
            }
            rt.activeExitReason = exitReason;
            rt.activeExitOrderType = orderType;
        };

        auto applyFlatExit = [&](const std::string& exitReason, const std::string& orderType = std::string(), double lmtPrice = 0.0) {
            targetPos = 0.0;
            reason = exitReason;
            explicitOrderType = orderType;
            explicitLmtPrice = lmtPrice;
            setExitSignal(reason, explicitOrderType);
            clearPendingFlipExit();
        };

        double momentumBps = 0.0;
        double momentumNoise = 0.0;
        double momentumRms = 0.0;
        if (!EstimateSlopeBps(rt, 0, slopeWindow, momentumBps, momentumNoise, momentumRms)) {
            emitReason("insufficient_samples");
            return;
        }
        const double momentumAbs = std::fabs(momentumBps);
        const double momentumSnr = (momentumNoise > 0.0) ? (momentumAbs / momentumNoise) : 0.0;
        OptimalStoppingSignal optimalStopSignal;
        const bool hasOptimalStopSignal = EstimateOptimalStoppingSignal(rt, 0, spreadBps, entryGateBps, optimalStopSignal) && optimalStopSignal.ready;
        signalStrength = hasOptimalStopSignal ? std::max(momentumAbs, std::fabs(optimalStopSignal.horizonDriftBps)) : momentumAbs;

        SeedExternalBasisIfNeeded(rt, price, nowTs);

        if (std::abs(rt.netPosition) > 1e-9 && rt.avgEntryPrice > 0.0) {
            const ScalpExitSignals exitSignals = BuildScalpExitSignals(
                rt, price, nowTs, momentumBps, momentumSnr,
                entryGateBps, exitGateBps, spreadBps,
                confirmSnr, minEntrySnr, volCapOk,
                hasOptimalStopSignal ? &optimalStopSignal : nullptr);

            if (exitSignals.takeProfit) {
                applyFlatExit("scalp_take_profit");
            } else if (exitSignals.stopLoss) {
                applyFlatExit("scalp_stop_loss", "MKT");
            } else if (exitSignals.trailingExit) {
                applyFlatExit("scalp_trailing_stop");
            } else if (exitSignals.breakevenExit) {
                applyFlatExit("scalp_break_even");
            } else if (exitSignals.reverseSignal) {
                if (requestFlipExit()) {
                    targetPos = 0.0;
                    reason = "scalp_signal_flip";
                    explicitOrderType.clear();
                    explicitLmtPrice = 0.0;
                    setExitSignal(reason, explicitOrderType);
                } else {
                    reason = "scalp_signal_flip_confirm_wait";
                    emitReason("flip_confirm_wait");
                }
            } else if (exitSignals.continuationExit) {
                applyFlatExit("scalp_optstop_decay");
            } else if (exitSignals.spreadStretch) {
                applyFlatExit("scalp_spread_stretch", "MKT");
            } else if (exitSignals.hardTimeExit) {
                applyFlatExit("scalp_time_exit");
            } else if (exitSignals.softTimeExit) {
                applyFlatExit("scalp_time_exit");
            }
        }

        if (reason.empty()) {
            clearPendingFlipExit();
        }
        if (reason.empty() && std::abs(rt.netPosition) < 1e-9) {
            const ScalpEntryDecision entryDecision = BuildScalpEntryDecision(
                rt, price, hasQuote, spreadOk, volOk, volCapOk,
                spreadBps, effectiveConfirms, entryGateBps,
                confirmSnr, minEntrySnr, entryPadBps, forceMarketScalp);
            if (entryDecision.warmupBlocked) {
                emitReason("optimal_stop_warmup");
                return;
            }
            if (entryDecision.costGated) {
                emitReason(entryDecision.reason.empty() ? "scalp_edge_cost_gate" : entryDecision.reason);
                return;
            }
            if (entryDecision.shouldEnter) {
                targetPos = entryDecision.targetPos;
                reason = entryDecision.reason;
                explicitOrderType = entryDecision.orderType;
                explicitLmtPrice = entryDecision.lmtPrice;
            } else if (!entryDecision.reason.empty()) {
                emitReason(entryDecision.reason);
                return;
            }
        }
    } else if (rt.cfg.name == "fx_trend") {



        if (HasManagedPositionBasis(rt)) {
            const double pnlBps = (rt.netPosition > 0.0)
                ? ((price - rt.avgEntryPrice) / rt.avgEntryPrice * 10000.0)
                : ((rt.avgEntryPrice - price) / rt.avgEntryPrice * 10000.0);

            if (rt.cfg.takeProfitBps > 0.0 && pnlBps >= rt.cfg.takeProfitBps) {
                targetPos = 0.0;
                reason = "trend_take_profit";
            } else if (rt.cfg.stopLossBps > 0.0 && pnlBps <= -rt.cfg.stopLossBps) {
                targetPos = 0.0;
                reason = "trend_stop_loss";
            } else if (HasManagedPositionClock(rt) && rt.cfg.holdTimeoutSec > 0 && rt.entryTs > 0 && (nowTs - rt.entryTs >= rt.cfg.holdTimeoutSec)) {
                targetPos = 0.0;
                reason = "trend_hold_timeout";
            }
        }

        if (reason.empty()) {
            if (std::abs(rt.netPosition) > 1e-9) {
                if ((rt.netPosition > 0.0 && diffBps < -rt.cfg.trendSignalBps) ||
                    (rt.netPosition < 0.0 && diffBps > rt.cfg.trendSignalBps)) {
                    targetPos = 0.0;
                    reason = "trend_flip_exit";
                } else if (std::abs(diffBps) < (rt.cfg.trendSignalBps * 0.35)) {
                    targetPos = 0.0;
                    reason = "trend_neutral_exit";
                }
            } else {
                if (!reliableQuote) {
                    emitReason("trend_quote_gate");
                    return;
                }
                const bool spreadOk = (rt.cfg.spreadThresholdBps <= 0.0 || spreadBps <= rt.cfg.spreadThresholdBps);
                if (!spreadOk) {
                    emitReason("trend_spread_gate");
                    return;
                }
                const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
                const bool volOk = (gateVolBps >= rt.cfg.minVolatilityBps);
                if (!volOk) {
                    emitReason("trend_min_vol_gate");
                    return;
                }
                if (diffBps > rt.cfg.trendSignalBps) {
                    targetPos = rt.cfg.maxPosition;
                    reason = "trend_up";
                } else if (diffBps < -rt.cfg.trendSignalBps) {
                    targetPos = -rt.cfg.maxPosition;
                    reason = "trend_down";
                }
            }
        }
    } else if (rt.cfg.name == "fx_momentum_burst") {

        if (HasManagedPositionBasis(rt)) {
            const double pnlBps = (rt.netPosition > 0.0)
                ? ((price - rt.avgEntryPrice) / rt.avgEntryPrice * 10000.0)
                : ((rt.avgEntryPrice - price) / rt.avgEntryPrice * 10000.0);

            if (rt.cfg.takeProfitBps > 0.0 && pnlBps >= rt.cfg.takeProfitBps) {
                targetPos = 0.0;
                reason = "burst_take_profit";
            } else if (rt.cfg.stopLossBps > 0.0 && pnlBps <= -rt.cfg.stopLossBps) {
                targetPos = 0.0;
                reason = "burst_stop_loss";
            } else if (HasManagedPositionClock(rt) && rt.cfg.holdTimeoutSec > 0 && rt.entryTs > 0 && (nowTs - rt.entryTs >= rt.cfg.holdTimeoutSec)) {
                targetPos = 0.0;
                reason = "burst_hold_timeout";
            } else if ((rt.netPosition > 0.0 && diffBps < 0.0) || (rt.netPosition < 0.0 && diffBps > 0.0)) {
                targetPos = 0.0;
                reason = "burst_flip_exit";
            }
        }

        if (std::abs(rt.netPosition) < 1e-9 && reason.empty()) {
            if (!reliableQuote) {
                emitReason("burst_quote_gate");
                return;
            }
            const bool spreadOk = (rt.cfg.spreadThresholdBps <= 0.0 || spreadBps <= rt.cfg.spreadThresholdBps);
            if (!spreadOk) {
                emitReason("burst_spread_gate");
                return;
            }
            const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
            if (gateVolBps < rt.cfg.minVolatilityBps) {
                emitReason("burst_min_vol_gate");
                return;
            }
            if (diffBps > rt.cfg.trendSignalBps) {
                targetPos = rt.cfg.maxPosition;
                reason = "burst_long";
            } else if (diffBps < -rt.cfg.trendSignalBps) {
                targetPos = -rt.cfg.maxPosition;
                reason = "burst_short";
            }
        }
    } else if (rt.cfg.name == "fx_market_making") {
        const bool hasQuote = reliableQuote;
        const bool spreadOk = hasQuote && (rt.cfg.spreadThresholdBps <= 0.0 || spreadBps <= rt.cfg.spreadThresholdBps);
        const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
        const bool volOk = (rt.cfg.maxVolatilityBps <= 0.0) || (gateVolBps <= rt.cfg.maxVolatilityBps);
        const double entryTriggerBps = std::max(rt.cfg.trendSignalBps, 0.01);
        const double revertTriggerBps = std::max(rt.cfg.signalExitBps, 0.01);
        const double adverseTriggerBps = entryTriggerBps + std::max(rt.cfg.entryBufferBps, revertTriggerBps);
        auto markExitSignal = [&](const std::string& exitReason, const std::string& orderType, bool isTimeoutSignal) {
            if (rt.firstExitSignalTs <= 0) {
                rt.firstExitSignalTs = nowTs;
                rt.firstExitSignalReason = exitReason;
            }
            if (isTimeoutSignal && rt.timeoutExitSignalTs <= 0) {
                rt.timeoutExitSignalTs = nowTs;
            }
            rt.activeExitReason = exitReason;
            rt.activeExitOrderType = orderType;
        };
        auto passiveBuyPx = [&](double bid, double ask) {
            if (!(bid > 0.0) || !(ask > 0.0) || ask < bid) return bid;
            const double improve = rt.cfg.quoteTickSize * static_cast<double>(rt.cfg.quoteImproveTicks);
            if ((ask - bid) <= rt.cfg.quoteTickSize * 1.5 || improve <= 0.0) return bid;
            return std::min(ask - rt.cfg.quoteTickSize, bid + improve);
        };
        auto passiveSellPx = [&](double bid, double ask) {
            if (!(bid > 0.0) || !(ask > 0.0) || ask < bid) return ask;
            const double improve = rt.cfg.quoteTickSize * static_cast<double>(rt.cfg.quoteImproveTicks);
            if ((ask - bid) <= rt.cfg.quoteTickSize * 1.5 || improve <= 0.0) return ask;
            return std::max(bid + rt.cfg.quoteTickSize, ask - improve);
        };
        auto smaAt = [&](int window, std::size_t endIdx, double& out) -> bool {
            if (window <= 0) return false;
            const std::size_t w = static_cast<std::size_t>(window);
            if (endIdx + 1 < w || rt.samples.size() < w) return false;
            double sum = 0.0;
            for (std::size_t i = 0; i < w; ++i) sum += rt.samples[endIdx - i];
            out = sum / static_cast<double>(w);
            return out > 0.0;
        };
        auto diffAt = [&](std::size_t endIdx, double& out) -> bool {
            double f = 0.0, s = 0.0;
            if (!smaAt(rt.cfg.fast, endIdx, f) || !smaAt(rt.cfg.slow, endIdx, s) || s <= 0.0) return false;
            out = (f - s) / s * 10000.0;
            return true;
        };

        if (std::abs(rt.netPosition) < 1e-9) {
            if (spreadOk && volOk) {
                const int confirms = std::max(1, rt.cfg.confirmSamples);
                bool shortSetup = true;
                bool longSetup = true;
                double lastStepBps = 0.0;
                if (rt.samples.size() >= 2) {
                    const double prevSample = rt.samples[rt.samples.size() - 2];
                    const double lastSample = rt.samples[rt.samples.size() - 1];
                    if (prevSample > 0.0) lastStepBps = (lastSample - prevSample) / prevSample * 10000.0;
                }
                if (rt.samples.size() < static_cast<std::size_t>(rt.cfg.slow + confirms - 1)) {
                    shortSetup = false;
                    longSetup = false;
                } else {
                    for (int i = 0; i < confirms; ++i) {
                        const std::size_t endIdx = rt.samples.size() - 1 - static_cast<std::size_t>(i);
                        double histDiff = 0.0;
                        if (!diffAt(endIdx, histDiff)) {
                            shortSetup = false;
                            longSetup = false;
                            break;
                        }
                        if (histDiff < entryTriggerBps) shortSetup = false;
                        if (histDiff > -entryTriggerBps) longSetup = false;
                    }
                }
                if (shortSetup && lastStepBps > rt.cfg.stallStepBps) shortSetup = false;
                if (longSetup && lastStepBps < -rt.cfg.stallStepBps) longSetup = false;
                if (shortSetup) {
                    targetPos = -rt.cfg.maxPosition;
                    reason = "mm_quote_ask";
                    explicitOrderType = "LMT";
                    explicitLmtPrice = passiveSellPx(rt.lastBid, rt.lastAsk);
                } else if (longSetup) {
                    targetPos = rt.cfg.maxPosition;
                    reason = "mm_quote_bid";
                    explicitOrderType = "LMT";
                    explicitLmtPrice = passiveBuyPx(rt.lastBid, rt.lastAsk);
                }
            }
        } else if (HasManagedPositionBasis(rt)) {
            const double pnlBps = (rt.netPosition > 0.0)
                ? ((price - rt.avgEntryPrice) / rt.avgEntryPrice * 10000.0)
                : ((rt.avgEntryPrice - price) / rt.avgEntryPrice * 10000.0);
            const bool stopExit = (rt.cfg.stopLossBps > 0.0 && pnlBps <= -rt.cfg.stopLossBps);
            const bool volExit = (rt.cfg.maxVolatilityBps > 0.0 && gateVolBps > rt.cfg.maxVolatilityBps);
            const bool adverseSignal = (rt.netPosition > 0.0 && diffBps <= -adverseTriggerBps) ||
                                      (rt.netPosition < 0.0 && diffBps >= adverseTriggerBps);
            const bool revertSignal = (rt.netPosition > 0.0)
                ? (diffBps >= -revertTriggerBps)
                : (diffBps <= revertTriggerBps);
            const bool pnlReached = (rt.cfg.takeProfitBps > 0.0 && pnlBps >= rt.cfg.takeProfitBps);
            const bool trustedHoldClock = HasManagedPositionClock(rt);
            const bool softTimeoutExit = (trustedHoldClock && rt.cfg.holdTimeoutSec > 0 && rt.entryTs > 0 && (nowTs - rt.entryTs >= rt.cfg.holdTimeoutSec));
            const bool hardTimeoutExit = (trustedHoldClock && rt.timeoutExitSignalTs > 0 && rt.cfg.holdTimeoutSec > 0 && (nowTs - rt.timeoutExitSignalTs >= rt.cfg.holdTimeoutSec));

            if (stopExit || volExit || adverseSignal || hardTimeoutExit) {
                targetPos = 0.0;
                reason = stopExit ? "mm_stop_loss" : (volExit ? "mm_vol_exit" : (adverseSignal ? "mm_signal_flip_exit" : "mm_hard_timeout"));
                explicitOrderType = "MKT";
                explicitLmtPrice = 0.0;
                markExitSignal(reason, explicitOrderType, false);
            } else if ((pnlReached || revertSignal) && spreadOk) {
                targetPos = 0.0;
                reason = "mm_take_profit_quote";
                explicitOrderType = "LMT";
                if (rt.netPosition > 0.0) {
                    const double minExit = rt.avgEntryPrice * (1.0 + std::max(0.0, rt.cfg.takeProfitBps) / 10000.0);
                    explicitLmtPrice = std::max(passiveSellPx(rt.lastBid, rt.lastAsk), minExit);
                } else {
                    const double maxExit = rt.avgEntryPrice * (1.0 - std::max(0.0, rt.cfg.takeProfitBps) / 10000.0);
                    explicitLmtPrice = std::min(passiveBuyPx(rt.lastBid, rt.lastAsk), maxExit);
                }
                markExitSignal(reason, explicitOrderType, false);
            } else if (softTimeoutExit && spreadOk && rt.timeoutExitSignalTs <= 0) {
                targetPos = 0.0;
                reason = "mm_soft_timeout_quote";
                explicitOrderType = "LMT";
                explicitLmtPrice = (rt.netPosition > 0.0) ? passiveSellPx(rt.lastBid, rt.lastAsk) : passiveBuyPx(rt.lastBid, rt.lastAsk);
                markExitSignal(reason, explicitOrderType, true);
            }
        }
    } else if (rt.cfg.name == "fx_mean_revert") {
        if (HasManagedPositionBasis(rt)) {
            const double pnlBps = (rt.netPosition > 0.0)
                ? ((price - rt.avgEntryPrice) / rt.avgEntryPrice * 10000.0)
                : ((rt.avgEntryPrice - price) / rt.avgEntryPrice * 10000.0);

            if (rt.cfg.takeProfitBps > 0.0 && pnlBps >= rt.cfg.takeProfitBps) {
                targetPos = 0.0;
                reason = "mr_take_profit";
            } else if (rt.cfg.stopLossBps > 0.0 && pnlBps <= -rt.cfg.stopLossBps) {
                targetPos = 0.0;
                reason = "mr_stop_loss";
            } else if (HasManagedPositionClock(rt) && rt.cfg.holdTimeoutSec > 0 && rt.entryTs > 0 && (nowTs - rt.entryTs >= rt.cfg.holdTimeoutSec)) {
                targetPos = 0.0;
                reason = "mr_hold_timeout";
            }
        }
        if (std::abs(rt.netPosition) < 1e-9) {
            if (!reliableQuote) {
                emitReason("mr_quote_gate");
                return;
            }
            const bool spreadOk = (rt.cfg.spreadThresholdBps <= 0.0 || spreadBps <= rt.cfg.spreadThresholdBps);
            if (!spreadOk) {
                emitReason("mr_spread_gate");
                return;
            }
            const double gateVolBps = NormalizeVolatilityBpsForReferenceInterval(rt.latestVolBps, rt.cfg);
            if (gateVolBps < rt.cfg.minVolatilityBps) {
                emitReason("mr_min_vol_gate");
                return;
            }
            if (diffBps > rt.cfg.trendSignalBps) {
                targetPos = -rt.cfg.maxPosition;
                reason = "mr_short_overextend";
            } else if (diffBps < -rt.cfg.trendSignalBps) {
                targetPos = rt.cfg.maxPosition;
                reason = "mr_long_oversold";
            }
        } else if (reason.empty()) {
            if (std::abs(diffBps) < (rt.cfg.trendSignalBps * 0.35)) {
                targetPos = 0.0;
                reason = "mr_revert_exit";
            }
        }
    }

    const double delta = targetPos - rt.netPosition;

    if (rt.cfg.name != "fx_scalping") {
        signalStrength = std::fabs(diffBps);
    }

    const double fastSlowDiffBps = std::max(0.0, signalStrength);

    if (std::abs(delta) < 1e-9) {
        emitReason("no_signal");
        return;
    }

    if (rt.cfg.minOrderQty > 1e-9 && std::abs(delta) < rt.cfg.minOrderQty) {
        emitReason("min_order_qty");
        return;
    }

    if (rt.cooldownUntil != 0 && nowTs < rt.cooldownUntil) {
        emitReason("cooldown");
        rt.lastSignalTs = nowTs;
        rt.lastSignalMs = nowMs;
        rt.lastSignalSteady = nowSteady;
        rt.hasLastSignalSteady = true;
        return;
    }

    IbFxOrderIntent intent;
    intent.strategy = rt.cfg.name;
    intent.instrument = rt.cfg.instrument;
    intent.side = (delta > 0.0) ? "BUY" : "SELL";
    intent.qty = std::min(std::abs(delta), rt.cfg.maxPosition);
    intent.referencePrice = price;
    intent.reason = reason.empty() ? "signal" : reason;
    intent.orderType = explicitOrderType;
    intent.lmtPrice = explicitLmtPrice;
    intent.signalStrength = std::max(0.1, fastSlowDiffBps);
    intent.riskCost = std::max(0.0, intent.qty);
    IbFxOrderLeg leg;
    leg.strategy = rt.cfg.name;
    leg.signedQty = (delta > 0.0 ? intent.qty : -intent.qty);
    intent.legs.push_back(leg);

    if (intent.qty > 0.0) {
        m_intents.push_back(intent);
    }
    rt.lastSignalTs = nowTs;
    rt.lastSignalMs = nowMs;
    rt.lastSignalSteady = nowSteady;
    rt.hasLastSignalSteady = true;
}


bool IbFxMultiStrategyEngine::SyncExternalPosition(const std::string& instrument, double netPosition, double markPrice, std::time_t nowTs) {
    PruneStaleReconciledResidualIntents(nowTs);

    std::vector<Runtime*> matchingRuntimes;
    matchingRuntimes.reserve(m_runtimes.size());
    for (auto& rt : m_runtimes) {
        if (IsSameInstrumentKey(rt.cfg.instrument, instrument)) matchingRuntimes.push_back(&rt);
    }
    if (matchingRuntimes.empty()) return false;

    Runtime* exclusiveRuntime = nullptr;
    if (matchingRuntimes.size() > 1) {
        double aggregateTrackedPosition = 0.0;
        std::vector<std::string> activeNames;
        activeNames.reserve(matchingRuntimes.size());
        int activeCount = 0;
        for (Runtime* rt : matchingRuntimes) {
            aggregateTrackedPosition += rt->netPosition;
            if (rt->hasPending || std::abs(rt->netPosition) > 1e-9) {
                activeNames.push_back(rt->cfg.name);
                ++activeCount;
                if (exclusiveRuntime == nullptr) exclusiveRuntime = rt;
            }
        }

        const double aggregateDelta = netPosition - aggregateTrackedPosition;
        const bool ambiguousMultiStrategy = (activeCount > 1);
        const bool noClearOwner = (exclusiveRuntime == nullptr);
        if ((ambiguousMultiStrategy || noClearOwner) && std::abs(aggregateDelta) > 1e-9) {
            m_decisionAudits.push_back(
                std::string("sync_external_position_multi_strategy_ambiguous instrument=") + instrument +
                " broker=" + std::to_string(netPosition) +
                " tracked=" + std::to_string(aggregateTrackedPosition) +
                " delta=" + std::to_string(aggregateDelta) +
                " active=" + (activeNames.empty() ? std::string("none") : JoinNames(activeNames)));
            return false;
        }
        if (ambiguousMultiStrategy) {
            return false;
        }
        if (exclusiveRuntime == nullptr) {
            if (std::abs(netPosition) > 1e-9) {
                m_decisionAudits.push_back(
                    std::string("sync_external_position_multi_strategy_bootstrap_ambiguous instrument=") + instrument +
                    " broker=" + std::to_string(netPosition));
            }
            return false;
        }
    }

    bool handled = false;
    for (auto& rt : m_runtimes) {
        if (!IsSameInstrumentKey(rt.cfg.instrument, instrument)) continue;
        if (exclusiveRuntime != nullptr && &rt != exclusiveRuntime) continue;
        bool runtimeHandled = false;

        auto tryConsumePendingDelta = [&]() {
            bool consumedAny = false;
            for (int guard = 0; guard < 8; ++guard) {
                const double currentPos = rt.netPosition;
                const double remainingDelta = netPosition - currentPos;
                if (std::abs(remainingDelta) <= 1e-9) break;

                struct ReconcileCandidate {
                    long orderId = -1;
                    double signedQty = 0.0;
                    double matchedQty = 0.0;
                    double overshootQty = 0.0;
                };
                bool haveCandidate = false;
                ReconcileCandidate chosen;
                for (const auto& kv : m_orderIntentById) {
                    if (!IsSameInstrumentKey(kv.second.instrument, instrument)) continue;
                    const bool runtimeBoundOrder = (rt.hasPending && rt.pendingOrderId > 0 && rt.pendingOrderId == kv.first);
                    if (rt.hasPending && rt.pendingOrderId > 0 && !runtimeBoundOrder) continue;
                    if (kv.second.reconciledByPosition && !runtimeBoundOrder) continue;
                    const double signedQty = SignedQtyForStrategy(kv.second, rt.cfg.name);
                    if (std::abs(signedQty) <= 1e-9) continue;
                    const bool sameDirection =
                        (remainingDelta > 1e-9 && signedQty > 1e-9) ||
                        (remainingDelta < -1e-9 && signedQty < -1e-9);
                    if (!sameDirection) continue;

                    const double matchedQty = std::min(std::abs(signedQty), std::abs(remainingDelta));
                    if (!(matchedQty > 1e-9)) continue;
                    const double overshootQty = std::max(0.0, std::abs(signedQty) - std::abs(remainingDelta));
                    if (!haveCandidate ||
                        matchedQty > chosen.matchedQty + 1e-9 ||
                        (std::abs(matchedQty - chosen.matchedQty) <= 1e-9 && overshootQty + 1e-9 < chosen.overshootQty) ||
                        (std::abs(matchedQty - chosen.matchedQty) <= 1e-9 && std::abs(overshootQty - chosen.overshootQty) <= 1e-9 && kv.first < chosen.orderId)) {
                        chosen.orderId = kv.first;
                        chosen.signedQty = signedQty;
                        chosen.matchedQty = matchedQty;
                        chosen.overshootQty = overshootQty;
                        haveCandidate = true;
                    }
                }

                if (!haveCandidate) break;
                const long chosenOrderId = chosen.orderId;
                auto itIntent = m_orderIntentById.find(chosenOrderId);
                if (itIntent == m_orderIntentById.end()) break;
                const double appliedSignedQty = (chosen.signedQty > 0.0) ? chosen.matchedQty : -chosen.matchedQty;
                const double reconciledPos = currentPos + appliedSignedQty;
                const bool partialReconcile = (chosen.matchedQty + 1e-9 < std::abs(chosen.signedQty));

                const double syntheticFillPx =
                    ((NormalizeStatus(itIntent->second.orderType) == "LMT" && itIntent->second.lmtPrice > 0.0)
                        ? itIntent->second.lmtPrice
                        : ((markPrice > 0.0) ? markPrice : ((rt.lastMid > 0.0) ? rt.lastMid : itIntent->second.referencePrice)));
                m_decisionAudits.push_back(
                    std::string("synthetic_position_reconcile strategy=") + rt.cfg.name +
                    " instrument=" + rt.cfg.instrument +
                    " order_id=" + std::to_string(chosenOrderId) +
                    " prev=" + std::to_string(currentPos) +
                    " observed=" + std::to_string(netPosition) +
                    " matched_qty=" + std::to_string(chosen.matchedQty) +
                    " intent_qty=" + std::to_string(std::abs(chosen.signedQty)) +
                    " partial=" + (partialReconcile ? std::string("1") : std::string("0")) +
                    " px_hint=" + std::to_string(syntheticFillPx));

                for (auto& leg : itIntent->second.legs) {
                    if (leg.strategy != rt.cfg.name) continue;
                    if (!leg.reconciledSnapshotTaken) {
                        leg.reconciledSnapshotTaken = true;
                        leg.reconciledStartPosition = currentPos;
                        leg.reconciledStartAvgEntryPrice = rt.avgEntryPrice;
                        leg.reconciledStartOpenPositionTs = rt.openPositionTs;
                        leg.reconciledStartBasisSource = rt.basisSource;
                        leg.reconciledStartPositionSource = rt.positionSource;
                        leg.reconciledStartTimeSource = rt.timeSource;
                        leg.reconciledStartEntryReason = rt.activeEntryReason;
                        leg.reconciledStartEntryOrderType = rt.activeEntryOrderType;
                        leg.reconciledStartFirstExitSignalTs = rt.firstExitSignalTs;
                        leg.reconciledStartFirstExitSignalReason = rt.firstExitSignalReason;
                    }
                }

                // Lightweight reconciliation only: trust broker position delta for
                // inventory state, but do not synthesize a full broker fill lifecycle
                // with synthetic prices. Keep the intent alive so late broker
                // orderStatus/execDetails callbacks can still attach and finalize
                // real fill/cancel stats instead of getting dropped on the floor.
                rt.netPosition = reconciledPos;
                const bool preserveExistingBinding =
                    (rt.hasPending && rt.pendingOrderId > 0 && rt.pendingOrderId != chosenOrderId);
                if (!preserveExistingBinding) {
                    rt.hasPending = partialReconcile;
                    rt.pendingOrderId = partialReconcile ? chosenOrderId : -1;
                    rt.lastTradeTs = nowTs;
                } else {
                    m_decisionAudits.push_back(
                        std::string("preserve_pending_binding_reconcile strategy=") + rt.cfg.name +
                        " instrument=" + rt.cfg.instrument +
                        " current_order_id=" + std::to_string(rt.pendingOrderId) +
                        " reconciled_order_id=" + std::to_string(chosenOrderId) +
                        " partial=" + (partialReconcile ? std::string("1") : std::string("0")));
                }
                rt.strategyOwnsBaselineUntil = nowTs + m_strategyOwnershipSec;
                rt.externalBaseline = false;
                rt.positionSource = "strategy_reconciled";
                if (std::abs(rt.netPosition) > 1e-9) {
                    rt.timeSource = "reconcile";
                    if (rt.avgEntryPrice <= 0.0 && syntheticFillPx > 0.0) {
                        rt.avgEntryPrice = syntheticFillPx;
                        rt.basisSource = "hint_reconcile";
                    }
                    if (rt.entryTs <= 0) rt.entryTs = nowTs;
                    if (rt.openPositionTs <= 0) rt.openPositionTs = nowTs;
                    rt.positionIntent = (rt.netPosition > 0.0) ? "LONG" : "SHORT";
                } else {
                    rt.avgEntryPrice = 0.0;
                    rt.entryTs = 0;
                    rt.openPositionTs = 0;
                    rt.peakFavorablePnlBps = 0.0;
                    rt.cycleRealizedPnl = 0.0;
                    rt.positionIntent = "FLAT";
                    rt.basisSource = "none";
                    rt.positionSource = "flat";
                    rt.timeSource = "none";
                    rt.strategyOwnsBaselineUntil = 0;
                    rt.activeEntryReason.clear();
                    rt.activeEntryOrderType.clear();
                    rt.activeExitReason.clear();
                    rt.activeExitOrderType.clear();
                    rt.firstExitSignalTs = 0;
                    rt.timeoutExitSignalTs = 0;
                    rt.firstExitSignalReason.clear();
                    rt.pendingExitSignalTs = 0;
                    rt.pendingExitReason.clear();
                }
                if (partialReconcile) {
                    itIntent->second.seenPartiallyFilled = true;
                }
                itIntent->second.reconciledByPosition = true;
                itIntent->second.reconciledAtTs = nowTs;
                itIntent->second.reconciledPxHint = syntheticFillPx;
                consumedAny = true;
            }
            return consumedAny;
        };

        const bool consumedPending = tryConsumePendingDelta();
        runtimeHandled = runtimeHandled || consumedPending;
        if (consumedPending && std::abs(rt.netPosition - netPosition) <= 1e-9) {
            handled = handled || runtimeHandled;
            continue;
        }

        bool allowPendingPartialReconcile = false;
        if (rt.hasPending && rt.pendingOrderId > 0) {
            auto itPending = m_orderIntentById.find(rt.pendingOrderId);
            if (itPending != m_orderIntentById.end()) {
                allowPendingPartialReconcile = itPending->second.seenPartiallyFilled || itPending->second.lastStatusFilledQty > 1e-9;
            }
        }
        if (rt.hasPending && !allowPendingPartialReconcile && rt.lastTradeTs > 0 && nowTs >= rt.lastTradeTs && (nowTs - rt.lastTradeTs) < m_pendingStallSec) {
            m_decisionAudits.push_back(
                std::string("sync_external_position_skipped_pending instrument=") + rt.cfg.instrument +
                " order_id=" + std::to_string(rt.pendingOrderId) +
                " seconds_since_last=" + std::to_string(nowTs - rt.lastTradeTs));
            handled = handled || runtimeHandled;
            continue;
        }

        const double prevPos = rt.netPosition;
        const bool wasExternal = rt.externalBaseline;
        const bool posChanged = (std::abs(prevPos - netPosition) > 1e-9);
        const bool prevFlat = (std::abs(prevPos) < 1e-9);
        const bool nowFlat = (std::abs(netPosition) < 1e-9);
        const bool signFlip = !prevFlat && !nowFlat && ((prevPos > 0.0 && netPosition < 0.0) || (prevPos < 0.0 && netPosition > 0.0));
        const bool sameSign = (prevPos > 0.0 && netPosition > 0.0) || (prevPos < 0.0 && netPosition < 0.0);
        const bool strategyOwnsBaselineWindow = (!nowFlat && !prevFlat && !signFlip && nowTs < rt.strategyOwnsBaselineUntil);
        const bool strategyIntentMatches =
            (!nowFlat &&
             ((rt.positionIntent == "LONG" && netPosition > 0.0) ||
              (rt.positionIntent == "SHORT" && netPosition < 0.0)) &&
             sameSign && rt.lastTradeTs > 0 && nowTs >= rt.lastTradeTs && (nowTs - rt.lastTradeTs) <= std::max<std::time_t>(m_pendingStallSec, 30));
        const bool keepStrategyBaseline = strategyOwnsBaselineWindow || strategyIntentMatches;

        if (keepStrategyBaseline) {
            runtimeHandled = true;
            if (posChanged) {
                const double prevAbs = std::abs(prevPos);
                const double nowAbs = std::abs(netPosition);
                const double basisPx = (markPrice > 0.0) ? markPrice : rt.lastMid;
                const std::string prevBasisSource = rt.basisSource;
                const std::string prevPositionSource = rt.positionSource;
                const std::string prevTimeSource = rt.timeSource;
                const bool sizeIncreasedViaSync = (sameSign && nowAbs > prevAbs + 1e-9);
                rt.netPosition = netPosition;
                rt.positionSource = "strategy_sync";
                if (sameSign && rt.avgEntryPrice > 0.0 && nowAbs > prevAbs && prevAbs > 1e-9 && basisPx > 0.0) {
                    const double addAbs = nowAbs - prevAbs;
                    rt.avgEntryPrice = ((rt.avgEntryPrice * prevAbs) + (basisPx * addAbs)) / (prevAbs + addAbs);
                    rt.basisSource = "strategy_sync";
                } else if (sameSign && rt.avgEntryPrice <= 0.0 && basisPx > 0.0) {
                    rt.avgEntryPrice = basisPx;
                    rt.basisSource = "strategy_sync";
                }
                if (sizeIncreasedViaSync) {
                    rt.basisSource = "strategy_sync";
                    rt.timeSource = "strategy_sync";
                }
                if (std::abs(rt.netPosition) > 1e-9 && rt.openPositionTs <= 0) {
                    rt.entryTs = nowTs;
                    rt.openPositionTs = nowTs;
                    rt.timeSource = "strategy_sync";
                }
                m_decisionAudits.push_back(std::string("sync_external_position_owned instrument=") + rt.cfg.instrument + " prev=" + std::to_string(prevPos) + " now=" + std::to_string(netPosition));
                if (prevBasisSource != rt.basisSource || prevPositionSource != rt.positionSource || prevTimeSource != rt.timeSource) {
                    m_decisionAudits.push_back(
                        std::string("sync_external_position_owned_sources instrument=") + rt.cfg.instrument +
                        " basis=" + prevBasisSource + "->" + rt.basisSource +
                        " pos=" + prevPositionSource + "->" + rt.positionSource +
                        " time=" + prevTimeSource + "->" + rt.timeSource);
                }
            }
            rt.externalBaseline = false;
            if (strategyIntentMatches && nowTs + m_strategyOwnershipSec > rt.strategyOwnsBaselineUntil) {
                rt.strategyOwnsBaselineUntil = nowTs + m_strategyOwnershipSec;
                m_decisionAudits.push_back(std::string("strategy_owns_baseline_extend instrument=") + rt.cfg.instrument + " until=" + std::to_string(static_cast<long long>(rt.strategyOwnsBaselineUntil)));
            }
            if (rt.positionIntent.empty() || rt.positionIntent == "FLAT") {
                rt.positionIntent = (netPosition > 0.0) ? "LONG" : "SHORT";
            }
            handled = handled || runtimeHandled;
            continue;
        }

        const bool allowExternalFallback = wasExternal || prevFlat || signFlip || nowFlat;
        if (!allowExternalFallback) {
            m_decisionAudits.push_back(std::string("external_sync_rejected_keep_strategy instrument=") + rt.cfg.instrument +
                " prev=" + std::to_string(prevPos) + " now=" + std::to_string(netPosition));
            handled = handled || runtimeHandled;
            continue;
        }

        runtimeHandled = true;
        rt.netPosition = netPosition;
        rt.externalBaseline = !nowFlat;
        rt.positionSource = nowFlat ? "flat" : "external_sync";
        rt.timeSource = nowFlat ? "none" : "external_sync";
        rt.strategyOwnsBaselineUntil = 0;

        if (!wasExternal) {
            m_decisionAudits.push_back(std::string("baseline_source_switch instrument=") + rt.cfg.instrument + " source=external_sync");
        }
        if (posChanged) {
            m_decisionAudits.push_back(std::string("sync_external_position instrument=") + rt.cfg.instrument + " prev=" + std::to_string(prevPos) + " now=" + std::to_string(netPosition));
        }

        if (nowFlat) {
            rt.avgEntryPrice = 0.0;
            rt.entryTs = 0;
            rt.peakFavorablePnlBps = 0.0;
            rt.openPositionTs = 0;
            rt.cycleRealizedPnl = 0.0;
            rt.basisSource = "none";
            rt.positionSource = "flat";
            rt.timeSource = "none";
            rt.positionIntent = "FLAT";
            rt.strategyOwnsBaselineUntil = 0;
            rt.activeEntryReason.clear();
            rt.activeEntryOrderType.clear();
            rt.activeExitReason.clear();
            rt.activeExitOrderType.clear();
            rt.firstExitSignalTs = 0;
            rt.timeoutExitSignalTs = 0;
            rt.firstExitSignalReason.clear();
            rt.pendingExitSignalTs = 0;
            rt.pendingExitReason.clear();
            handled = handled || runtimeHandled;
            continue;
        }

        const bool missingBasis = (rt.avgEntryPrice <= 0.0);
        const bool shouldRefreshBasis = missingBasis || prevFlat || signFlip;
        if (shouldRefreshBasis) {
            if (markPrice > 0.0) {
                rt.avgEntryPrice = markPrice;
                rt.basisSource = "external_mark";
            } else if (rt.lastMid > 0.0) {
                rt.avgEntryPrice = rt.lastMid;
                rt.basisSource = "external_mid";
            }
            rt.entryTs = nowTs;
            rt.peakFavorablePnlBps = 0.0;
            rt.openPositionTs = nowTs;
            rt.timeSource = "external_sync";
            rt.activeEntryReason.clear();
            rt.activeEntryOrderType.clear();
            rt.activeExitReason.clear();
            rt.activeExitOrderType.clear();
            rt.firstExitSignalTs = 0;
            rt.timeoutExitSignalTs = 0;
            rt.firstExitSignalReason.clear();
            rt.pendingExitSignalTs = 0;
            rt.pendingExitReason.clear();
        } else {
            if (rt.entryTs <= 0) rt.entryTs = nowTs;
            if (rt.openPositionTs <= 0) rt.openPositionTs = nowTs;
        }
        rt.positionIntent = (netPosition > 0.0) ? "LONG" : "SHORT";
        handled = handled || runtimeHandled;
    }

    return handled;
}

namespace {
    constexpr long kIbFxStateSchemaVersion = 2;

    enum class JsonFieldKind {
        Missing = 0,
        String,
        Primitive,
        Object,
        Array,
    };

    enum class JsonRequiredType {
        String = 0,
        NonEmptyString,
        Long,
        Double,
        Array,
        Bool01,
    };

    void SkipJsonWhitespace(const std::string& json, std::size_t& i) {
        while (i < json.size() && std::isspace(static_cast<unsigned char>(json[i]))) {
            ++i;
        }
    }

    bool ParseJsonStringToken(const std::string& json, std::size_t& i, std::string* out) {
        if (i >= json.size() || json[i] != '"') return false;
        ++i;
        std::string value;
        bool escape = false;
        while (i < json.size()) {
            const char c = json[i++];
            if (escape) {
                switch (c) {
                case 'n': value.push_back('\n'); break;
                case 'r': value.push_back('\r'); break;
                case 't': value.push_back('\t'); break;
                case '\\': value.push_back('\\'); break;
                case '"': value.push_back('"'); break;
                default: value.push_back(c); break;
                }
                escape = false;
                continue;
            }
            if (c == '\\') {
                escape = true;
                continue;
            }
            if (c == '"') {
                if (out) *out = value;
                return true;
            }
            value.push_back(c);
        }
        return false;
    }

    bool SkipJsonCompoundValue(const std::string& json, std::size_t& i) {
        if (i >= json.size() || (json[i] != '{' && json[i] != '[')) return false;
        int objectDepth = (json[i] == '{') ? 1 : 0;
        int arrayDepth = (json[i] == '[') ? 1 : 0;
        ++i;
        bool inString = false;
        bool escape = false;
        while (i < json.size()) {
            const char c = json[i++];
            if (inString) {
                if (escape) {
                    escape = false;
                } else if (c == '\\') {
                    escape = true;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }
            if (c == '"') {
                inString = true;
            } else if (c == '{') {
                ++objectDepth;
            } else if (c == '}') {
                if (objectDepth > 0) --objectDepth;
                if (objectDepth == 0 && arrayDepth == 0) return true;
            } else if (c == '[') {
                ++arrayDepth;
            } else if (c == ']') {
                if (arrayDepth > 0) --arrayDepth;
                if (objectDepth == 0 && arrayDepth == 0) return true;
            }
        }
        return false;
    }

    bool TryExtractTopLevelJsonField(const std::string& json,
                                     const std::string& key,
                                     JsonFieldKind& outKind,
                                     std::string& outValue) {
        outKind = JsonFieldKind::Missing;
        outValue.clear();

        std::size_t i = json.find('{');
        if (i == std::string::npos) return false;
        ++i;

        while (i < json.size()) {
            SkipJsonWhitespace(json, i);
            if (i < json.size() && json[i] == ',') {
                ++i;
                continue;
            }
            SkipJsonWhitespace(json, i);
            if (i >= json.size() || json[i] == '}') break;
            if (json[i] != '"') {
                ++i;
                continue;
            }

            std::string parsedKey;
            if (!ParseJsonStringToken(json, i, &parsedKey)) return false;

            SkipJsonWhitespace(json, i);
            if (i >= json.size() || json[i] != ':') return false;
            ++i;
            SkipJsonWhitespace(json, i);
            if (i >= json.size()) return false;

            if (json[i] == '"') {
                std::string parsedValue;
                if (!ParseJsonStringToken(json, i, &parsedValue)) return false;
                if (parsedKey == key) {
                    outKind = JsonFieldKind::String;
                    outValue = parsedValue;
                    return true;
                }
            } else if (json[i] == '{' || json[i] == '[') {
                const std::size_t start = i;
                const JsonFieldKind kind = (json[i] == '{') ? JsonFieldKind::Object : JsonFieldKind::Array;
                if (!SkipJsonCompoundValue(json, i)) return false;
                if (parsedKey == key) {
                    outKind = kind;
                    outValue = json.substr(start, i - start);
                    return true;
                }
            } else {
                std::size_t start = i;
                while (i < json.size() && json[i] != ',' && json[i] != '}') ++i;
                std::size_t end = i;
                while (start < end && std::isspace(static_cast<unsigned char>(json[start]))) ++start;
                while (end > start && std::isspace(static_cast<unsigned char>(json[end - 1]))) --end;
                if (parsedKey == key) {
                    outKind = JsonFieldKind::Primitive;
                    outValue = json.substr(start, end - start);
                    return true;
                }
            }
        }

        return true;
    }

    bool ParseLongStrict(const std::string& text, long& out) {
        if (text.empty()) return false;
        errno = 0;
        char* end = nullptr;
        const long value = std::strtol(text.c_str(), &end, 10);
        if (end == text.c_str() || errno == ERANGE) return false;
        while (end && *end && std::isspace(static_cast<unsigned char>(*end))) ++end;
        if (end && *end != '\0') return false;
        out = value;
        return true;
    }

    bool ParseDoubleStrict(const std::string& text, double& out) {
        if (text.empty()) return false;
        errno = 0;
        char* end = nullptr;
        const double value = std::strtod(text.c_str(), &end);
        if (end == text.c_str() || errno == ERANGE) return false;
        while (end && *end && std::isspace(static_cast<unsigned char>(*end))) ++end;
        if (end && *end != '\0') return false;
        if (!std::isfinite(value)) return false;
        out = value;
        return true;
    }

    bool ValidateRequiredFields(const std::string& json,
                                std::initializer_list<std::pair<const char*, JsonRequiredType> > fields) {
        for (const auto& entry : fields) {
            JsonFieldKind kind = JsonFieldKind::Missing;
            std::string value;
            if (!TryExtractTopLevelJsonField(json, entry.first, kind, value)) return false;
            switch (entry.second) {
            case JsonRequiredType::String:
                if (kind != JsonFieldKind::String) return false;
                break;
            case JsonRequiredType::NonEmptyString:
                if (kind != JsonFieldKind::String || value.empty()) return false;
                break;
            case JsonRequiredType::Long: {
                long parsed = 0;
                if (kind != JsonFieldKind::Primitive || !ParseLongStrict(value, parsed)) return false;
                break;
            }
            case JsonRequiredType::Double: {
                double parsed = 0.0;
                if (kind != JsonFieldKind::Primitive || !ParseDoubleStrict(value, parsed)) return false;
                break;
            }
            case JsonRequiredType::Array:
                if (kind != JsonFieldKind::Array) return false;
                break;
            case JsonRequiredType::Bool01: {
                long parsed = 0;
                if (kind != JsonFieldKind::Primitive || !ParseLongStrict(value, parsed)) return false;
                if (parsed != 0 && parsed != 1) return false;
                break;
            }
            }
        }
        return true;
    }

    bool IsAllowedStringValue(const std::string& value, std::initializer_list<const char*> allowed) {
        for (const char* candidate : allowed) {
            if (value == candidate) return true;
        }
        return false;
    }

    std::string EscapeJsonString(const std::string& value) {
        std::string out;
        out.reserve(value.size() + 8);
        for (char c : value) {
            switch (c) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out.push_back(c); break;
            }
        }
        return out;
    }

    std::string ExtractString(const std::string& json, const std::string& key) {
        JsonFieldKind kind = JsonFieldKind::Missing;
        std::string value;
        if (!TryExtractTopLevelJsonField(json, key, kind, value) || kind != JsonFieldKind::String) return "";
        return value;
    }

    long ExtractLong(const std::string& json, const std::string& key) {
        JsonFieldKind kind = JsonFieldKind::Missing;
        std::string value;
        if (!TryExtractTopLevelJsonField(json, key, kind, value) || value.empty()) return 0;
        return std::atol(value.c_str());
    }

    double ExtractDouble(const std::string& json, const std::string& key) {
        JsonFieldKind kind = JsonFieldKind::Missing;
        std::string value;
        if (!TryExtractTopLevelJsonField(json, key, kind, value) || value.empty()) return 0.0;
        return std::atof(value.c_str());
    }

    std::vector<std::string> ExtractFlatObjectBlocks(const std::string& json, const std::string& arrayKey) {
        std::vector<std::string> out;
        JsonFieldKind kind = JsonFieldKind::Missing;
        std::string arrayValue;
        if (!TryExtractTopLevelJsonField(json, arrayKey, kind, arrayValue) || kind != JsonFieldKind::Array) return out;

        std::size_t p = 1;
        if (arrayValue.empty() || arrayValue[0] != '[') return out;

        int objectDepth = 0;
        int arrayDepth = 1;
        bool inString = false;
        bool escape = false;
        std::size_t blockStart = std::string::npos;
        for (std::size_t i = p; i < arrayValue.size(); ++i) {
            const char c = arrayValue[i];

            if (inString) {
                if (escape) {
                    escape = false;
                } else if (c == '\\') {
                    escape = true;
                } else if (c == '"') {
                    inString = false;
                }
                continue;
            }

            if (c == '"') {
                inString = true;
                continue;
            }

            if (c == '[') {
                ++arrayDepth;
                continue;
            }

            if (c == ']') {
                if (objectDepth == 0) {
                    --arrayDepth;
                    if (arrayDepth <= 0) break;
                }
                continue;
            }

            if (c == '{') {
                if (objectDepth == 0) blockStart = i;
                ++objectDepth;
            } else if (c == '}') {
                if (objectDepth <= 0) continue;
                --objectDepth;
                if (objectDepth == 0 && blockStart != std::string::npos) {
                    out.push_back(arrayValue.substr(blockStart, i - blockStart + 1));
                    blockStart = std::string::npos;
                }
            }
        }
        return out;
    }

    std::string SerializeDoubleDeque(const std::deque<double>& values) {
        std::ostringstream oss;
        oss << std::setprecision(12);
        for (std::size_t i = 0; i < values.size(); ++i) {
            if (i) oss << ",";
            oss << values[i];
        }
        return oss.str();
    }

    std::deque<double> ParseDoubleDeque(const std::string& text) {
        std::deque<double> out;
        if (text.empty()) return out;
        std::stringstream ss(text);
        std::string item;
        while (std::getline(ss, item, ',')) {
            if (item.empty()) continue;
            out.push_back(std::atof(item.c_str()));
        }
        return out;
    }
}

bool IbFxMultiStrategyEngine::SaveState(const std::string& statePath) const {
    if (statePath.empty()) return false;
    std::string tmpPath = statePath + ".tmp";
    std::ofstream ofs(tmpPath);
    if (!ofs) return false;

    ofs << "{\n"
        << "  \"schemaVersion\": " << kIbFxStateSchemaVersion << ",\n"
        << "  \"strategies\": [\n";
    for (size_t i = 0; i < m_runtimes.size(); ++i) {
        const auto& rt = m_runtimes[i];
        ofs << "    {\n"
            << "      \"name\": \"" << EscapeJsonString(rt.cfg.name) << "\",\n"
            << "      \"lastSampleTs\": " << rt.lastSampleTs << ",\n"
            << "      \"lastSignalTs\": " << rt.lastSignalTs << ",\n"
            << "      \"lastSignalMs\": " << rt.lastSignalMs << ",\n"
            << "      \"lastTradeTs\": " << rt.lastTradeTs << ",\n"
            << "      \"cooldownUntil\": " << rt.cooldownUntil << ",\n"
            << "      \"positionIntent\": \"" << EscapeJsonString(rt.positionIntent) << "\",\n"
            << "      \"entryTs\": " << rt.entryTs << ",\n"
            << "      \"openPositionTs\": " << rt.openPositionTs << ",\n"
            << "      \"lastBid\": " << rt.lastBid << ",\n"
            << "      \"lastAsk\": " << rt.lastAsk << ",\n"
            << "      \"lastMid\": " << rt.lastMid << ",\n"
            << "      \"latestVolBps\": " << rt.latestVolBps << ",\n"
            << "      \"lastEvalMid\": " << rt.lastEvalMid << ",\n"
            << "      \"lastEvalSpreadBps\": " << rt.lastEvalSpreadBps << ",\n"
            << "      \"samplesCsv\": \"" << EscapeJsonString(SerializeDoubleDeque(rt.samples)) << "\",\n"
            << "      \"netPosition\": " << rt.netPosition << ",\n"
            << "      \"avgEntryPrice\": " << rt.avgEntryPrice << ",\n"
            << "      \"peakFavorablePnlBps\": " << rt.peakFavorablePnlBps << ",\n"
            << "      \"basisSource\": \"" << EscapeJsonString(rt.basisSource) << "\",\n"
            << "      \"positionSource\": \"" << EscapeJsonString(rt.positionSource) << "\",\n"
            << "      \"timeSource\": \"" << EscapeJsonString(rt.timeSource) << "\",\n"
            << "      \"ordersSent\": " << rt.ordersSent << ",\n"
            << "      \"fills\": " << rt.fills << ",\n"
            << "      \"rejects\": " << rt.rejects << ",\n"
            << "      \"cancels\": " << rt.cancels << ",\n"
            << "      \"closedTrades\": " << rt.closedTrades << ",\n"
            << "      \"winningTrades\": " << rt.winningTrades << ",\n"
            << "      \"totalHoldSec\": " << rt.totalHoldSec << ",\n"
            << "      \"realizedPnl\": " << rt.realizedPnl << ",\n"
            << "      \"cycleRealizedPnl\": " << rt.cycleRealizedPnl << ",\n"
            << "      \"externalBaseline\": " << (rt.externalBaseline ? 1 : 0) << ",\n"
            << "      \"strategyOwnsBaselineUntil\": " << rt.strategyOwnsBaselineUntil << ",\n"
            << "      \"activeEntryReason\": \"" << EscapeJsonString(rt.activeEntryReason) << "\",\n"
            << "      \"activeEntryOrderType\": \"" << EscapeJsonString(rt.activeEntryOrderType) << "\",\n"
            << "      \"activeExitReason\": \"" << EscapeJsonString(rt.activeExitReason) << "\",\n"
            << "      \"activeExitOrderType\": \"" << EscapeJsonString(rt.activeExitOrderType) << "\",\n"
            << "      \"firstExitSignalTs\": " << rt.firstExitSignalTs << ",\n"
            << "      \"timeoutExitSignalTs\": " << rt.timeoutExitSignalTs << ",\n"
            << "      \"firstExitSignalReason\": \"" << EscapeJsonString(rt.firstExitSignalReason) << "\",\n"
            << "      \"pendingExitSignalTs\": " << rt.pendingExitSignalTs << ",\n"
            << "      \"pendingExitReason\": \"" << EscapeJsonString(rt.pendingExitReason) << "\",\n"
            << "      \"consecutiveLosses\": " << rt.consecutiveLosses << ",\n"
            << "      \"lossCooldownUntil\": " << rt.lossCooldownUntil << ",\n"
            << "      \"lastNoTradeReasonTs\": " << rt.lastNoTradeReasonTs << ",\n"
            << "      \"hasPending\": " << (rt.hasPending ? 1 : 0) << ",\n"
            << "      \"pendingOrderId\": " << rt.pendingOrderId << "\n"
            << "    }" << (i + 1 == m_runtimes.size() ? "" : ",") << "\n";
    }
    ofs << "  ],\n";

    std::vector<long> orderIds;
    orderIds.reserve(m_orderIntentById.size());
    for (const auto& kv : m_orderIntentById) orderIds.push_back(kv.first);
    std::sort(orderIds.begin(), orderIds.end());

    ofs << "  \"pendingOrders\": [\n";
    for (size_t i = 0; i < orderIds.size(); ++i) {
        const auto it = m_orderIntentById.find(orderIds[i]);
        if (it == m_orderIntentById.end()) continue;
        const auto& intent = it->second;
        ofs << "    {\n"
            << "      \"orderId\": " << orderIds[i] << ",\n"
            << "      \"strategy\": \"" << EscapeJsonString(intent.strategy) << "\",\n"
            << "      \"instrument\": \"" << EscapeJsonString(intent.instrument) << "\",\n"
            << "      \"side\": \"" << EscapeJsonString(intent.side) << "\",\n"
            << "      \"qty\": " << intent.qty << ",\n"
            << "      \"referencePrice\": " << intent.referencePrice << ",\n"
            << "      \"reason\": \"" << EscapeJsonString(intent.reason) << "\",\n"
            << "      \"orderType\": \"" << EscapeJsonString(intent.orderType) << "\",\n"
            << "      \"lmtPrice\": " << intent.lmtPrice << ",\n"
            << "      \"signalStrength\": " << intent.signalStrength << ",\n"
            << "      \"riskCost\": " << intent.riskCost << ",\n"
            << "      \"seenPartiallyFilled\": " << (intent.seenPartiallyFilled ? 1 : 0) << ",\n"
            << "      \"seenFilled\": " << (intent.seenFilled ? 1 : 0) << ",\n"
            << "      \"lastStatusFilledQty\": " << intent.lastStatusFilledQty << ",\n"
            << "      \"lastStatusRemainingQty\": " << intent.lastStatusRemainingQty << ",\n"
            << "      \"lastStatusAvgFillPrice\": " << intent.lastStatusAvgFillPrice << ",\n"
            << "      \"appliedFilledQty\": " << intent.appliedFilledQty << ",\n"
            << "      \"appliedFilledNotional\": " << intent.appliedFilledNotional << ",\n"
            << "      \"reconciledByPosition\": " << (intent.reconciledByPosition ? 1 : 0) << ",\n"
            << "      \"reconciledAtTs\": " << intent.reconciledAtTs << ",\n"
            << "      \"reconciledPxHint\": " << intent.reconciledPxHint << "\n"
            << "    }" << (i + 1 == orderIds.size() ? "" : ",") << "\n";
    }
    ofs << "  ],\n";

    std::vector<std::pair<long, const IbFxOrderLeg*> > legEntries;
    for (long orderId : orderIds) {
        const auto it = m_orderIntentById.find(orderId);
        if (it == m_orderIntentById.end()) continue;
        for (const auto& leg : it->second.legs) {
            legEntries.push_back(std::make_pair(orderId, &leg));
        }
    }

    ofs << "  \"pendingOrderLegs\": [\n";
    for (size_t i = 0; i < legEntries.size(); ++i) {
        const long orderId = legEntries[i].first;
        const IbFxOrderLeg& leg = *legEntries[i].second;
        ofs << "    {\n"
            << "      \"orderId\": " << orderId << ",\n"
            << "      \"strategy\": \"" << EscapeJsonString(leg.strategy) << "\",\n"
            << "      \"signedQty\": " << leg.signedQty << ",\n"
            << "      \"reconciledSnapshotTaken\": " << (leg.reconciledSnapshotTaken ? 1 : 0) << ",\n"
            << "      \"reconciledStartPosition\": " << leg.reconciledStartPosition << ",\n"
            << "      \"reconciledStartAvgEntryPrice\": " << leg.reconciledStartAvgEntryPrice << ",\n"
            << "      \"reconciledStartOpenPositionTs\": " << leg.reconciledStartOpenPositionTs << ",\n"
            << "      \"reconciledStartBasisSource\": \"" << EscapeJsonString(leg.reconciledStartBasisSource) << "\",\n"
            << "      \"reconciledStartPositionSource\": \"" << EscapeJsonString(leg.reconciledStartPositionSource) << "\",\n"
            << "      \"reconciledStartTimeSource\": \"" << EscapeJsonString(leg.reconciledStartTimeSource) << "\",\n"
            << "      \"reconciledStartEntryReason\": \"" << EscapeJsonString(leg.reconciledStartEntryReason) << "\",\n"
            << "      \"reconciledStartEntryOrderType\": \"" << EscapeJsonString(leg.reconciledStartEntryOrderType) << "\",\n"
            << "      \"reconciledStartFirstExitSignalTs\": " << leg.reconciledStartFirstExitSignalTs << ",\n"
            << "      \"reconciledStartFirstExitSignalReason\": \"" << EscapeJsonString(leg.reconciledStartFirstExitSignalReason) << "\"\n"
            << "    }" << (i + 1 == legEntries.size() ? "" : ",") << "\n";
    }
    ofs << "  ]\n}\n";
    ofs.close();

    if (!AtomicRenameReplace(tmpPath, statePath)) {
        std::remove(tmpPath.c_str());
        return false;
    }
    return true;
}

bool IbFxMultiStrategyEngine::LoadState(const std::string& statePath) {
    std::ifstream ifs(statePath);
    if (!ifs) return false;
    std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());

    const long schemaVersion = ExtractLong(content, "schemaVersion");
    const bool strictState = (schemaVersion >= kIbFxStateSchemaVersion);
    if (schemaVersion > 0 && schemaVersion > kIbFxStateSchemaVersion) {
        return false;
    }

    JsonFieldKind strategiesKind = JsonFieldKind::Missing;
    std::string strategiesValue;
    if (!TryExtractTopLevelJsonField(content, "strategies", strategiesKind, strategiesValue) ||
        strategiesKind != JsonFieldKind::Array) {
        return false;
    }
    if (strictState && !ValidateRequiredFields(content, {
            {"strategies", JsonRequiredType::Array},
            {"pendingOrders", JsonRequiredType::Array},
            {"pendingOrderLegs", JsonRequiredType::Array},
        })) {
        return false;
    }

    const std::vector<std::string> strategyBlocks = ExtractFlatObjectBlocks(content, "strategies");
    if (!m_runtimes.empty() && strategyBlocks.empty()) {
        return false;
    }

    std::vector<Runtime> parsedRuntimes = m_runtimes;
    std::unordered_map<long, IbFxOrderIntent> parsedOrderIntentById;

    for (auto& rt : parsedRuntimes) {
        rt.hasPending = false;
        rt.pendingOrderId = -1;
        rt.hasLastSampleSteady = false;
        rt.hasLastSignalSteady = false;
        rt.hasLastEvalSteady = false;
    }

    auto findParsedRuntime = [&](const std::string& name) -> Runtime* {
        for (auto& rt : parsedRuntimes) {
            if (rt.cfg.name == name) return &rt;
        }
        return nullptr;
    };

    for (const auto& block : strategyBlocks) {
        if (strictState && !ValidateRequiredFields(block, {
                {"name", JsonRequiredType::NonEmptyString},
                {"lastSampleTs", JsonRequiredType::Long},
                {"lastSignalTs", JsonRequiredType::Long},
                {"lastSignalMs", JsonRequiredType::Long},
                {"lastTradeTs", JsonRequiredType::Long},
                {"cooldownUntil", JsonRequiredType::Long},
                {"positionIntent", JsonRequiredType::NonEmptyString},
                {"entryTs", JsonRequiredType::Long},
                {"openPositionTs", JsonRequiredType::Long},
                {"lastBid", JsonRequiredType::Double},
                {"lastAsk", JsonRequiredType::Double},
                {"lastMid", JsonRequiredType::Double},
                {"latestVolBps", JsonRequiredType::Double},
                {"lastEvalMid", JsonRequiredType::Double},
                {"lastEvalSpreadBps", JsonRequiredType::Double},
                {"samplesCsv", JsonRequiredType::String},
                {"netPosition", JsonRequiredType::Double},
                {"avgEntryPrice", JsonRequiredType::Double},
                {"peakFavorablePnlBps", JsonRequiredType::Double},
                {"basisSource", JsonRequiredType::NonEmptyString},
                {"positionSource", JsonRequiredType::NonEmptyString},
                {"timeSource", JsonRequiredType::NonEmptyString},
                {"ordersSent", JsonRequiredType::Long},
                {"fills", JsonRequiredType::Long},
                {"rejects", JsonRequiredType::Long},
                {"cancels", JsonRequiredType::Long},
                {"closedTrades", JsonRequiredType::Long},
                {"winningTrades", JsonRequiredType::Long},
                {"totalHoldSec", JsonRequiredType::Long},
                {"realizedPnl", JsonRequiredType::Double},
                {"cycleRealizedPnl", JsonRequiredType::Double},
                {"externalBaseline", JsonRequiredType::Bool01},
                {"strategyOwnsBaselineUntil", JsonRequiredType::Long},
                {"activeEntryReason", JsonRequiredType::String},
                {"activeEntryOrderType", JsonRequiredType::String},
                {"activeExitReason", JsonRequiredType::String},
                {"activeExitOrderType", JsonRequiredType::String},
                {"firstExitSignalTs", JsonRequiredType::Long},
                {"timeoutExitSignalTs", JsonRequiredType::Long},
                {"firstExitSignalReason", JsonRequiredType::String},
                {"pendingExitSignalTs", JsonRequiredType::Long},
                {"pendingExitReason", JsonRequiredType::String},
                {"consecutiveLosses", JsonRequiredType::Long},
                {"lossCooldownUntil", JsonRequiredType::Long},
                {"lastNoTradeReasonTs", JsonRequiredType::Long},
                {"hasPending", JsonRequiredType::Bool01},
                {"pendingOrderId", JsonRequiredType::Long},
            })) {
            return false;
        }

        std::string name = ExtractString(block, "name");
        if (name.empty()) continue;
        Runtime* rt = findParsedRuntime(name);
        if (!rt) continue;

        rt->lastSampleTs = ExtractLong(block, "lastSampleTs");
        rt->lastSignalTs = ExtractLong(block, "lastSignalTs");
        rt->lastSignalMs = ExtractLong(block, "lastSignalMs");
        rt->lastTradeTs = ExtractLong(block, "lastTradeTs");
        rt->entryTs = ExtractLong(block, "entryTs");
        rt->openPositionTs = ExtractLong(block, "openPositionTs");
        rt->lastBid = ExtractDouble(block, "lastBid");
        rt->lastAsk = ExtractDouble(block, "lastAsk");
        rt->lastMid = ExtractDouble(block, "lastMid");
        rt->latestVolBps = ExtractDouble(block, "latestVolBps");
        rt->lastEvalMid = ExtractDouble(block, "lastEvalMid");
        rt->lastEvalSpreadBps = ExtractDouble(block, "lastEvalSpreadBps");
        rt->samples = ParseDoubleDeque(ExtractString(block, "samplesCsv"));
        const std::size_t keep = RequiredSampleKeep(rt->cfg);
        while (rt->samples.size() > keep) rt->samples.pop_front();
        rt->hasLastSampleSteady = false;
        rt->hasLastSignalSteady = false;
        rt->hasLastEvalSteady = false;
        rt->netPosition = ExtractDouble(block, "netPosition");
        rt->avgEntryPrice = ExtractDouble(block, "avgEntryPrice");
        rt->peakFavorablePnlBps = ExtractDouble(block, "peakFavorablePnlBps");
        rt->basisSource = ExtractString(block, "basisSource");
        rt->positionSource = ExtractString(block, "positionSource");
        rt->timeSource = ExtractString(block, "timeSource");
        rt->ordersSent = ExtractLong(block, "ordersSent");
        rt->fills = ExtractLong(block, "fills");
        rt->rejects = ExtractLong(block, "rejects");
        rt->cancels = ExtractLong(block, "cancels");
        rt->closedTrades = ExtractLong(block, "closedTrades");
        rt->winningTrades = ExtractLong(block, "winningTrades");
        rt->totalHoldSec = ExtractLong(block, "totalHoldSec");
        rt->realizedPnl = ExtractDouble(block, "realizedPnl");
        rt->cycleRealizedPnl = ExtractDouble(block, "cycleRealizedPnl");
        rt->externalBaseline = (ExtractLong(block, "externalBaseline") != 0);
        rt->strategyOwnsBaselineUntil = ExtractLong(block, "strategyOwnsBaselineUntil");
        rt->activeEntryReason = ExtractString(block, "activeEntryReason");
        rt->activeEntryOrderType = ExtractString(block, "activeEntryOrderType");
        rt->activeExitReason = ExtractString(block, "activeExitReason");
        rt->activeExitOrderType = ExtractString(block, "activeExitOrderType");
        rt->firstExitSignalTs = ExtractLong(block, "firstExitSignalTs");
        rt->timeoutExitSignalTs = ExtractLong(block, "timeoutExitSignalTs");
        rt->firstExitSignalReason = ExtractString(block, "firstExitSignalReason");
        rt->pendingExitSignalTs = ExtractLong(block, "pendingExitSignalTs");
        rt->pendingExitReason = ExtractString(block, "pendingExitReason");
        rt->consecutiveLosses = static_cast<int>(ExtractLong(block, "consecutiveLosses"));
        rt->lossCooldownUntil = ExtractLong(block, "lossCooldownUntil");
        rt->lastNoTradeReasonTs = ExtractLong(block, "lastNoTradeReasonTs");
        rt->cooldownUntil = ExtractLong(block, "cooldownUntil");
        rt->positionIntent = ExtractString(block, "positionIntent");
        if (strictState && !IsAllowedStringValue(rt->positionIntent, {"LONG", "SHORT", "FLAT"})) {
            return false;
        }
        const bool savedHasPending = (ExtractLong(block, "hasPending") != 0);
        const long savedPendingOrderId = ExtractLong(block, "pendingOrderId");
        if (strictState && (savedHasPending != (savedPendingOrderId > 0))) {
            return false;
        }
        rt->hasPending = savedHasPending && savedPendingOrderId > 0;
        rt->pendingOrderId = rt->hasPending ? savedPendingOrderId : -1;
        if (std::abs(rt->netPosition) <= 1e-9) {
            rt->externalBaseline = false;
        }
        if (rt->positionIntent.empty()) {
            if (rt->netPosition > 1e-9) rt->positionIntent = "LONG";
            else if (rt->netPosition < -1e-9) rt->positionIntent = "SHORT";
            else rt->positionIntent = "FLAT";
        }
        if (rt->basisSource.empty()) {
            rt->basisSource = (rt->avgEntryPrice > 0.0) ? "legacy_unknown" : "none";
        }
        if (rt->positionSource.empty()) {
            rt->positionSource = (std::abs(rt->netPosition) > 1e-9) ? "legacy_unknown" : "flat";
        }
        if (rt->timeSource.empty()) {
            rt->timeSource = (rt->entryTs > 0 || rt->openPositionTs > 0) ? "legacy_unknown" : "none";
        }
        if (rt->openPositionTs <= 0 && rt->entryTs > 0 && std::abs(rt->netPosition) > 1e-9) {
            rt->openPositionTs = rt->entryTs;
        }
    }

    const std::vector<std::string> pendingOrderBlocks = ExtractFlatObjectBlocks(content, "pendingOrders");
    for (const auto& block : pendingOrderBlocks) {
        if (strictState && !ValidateRequiredFields(block, {
                {"orderId", JsonRequiredType::Long},
                {"strategy", JsonRequiredType::NonEmptyString},
                {"instrument", JsonRequiredType::NonEmptyString},
                {"side", JsonRequiredType::NonEmptyString},
                {"qty", JsonRequiredType::Double},
                {"referencePrice", JsonRequiredType::Double},
                {"reason", JsonRequiredType::String},
                {"orderType", JsonRequiredType::String},
                {"lmtPrice", JsonRequiredType::Double},
                {"signalStrength", JsonRequiredType::Double},
                {"riskCost", JsonRequiredType::Double},
                {"seenPartiallyFilled", JsonRequiredType::Bool01},
                {"seenFilled", JsonRequiredType::Bool01},
                {"lastStatusFilledQty", JsonRequiredType::Double},
                {"lastStatusRemainingQty", JsonRequiredType::Double},
                {"lastStatusAvgFillPrice", JsonRequiredType::Double},
                {"appliedFilledQty", JsonRequiredType::Double},
                {"appliedFilledNotional", JsonRequiredType::Double},
                {"reconciledByPosition", JsonRequiredType::Bool01},
                {"reconciledAtTs", JsonRequiredType::Long},
                {"reconciledPxHint", JsonRequiredType::Double},
            })) {
            return false;
        }

        const long orderId = ExtractLong(block, "orderId");
        if (orderId <= 0) continue;
        IbFxOrderIntent intent;
        intent.strategy = ExtractString(block, "strategy");
        intent.instrument = ExtractString(block, "instrument");
        intent.side = ExtractString(block, "side");
        intent.qty = ExtractDouble(block, "qty");
        intent.referencePrice = ExtractDouble(block, "referencePrice");
        intent.reason = ExtractString(block, "reason");
        intent.orderType = ExtractString(block, "orderType");
        intent.lmtPrice = ExtractDouble(block, "lmtPrice");
        intent.signalStrength = ExtractDouble(block, "signalStrength");
        intent.riskCost = ExtractDouble(block, "riskCost");
        intent.seenPartiallyFilled = (ExtractLong(block, "seenPartiallyFilled") != 0);
        intent.seenFilled = (ExtractLong(block, "seenFilled") != 0);
        intent.lastStatusFilledQty = ExtractDouble(block, "lastStatusFilledQty");
        intent.lastStatusRemainingQty = ExtractDouble(block, "lastStatusRemainingQty");
        intent.lastStatusAvgFillPrice = ExtractDouble(block, "lastStatusAvgFillPrice");
        intent.appliedFilledQty = ExtractDouble(block, "appliedFilledQty");
        intent.appliedFilledNotional = ExtractDouble(block, "appliedFilledNotional");
        intent.reconciledByPosition = (ExtractLong(block, "reconciledByPosition") != 0);
        intent.reconciledAtTs = ExtractLong(block, "reconciledAtTs");
        intent.reconciledPxHint = ExtractDouble(block, "reconciledPxHint");
        if (strictState) {
            const double maxQty = std::max(0.0, intent.qty) + 1e-6;
            if (!IsAllowedStringValue(intent.side, {"BUY", "SELL"})) return false;
            if (!intent.orderType.empty() && !IsAllowedStringValue(FormatOrderType(intent.orderType), {"MKT", "LMT"})) {
                return false;
            }
            if (intent.qty <= 0.0) return false;
            if (intent.reconciledPxHint < -1e-9) return false;
            if (intent.lastStatusFilledQty < -1e-9 || intent.lastStatusRemainingQty < -1e-9 ||
                intent.appliedFilledQty < -1e-9 || intent.appliedFilledNotional < -1e-9) {
                return false;
            }
            if (intent.lastStatusFilledQty > maxQty || intent.lastStatusRemainingQty > maxQty || intent.appliedFilledQty > maxQty) {
                return false;
            }
            if (intent.appliedFilledQty > intent.lastStatusFilledQty + 1e-6) {
                return false;
            }
            if (intent.lastStatusFilledQty + intent.lastStatusRemainingQty > maxQty + 1e-6) {
                return false;
            }
            if (intent.reconciledByPosition) {
                if (intent.reconciledAtTs <= 0) return false;
            } else {
                if (intent.reconciledAtTs != 0) return false;
                if (intent.reconciledPxHint > 1e-9) return false;
            }
        }
        parsedOrderIntentById[orderId] = intent;
    }

    const std::vector<std::string> pendingLegBlocks = ExtractFlatObjectBlocks(content, "pendingOrderLegs");
    for (const auto& block : pendingLegBlocks) {
        if (strictState && !ValidateRequiredFields(block, {
                {"orderId", JsonRequiredType::Long},
                {"strategy", JsonRequiredType::NonEmptyString},
                {"signedQty", JsonRequiredType::Double},
                {"reconciledSnapshotTaken", JsonRequiredType::Bool01},
                {"reconciledStartPosition", JsonRequiredType::Double},
                {"reconciledStartAvgEntryPrice", JsonRequiredType::Double},
                {"reconciledStartOpenPositionTs", JsonRequiredType::Long},
                {"reconciledStartBasisSource", JsonRequiredType::String},
                {"reconciledStartPositionSource", JsonRequiredType::String},
                {"reconciledStartTimeSource", JsonRequiredType::String},
                {"reconciledStartEntryReason", JsonRequiredType::String},
                {"reconciledStartEntryOrderType", JsonRequiredType::String},
                {"reconciledStartFirstExitSignalTs", JsonRequiredType::Long},
                {"reconciledStartFirstExitSignalReason", JsonRequiredType::String},
            })) {
            return false;
        }

        const long orderId = ExtractLong(block, "orderId");
        auto it = parsedOrderIntentById.find(orderId);
        if (it == parsedOrderIntentById.end()) {
            if (strictState) return false;
            continue;
        }
        IbFxOrderLeg leg;
        leg.strategy = ExtractString(block, "strategy");
        leg.signedQty = ExtractDouble(block, "signedQty");
        leg.reconciledSnapshotTaken = (ExtractLong(block, "reconciledSnapshotTaken") != 0);
        leg.reconciledStartPosition = ExtractDouble(block, "reconciledStartPosition");
        leg.reconciledStartAvgEntryPrice = ExtractDouble(block, "reconciledStartAvgEntryPrice");
        leg.reconciledStartOpenPositionTs = ExtractLong(block, "reconciledStartOpenPositionTs");
        leg.reconciledStartBasisSource = ExtractString(block, "reconciledStartBasisSource");
        leg.reconciledStartPositionSource = ExtractString(block, "reconciledStartPositionSource");
        leg.reconciledStartTimeSource = ExtractString(block, "reconciledStartTimeSource");
        leg.reconciledStartEntryReason = ExtractString(block, "reconciledStartEntryReason");
        leg.reconciledStartEntryOrderType = ExtractString(block, "reconciledStartEntryOrderType");
        leg.reconciledStartFirstExitSignalTs = ExtractLong(block, "reconciledStartFirstExitSignalTs");
        leg.reconciledStartFirstExitSignalReason = ExtractString(block, "reconciledStartFirstExitSignalReason");
        if (strictState && !findParsedRuntime(leg.strategy)) return false;
        it->second.legs.push_back(leg);
    }

    if (strictState) {
        for (const auto& kv : parsedOrderIntentById) {
            if (kv.second.legs.empty()) return false;
            double totalSignedQty = 0.0;
            bool anyReconcileSnapshot = false;
            for (const auto& leg : kv.second.legs) {
                totalSignedQty += leg.signedQty;
                if (leg.reconciledSnapshotTaken) anyReconcileSnapshot = true;
            }
            if (std::abs(totalSignedQty) <= 1e-9) return false;
            if (kv.second.side == "BUY" && totalSignedQty <= 1e-9) return false;
            if (kv.second.side == "SELL" && totalSignedQty >= -1e-9) return false;
            const double qtyGap = std::abs(std::abs(totalSignedQty) - kv.second.qty);
            const double qtyTol = std::max(1e-6, std::abs(kv.second.qty) * 1e-6);
            if (qtyGap > qtyTol) return false;
            if (kv.second.reconciledByPosition != anyReconcileSnapshot) return false;
            if (!kv.second.reconciledByPosition) {
                for (const auto& leg : kv.second.legs) {
                    Runtime* rt = findParsedRuntime(leg.strategy);
                    if (!rt) return false;
                    if (!rt->hasPending || rt->pendingOrderId != kv.first) {
                        return false;
                    }
                }
            }
        }
        for (const auto& rt : parsedRuntimes) {
            if (rt.hasPending &&
                parsedOrderIntentById.find(rt.pendingOrderId) == parsedOrderIntentById.end()) {
                return false;
            }
        }
    }

    for (auto& rt : parsedRuntimes) {
        if (rt.pendingOrderId > 0) {
            auto itPending = parsedOrderIntentById.find(rt.pendingOrderId);
            if (itPending != parsedOrderIntentById.end()) {
                const double signedQty = SignedQtyForStrategy(itPending->second, rt.cfg.name);
                if (std::abs(signedQty) > 1e-9) {
                    rt.hasPending = true;
                    continue;
                }
                if (strictState && rt.hasPending) {
                    return false;
                }
            } else if (strictState && rt.hasPending) {
                return false;
            }
        }
        rt.hasPending = false;
        rt.pendingOrderId = -1;
    }

    if (!strictState) {
        for (const auto& kv : parsedOrderIntentById) {
            for (const auto& leg : kv.second.legs) {
                Runtime* rt = findParsedRuntime(leg.strategy);
                if (!rt) continue;
                if (!rt->hasPending || rt->pendingOrderId <= 0) {
                    rt->hasPending = true;
                    rt->pendingOrderId = kv.first;
                }
            }
        }
    }

    m_runtimes.swap(parsedRuntimes);
    m_orderIntentById.swap(parsedOrderIntentById);
    PruneStaleReconciledResidualIntents(std::time(nullptr));

    return true;
}
