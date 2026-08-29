#pragma once

#include <string>
#include <vector>
#include <deque>
#include <unordered_map>
#include <ctime>
#include <chrono>

struct IbFxStrategyParams {
    std::string name;
    std::string instrument = "USD.CNH";
    int fast = 5;
    int slow = 20;
    int signalIntervalSec = 5;
    int signalIntervalMs = 0;
    double maxPosition = 1000.0;
    double minOrderQty = 25000.0;   // broker minimum executable lot/size guard

    // Scalping controls
    double spreadThresholdBps = 2.0;
    double minVolatilityBps = 0.8;
    int holdTimeoutSec = 60;
    double takeProfitBps = 8.0;
    double stopLossBps = 6.0;
    int cooldownSec = 10;
    double minSignalBps = 0.0;
    double entrySpreadMultiplier = 1.25;
    double entryBufferBps = 0.10;
    double signalExitBps = 0.15;
    double signalDecayFraction = 0.50;
    double maxVolatilityBps = 0.0;
    // Scalp quality gates
    double minEntrySnr = 1.5;          // momentum signal SNR required for entry decision
    double confirmMomentumSnr = 1.0;    // minimum SNR during confirm window
    double slopeDecayRatio = 0.55;      // minimum monotonicity ratio across confirm windows
    double rmsGrowthRatio = 2.0;        // max allowed RMS noise growth ratio
    double entryCostScale = 1.0;    // scale for spread-aware entry cost before dynamic cap
    double entryCostCapBps = 0.0;      // optional cap to prevent over-strict spread gating (0=off)
    int exitFlipConfirmSec = 1;        // confirmation delay before confirmed scalping momentum flip exit
    int confirmSamples = 1;
    int warmupSamples = 48;            // minimum observations before optimal-stopping signal is allowed
    int driftLookbackSamples = 36;     // recent log-return window used for GBM drift/vol estimation
    double optimalStopDiscount = 1.0;  // discount rate in bps per decision step for the GBM stopping boundary
    double optimalStopBoundaryScale = 1.0; // scales the closed-form GBM trigger distance from the anchor price
    int maxLossStreak = 0;             // 0=disabled; completed losing trades before cooldown
    int lossStreakCooldownSec = 0;     // cooldown after hitting maxLossStreak
    double estRoundTripCostUsd = 0.0;  // estimated all-in round-trip execution cost in account ccy
    double minEdgeCostMultiple = 1.0;  // required expected gross edge / estimated cost ratio
    int minHoldBeforeFlipSec = 0;      // minimum hold time before allowing signal-flip exit
    double reverseExitSnrMult = 1.0;   // reverse exit requires confirmSnr * multiplier
    double breakevenArmBps = 0.0;      // arm breakeven once peak favorable pnl reaches this level
    double breakevenFloorBps = 0.0;    // exit if pnl falls back to/below this floor after breakevenArmBps
    double trailingArmBps = 0.0;       // arm trailing exit once peak favorable pnl reaches this level
    double trailingGivebackBps = 0.0;  // allowed giveback from peak favorable pnl before trailing exit
    double stallStepBps = 0.02;
    double quoteTickSize = 0.00001;
    int quoteImproveTicks = 1;

    // Trend controls
    double trendSignalBps = 1.0;
};

struct IbFxOrderLeg {
    std::string strategy;
    double signedQty = 0.0;
    bool reconciledSnapshotTaken = false;
    double reconciledStartPosition = 0.0;
    double reconciledStartAvgEntryPrice = 0.0;
    std::time_t reconciledStartOpenPositionTs = 0;
    std::string reconciledStartBasisSource;
    std::string reconciledStartPositionSource;
    std::string reconciledStartTimeSource;
    std::string reconciledStartEntryReason;
    std::string reconciledStartEntryOrderType;
    std::time_t reconciledStartFirstExitSignalTs = 0;
    std::string reconciledStartFirstExitSignalReason;
};

struct IbFxOrderIntent {
    std::string strategy;
    std::string instrument;
    std::string side;
    double qty = 0.0;
    double referencePrice = 0.0;
    std::string reason;
    std::string orderType; // optional explicit order type override (e.g. MKT/LMT)
    double lmtPrice = 0.0; // used when orderType=LMT
    double signalStrength = 0.0; // stronger signal -> higher scheduling priority
    double riskCost = 0.0;       // approximate per-order risk footprint
    std::vector<IbFxOrderLeg> legs;
    bool seenPartiallyFilled = false;
    bool seenFilled = false;
    double lastStatusFilledQty = 0.0;
    double lastStatusRemainingQty = 0.0;
    double lastStatusAvgFillPrice = 0.0;
    double appliedFilledQty = 0.0;
    double appliedFilledNotional = 0.0;
    bool reconciledByPosition = false;
    std::time_t reconciledAtTs = 0;
    double reconciledPxHint = 0.0;
};

