#pragma once

#include "oms_latency_observation.h"
#include "execution/ib_economic_evidence.h"

#include <cstdint>
#include <cmath>
#include <functional>
#include <mutex>
#include <string>
#include <vector>
#include <chrono>
#include <deque>
#include <thread>
#include <condition_variable>

// Optional additive v4 native STOP child witness. Present only on an
// exact owner-bound, complete SDK openOrder callback. The witness is NOT a
// working protective guarantee without independently complete broker orders,
// exact physical position and no-new-entry fence.
struct IbBracketCallbackEvidence {
    int version = 0;
    long contractId = 0;
    long parentOrderId = 0;
    long permanentOrderId = 0;
    int transmit = 0;
    int ocaType = 0;
    int triggerMethod = 0;
    std::string orderRef;
    std::string ocaGroup;
    std::string timeInForce;
    double quantity = 0.0;
    double stopAuxPrice = 0.0;

    static bool CanonicalH1Ref(const std::string& ref) {
        // Unpadded base64url of exactly 32 bytes has 43 characters. The
        // last sextet contains two unused zero bits; reject noncanonical
        // aliases rather than treating prefix-only H1 as valid evidence.
        static const std::string alphabet=
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        if (ref.size()!=45 || ref.compare(0,2,"H1")!=0) return false;
        for(std::size_t i=2;i<ref.size();++i)
            if(alphabet.find(ref[i])==std::string::npos) return false;
        const auto last=alphabet.find(ref.back());
        return last!=std::string::npos && last%4==0;
    }
    bool ValidShape() const {
        if ((version!=1 && version!=2) || contractId!=12087792 ||
            parentOrderId<=0 || permanentOrderId<=0 || transmit!=1 ||
            ocaType!=1 || triggerMethod!=0 || timeInForce!="GTC" ||
            !std::isfinite(quantity) || quantity<=0 || quantity>100000 ||
            std::floor(quantity)!=quantity ||
            !std::isfinite(stopAuxPrice) || stopAuxPrice<=0) return false;
        if(version==1) {
            // Preserve the exact legacy research-only v1 shape.
            static const std::string prefix="HEPTA-STOP-";
            if(orderRef.compare(0,prefix.size(),prefix)!=0 ||
               orderRef.size()<=prefix.size() || orderRef.size()>45 ||
               ocaGroup!="HEPTA-FX-OCA-"+orderRef.substr(prefix.size()))
                return false;
            for(std::size_t i=prefix.size();i<orderRef.size();++i) {
                const char c=orderRef[i];
                if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||
                     (c>='0'&&c<='9')||c=='-'||c=='_'||c=='.'))
                    return false;
            }
        } else {
            // v2 is H1 field SHAPE ONLY; an actual candidate requires a
            // separate, exact original-OMS + SDK openOrder projection.
            if(!CanonicalH1Ref(orderRef) || !CanonicalH1Ref(ocaGroup) ||
               orderRef==ocaGroup) return false;
        }
        const double ticks=stopAuxPrice/0.00005;
        return std::isfinite(ticks) &&
               std::fabs(ticks-std::round(ticks))<1.e-7;
    }
};

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
    int brokerClientId = -1; // optional v4 evidence; -1 means legacy/unknown
    long long brokerRequestId = 0;
    int brokerErrorCode = 0;
    std::string brokerMessage;
    std::string brokerAdvancedOrderRejectJson;
    std::string brokerWhyHeld;
    std::string brokerExecutionId;
    double brokerRemainingQuantity = 0.0;
    double brokerMarketCapPrice = 0.0;
    IbEconomicEvidence brokerEconomics;
    IbBracketCallbackEvidence brokerBracket;

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
    // Capacity is a recovery admission budget, never permission to truncate.
    std::size_t replayMaxBytes = 0;
    std::size_t replayMaxRecords = 0;
    std::size_t replayMaxRecordBytes = 0;
    std::size_t replayObservedBytes = 0;
    std::size_t replayValidatedRecords = 0;
    std::string replayReasonCode;
    OmsLatencySummary appendLatency;
    OmsLatencySummary dataSyncLatency;
    OmsLatencySummary replayValidationLatency;
    // Online written-ledger capacity; false is unknown, never an observed zero.
    bool capacityKnown = false;
    std::uint64_t currentBytes = 0;
    std::uint64_t currentRecords = 0;
    std::uint64_t storageBytes = 0; // physical bytes, never charged as decoded recovery budget
    bool gzipStorage = false;
    std::size_t pendingBytes = 0;
    std::size_t maxPendingBytes = 0;
    std::size_t maxPendingRecords = 0;
    std::uint64_t queueCapacityRejections = 0;
};

