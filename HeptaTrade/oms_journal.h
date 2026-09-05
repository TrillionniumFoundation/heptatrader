#pragma once

#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <vector>
#include <chrono>
#include <deque>
#include <thread>
#include <condition_variable>

struct OmsJournalEvent {
    // v4 schema. Keep old keys/fields for backward compatibility.
    int schemaVersion = 4;
    std::string eventType;      // order_intent/place_send_attempt/cancel_send_attempt/place_sent/cancel/...
    long long tsMs = 0;
    long orderId = -1;
    std::string clientReqId;    // legacy alias of req_id
    std::string instrument;
    std::string side;
    double qty = 0.0;
    double price = 0.0;
    std::string status;
    std::string reason;
    std::string source;

    std::string traceId;
    std::string reqId;
    std::string riskCode;
    std::string venue;
    std::string strategy;
    std::string account;
    std::string eventId;        // optional idempotency key
    std::string executionDomain;
    std::string requestHash;    // canonical execution request hash, when applicable
    std::string venueCorrelationId; // stable service-owned venue correlation

    // Optional broker callback evidence. These are additive v4 fields so old
    // journal readers continue to replay existing command records while the
    // PAPER runtime can durably retain the broker's complete diagnostic path.
    std::string brokerCallbackType;
    std::string brokerServiceEpoch;
    std::uint64_t brokerConnectionEpoch = 0;
    long long brokerRequestId = 0;
    int brokerErrorCode = 0;
    std::string brokerMessage;
    std::string brokerAdvancedOrderRejectJson;
    std::string brokerWhyHeld;
    std::string brokerExecutionId;
    double brokerRemainingQuantity = 0.0;
    double brokerMarketCapPrice = 0.0;

    std::string rawLine;
};

struct OmsJournalHealthSnapshot {
    bool asyncEnabled = false;
    bool syncCritical = true;
    std::size_t queueDepth = 0;
    std::size_t bufferedDepth = 0;
    long long enqueuedTotal = 0;
    long long flushedTotal = 0;
    long long writeFailTotal = 0;
    long long criticalSyncWrites = 0;
    long long criticalAsyncWrites = 0;
    long long durableSyncWrites = 0;
    long long durableSyncFailures = 0;
    long long maxQueueDepth = 0;
    long long lastFlushMs = 0;
    bool writePoisoned = false;
};

class OmsJournal {
public:
    static const int kSchemaVersion = 4;
    // JSON bytes excluding the LF delimiter; shared by Append and Replay.
    static constexpr std::size_t kMaximumRecordBytes = 1024 * 1024;

    OmsJournal() = default;
    ~OmsJournal() noexcept;

    bool Init(const std::string& path);
    bool Append(const OmsJournalEvent& evt);
    int Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const;
    std::string GetPath() const;
    OmsJournalHealthSnapshot GetHealthSnapshot() const;

    static long long NowEpochMs();

private:
    static bool IsCriticalEventType(const std::string& eventType);
    bool FlushBufferedLocked();
    bool FlushQueuedNoLock();
    bool WriteLineDirect(const std::string& line);
    bool WriteLineToPinnedFileLocked(const std::string& line, bool durable);
    bool ValidatePinnedPathLocked();
    bool OpenPinnedFileLocked(const std::string& path);
    bool ClosePinnedFileLocked();
    void WorkerLoop();

private:
    static std::string EscapeJson(const std::string& s);
    static std::string BuildJsonLine(const OmsJournalEvent& evt);
    static bool ParseJsonLine(const std::string& line, OmsJournalEvent& out);

private:
    std::string m_path;
    int m_fd = -1;
    bool m_writePoisoned = false;
    mutable std::mutex m_mtx;

    std::vector<std::string> m_bufferedLines;
    std::deque<std::string> m_asyncQueue;
    std::size_t m_batchSize = 1;
    long long m_flushIntervalMs = 0;
    long long m_lastFlushMs = 0;
    bool m_asyncEnabled = false;
    bool m_syncCritical = true;
    bool m_criticalFlushQueued = false;
    bool m_stopWorker = false;
    std::thread m_worker;
    std::condition_variable m_cv;

    long long m_enqueuedTotal = 0;
    long long m_flushedTotal = 0;
    long long m_writeFailTotal = 0;
    long long m_criticalSyncWrites = 0;
    long long m_criticalAsyncWrites = 0;
    long long m_durableSyncWrites = 0;
    long long m_durableSyncFailures = 0;
    long long m_maxQueueDepth = 0;
};