class IbFxMultiStrategyEngine {
public:
    struct Options {
        bool useSteadySignalClock = false;
        bool emitTimingAudits = false;
    };
    struct StrategySummary {
        std::string name;
        std::string instrument;
        double netPosition = 0.0;
        double avgEntryPrice = 0.0;
        double lastPrice = 0.0;
        double realizedPnl = 0.0;
        double unrealizedPnl = 0.0;
        double totalPnl = 0.0;
        double realizedPnlUsd = 0.0;
        double unrealizedPnlUsd = 0.0;
        double totalPnlUsd = 0.0;
        double estimatedCostsUsd = 0.0;
        long ordersSent = 0;
        long fills = 0;
        long rejects = 0;
        long cancels = 0;
        double fillRatePct = 0.0;
        long closedTrades = 0;
        long winningTrades = 0;
        double winRatePct = 0.0;
        double avgHoldSec = 0.0;
        bool externalBaseline = false;
        std::string basisSource = "none";
        std::string positionSource = "none";
        std::string timeSource = "none";
        bool unrealizedBasisTrusted = false;
        std::time_t lastNoTradeReasonTs = 0;

        long long evalCount = 0;
        long long evalTotalUs = 0;
        long long evalMaxUs = 0;
        long long cycleTotalMs = 0;
        long long cycleMaxMs = 0;
        long long lastEvalUs = 0;
        long long lastCycleMs = 0;
        std::chrono::steady_clock::time_point lastEvalSteady{};
        bool hasLastEvalSteady = false;
    };

    void Configure(const std::vector<IbFxStrategyParams>& strategies);
    void Configure(const std::vector<IbFxStrategyParams>& strategies, const Options& options);
    bool Empty() const;

    // Legacy price-only feed
    void OnTick(double price, std::time_t nowTs);
    // Quote feed for spread-aware strategies
    void OnQuote(double bid, double ask, std::time_t nowTs);
    std::vector<IbFxOrderIntent> DrainIntents();
    std::vector<std::string> DrainDecisionAudits();

    struct StrategyTimingSummary {
        std::string name;
        long long evalCount = 0;
        double avgEvalUs = 0.0;
        long long maxEvalUs = 0;
        double avgCycleMs = 0.0;
        long long maxCycleMs = 0;
        long long lastEvalUs = 0;
        long long lastCycleMs = 0;
    };
    std::vector<StrategyTimingSummary> GetTimingSummaries() const;

    void OnOrderPlaced(long orderId, const IbFxOrderIntent& intent);
    void OnOrderRejected(const IbFxOrderIntent& intent);
    void OnOrderStatus(long orderId, const std::string& status, double avgPrice, double filledQty = 0.0, double remainingQty = 0.0);
    bool HasPendingOrders() const;

    bool GetOrderIntent(long orderId, IbFxOrderIntent& out) const;
    std::vector<StrategySummary> GetStrategySummaries(std::time_t nowTs) const;
    bool SyncExternalPosition(const std::string& instrument, double netPosition, double markPrice, std::time_t nowTs);

    bool SaveState(const std::string& statePath) const;
    bool LoadState(const std::string& statePath);

private:
    struct ScalpExitSignals {
        double pnlBps = 0.0;
        std::time_t holdSeconds = 0;
        bool takeProfit = false;
        bool stopLoss = false;
        bool breakevenExit = false;
        bool trailingExit = false;
        bool reverseSignal = false;
        bool continuationExit = false;
        bool spreadStretch = false;
        bool hardTimeExit = false;
        bool softTimeExit = false;
    };

    struct ScalpEntryDecision {
        bool shouldEnter = false;
        bool costGated = false;
        bool warmupBlocked = false;
        double targetPos = 0.0;
        std::string reason;
        std::string orderType;
        double lmtPrice = 0.0;
    };

    struct OptimalStoppingSignal {
        bool ready = false;
        bool longSignal = false;
        bool shortSignal = false;
        double driftBpsPerSample = 0.0;
        double volatilityBpsPerSample = 0.0;
        double horizonDriftBps = 0.0;
        double horizonVolBps = 0.0;
        double entryBoundaryBps = 0.0;
        double confidence = 0.0;
    };