class OmsJournal {
public:
    static const int kSchemaVersion = 4;

    OmsJournal() = default;
    ~OmsJournal() noexcept;

    bool Init(const std::string& path);
    bool Append(const OmsJournalEvent& evt);
    // Mandatory pre-broker-send durability barrier. Unlike ordinary Append,
    // this ALWAYS flushes the existing queue and fsyncs the exact critical
    // marker, even with HEPTA_OMS_SYNC_CRITICAL=0 or async writes enabled.
    // A failed barrier never authorizes a Broker request.
    bool AppendDurableCritical(const OmsJournalEvent& evt);
    // Two ordered critical records with no intervening external effect share
    // one durability barrier. This is not an atomic two-record disk format:
    // a failed/crashed call may leave a prefix, which ordinary replay retains.
    // Success means both records were synced and their path stayed pinned.
    enum class DurablePairResult { Committed, FirstFailed, SecondFailed };
    DurablePairResult AppendDurablePair(const OmsJournalEvent& first,
                                        const OmsJournalEvent& second);
    int Replay(const std::function<void(const OmsJournalEvent&)>& onEvent) const;
    std::string GetPath() const;
    OmsJournalHealthSnapshot GetHealthSnapshot() const;

    static long long NowEpochMs();
    // Shared strict line parser for native checkpoint/tail recovery. This does
    // not introduce another schema or a permissive reader: generation recovery
    // deliberately reuses the exact production journal parser.
    static bool ParseJsonLine(const std::string& line, OmsJournalEvent& out);

    // Called only after OmsGenerationStore has verified a selected generation,
    // its hot replay and the exact active tail. The bytes/records are the next
    // restart working set, not total immutable historical storage. This lets
    // existing new-entry headroom apply to generation-backed incremental
    // recovery instead of treating every pre-cut byte as still hot.
    void AdoptValidatedIncrementalRecoveryCapacity(
        std::uint64_t decodedBytes,
        std::uint64_t records);

private:
    static bool IsCriticalEventType(const std::string& eventType);
    bool EncodeValidatedEventLocked(const OmsJournalEvent& event,
                                    std::string& line) const;
    bool FlushBufferedLocked();
    bool QueueLineLocked(std::string line, bool asynchronous);
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

private:
    std::string m_path;
    int m_fd = -1;
    bool m_writePoisoned = false;
    mutable std::mutex m_mtx;
    std::size_t m_replayMaxBytes = 64U * 1024U * 1024U;
    std::size_t m_replayMaxRecords = 65536U;
    std::size_t m_replayMaxRecordBytes = 256U * 1024U;
    std::size_t m_replayObservedBytes = 0;
    std::size_t m_replayValidatedRecords = 0;
    std::string m_replayReasonCode;
    OmsLatencySummary m_appendLatency;
    OmsLatencySummary m_dataSyncLatency;
    OmsLatencySummary m_replayValidationLatency;
    bool m_capacityKnown = false;
    std::uint64_t m_capacityBytes = 0;
    std::uint64_t m_capacityRecords = 0;
    std::uint64_t m_storageBytes = 0;
    bool m_gzipStorage = false;

    std::size_t m_pendingBytes = 0;
    std::size_t m_maxPendingBytes = 8U * 1024U * 1024U;
    std::size_t m_maxPendingRecords = 8192U;
    std::uint64_t m_queueCapacityRejections = 0;
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
