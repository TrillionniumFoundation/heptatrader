#pragma once

#include <string>
#include <vector>
#include <unordered_set>

#include "adapter_ib/ib_api_wrapper.h"

struct OpenClaw0DteIntent {
    std::string eventId;
    std::string reqId;
    std::string traceId;
    std::string source;
    std::string strategy;
    std::string instrument;
    std::string side;
    std::string reason;
    long long tsMs = 0;
    double confidence = 0.0;
    double premiumAtRiskUsd = 0.0;
    bool reduceOnly = false;
    IBContractLite contract;
    IBOrderLite order;
};

struct OpenClaw0DteBridgeReject {
    std::string riskCode;
    std::string detail;
    std::string eventId;
    std::string rawLine;
};

class OpenClaw0DteBridgeConsumer {
public:
    struct Options {
        std::string path = "runtime-logs/openclaw-0dte-intents.jsonl";
        std::string cursorPath;
        std::string consumedEventIdsPath;
        int maxBatch = 8;
        long long maxSignalAgeMs = 120000;
        double maxQty = 1.0;
        double maxPremiumUsd = 250.0;
        bool allowSell = false;
        std::string entryWindowUtc;
        std::string noNewEntriesAfterUtc;
    };

    void Configure(const Options& options);
    bool Enabled() const { return !m_options.path.empty(); }
    const std::string& Path() const { return m_options.path; }
    std::vector<OpenClaw0DteIntent> Poll(std::vector<OpenClaw0DteBridgeReject>* rejects = nullptr);

private:
    void LoadCursor();
    void SaveCursor() const;
    void LoadConsumedEventIds();
    void AppendConsumedEventId(const std::string& eventId) const;
    bool ParseIntentLine(const std::string& line, OpenClaw0DteIntent& out, OpenClaw0DteBridgeReject& reject);
    bool ValidateIntent(const std::string& line, OpenClaw0DteIntent& out, OpenClaw0DteBridgeReject& reject);

private:
    Options m_options;
    long long m_offset = 0;
    std::unordered_set<std::string> m_seenEventIds;
};