    struct Runtime {
        IbFxStrategyParams cfg;
        std::deque<double> samples;
        std::time_t lastSampleTs = 0;
        std::time_t lastSignalTs = 0;
        long long lastSignalMs = 0;
        std::chrono::steady_clock::time_point lastSampleSteady{};
        std::chrono::steady_clock::time_point lastSignalSteady{};
        bool hasLastSampleSteady = false;
        bool hasLastSignalSteady = false;
        std::time_t lastTradeTs = 0;
        std::time_t cooldownUntil = 0;
        std::time_t entryTs = 0;
        double peakFavorablePnlBps = 0.0;

        std::string positionIntent = "FLAT";

        double lastBid = 0.0;
        double lastAsk = 0.0;
        double lastMid = 0.0;
        double latestVolBps = 0.0;
        double lastEvalMid = 0.0;
        double lastEvalSpreadBps = 0.0;

        double netPosition = 0.0;
        double avgEntryPrice = 0.0;
        bool hasPending = false;
        long pendingOrderId = -1;

        long ordersSent = 0;
        long fills = 0;
        long rejects = 0;
        long cancels = 0;
        long closedTrades = 0;
        long winningTrades = 0;
        long long totalHoldSec = 0;
        std::time_t openPositionTs = 0;
        double realizedPnl = 0.0;
        double cycleRealizedPnl = 0.0;
        bool externalBaseline = false;
        std::string basisSource = "none";
        std::string positionSource = "none";
        std::string timeSource = "none";
        std::time_t strategyOwnsBaselineUntil = 0;
        std::string activeEntryReason;
        std::string activeEntryOrderType;
        std::string activeExitReason;
        std::string activeExitOrderType;
        std::time_t firstExitSignalTs = 0;
        std::time_t timeoutExitSignalTs = 0;
        std::string firstExitSignalReason;
        std::time_t pendingExitSignalTs = 0;
        std::string pendingExitReason;
        int consecutiveLosses = 0;
        std::time_t lossCooldownUntil = 0;
        std::time_t lastNoTradeReasonTs = 0;

        long long evalCount = 0;
        long long evalTotalUs = 0;
        long long evalMaxUs = 0;
        long long cycleTotalMs = 0;
        long long cycleMaxMs = 0;
        long long lastEvalUs = 0;
        long long lastCycleMs = 0;
        std::chrono::steady_clock::time_point lastEvalSteady{};
        bool hasLastEvalSteady = false;
    };

    Runtime* FindRuntime(const std::string& name);
    const Runtime* FindRuntime(const std::string& name) const;
    bool IsRuntimeBoundToOrder(long orderId) const;
    bool IsStaleReconciledResidualIntent(const IbFxOrderIntent& intent, std::time_t nowTs) const;
    void PruneStaleReconciledResidualIntents(std::time_t nowTs);

    static double ComputeSma(const std::deque<double>& samples, int window);
    bool HasManagedPositionBasis(const Runtime& rt) const;
    bool HasManagedPositionClock(const Runtime& rt) const;
    bool EstimateSlopeBps(const Runtime& rt, std::size_t endOffset, int window,
                          double& outSlopeBps, double& outNoiseBps, double& outRmsStepBps) const;
    void SeedExternalBasisIfNeeded(Runtime& rt, double price, std::time_t nowTs);
    bool EstimateOptimalStoppingSignal(const Runtime& rt, std::size_t endOffset,
                                       double spreadBps, double entryGateBps,
                                       OptimalStoppingSignal& out) const;
    ScalpExitSignals BuildScalpExitSignals(Runtime& rt, double price, std::time_t nowTs,
                                           double momentumBps, double momentumSnr,
                                           double entryGateBps, double exitGateBps,
                                           double spreadBps, double confirmSnr,
                                           double minEntrySnr, bool volCapOk,
                                           const OptimalStoppingSignal* optimalSignal) const;
    ScalpEntryDecision BuildScalpEntryDecision(const Runtime& rt, double price,
                                               bool hasQuote, bool spreadOk, bool volOk, bool volCapOk,
                                               double spreadBps, int effectiveConfirms,
                                               double entryGateBps, double confirmSnr,
                                               double minEntrySnr, double entryPadBps,
                                               bool forceMarketScalp) const;
    void EvaluateRuntime(Runtime& rt, double price, std::time_t nowTs);

private:
    Options m_options;
    std::vector<Runtime> m_runtimes;
    std::vector<IbFxOrderIntent> m_intents;
    std::vector<std::string> m_decisionAudits;
    std::unordered_map<long, IbFxOrderIntent> m_orderIntentById;
    int m_pendingStallSec = 5;
    int m_strategyOwnershipSec = 60;
};
