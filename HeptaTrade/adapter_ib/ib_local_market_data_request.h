#pragma once

#include "ib_api_wrapper.h"
#include <cmath>
#include <climits>
#include <deque>
#include <map>
#include <mutex>

// Wrapper-local evidence for actual CASH reqMktData/cancelMktData calls.
// A request's SDK return is not a live-price witness. Only valid native BID
// and ASK can end its pending phase. This class never sends or rearms anything.
// The wrapper still blocks every OPEN/RESERVED admission for a scoped error;
// localization only permits its existing Idle fault policy to remain local.
class IBLocalMarketDataRequestTracker {
public:
    enum {
        kFirstMarketRequestId = 1000000,
        kMaxActiveRequests = 128,
        kMaxRetiredRequests = 1024
    };

    void Reset(std::uint64_t epoch) {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_epoch = epoch;
        m_requests.clear();
        m_retired.clear();
        m_activeCount = 0;
        m_largestSentId = 0;
    }

    void Clear() { Reset(0); }

    // Call at the actual SDK send boundary, after the sender won admission.
    // IDs are allocated monotonically by the production subscription set.
    // Refusing reuse also prevents an old delayed cancel reply from borrowing
    // the identity of a new request after bounded retired history is pruned.
    bool BeginSent(std::uint64_t epoch, int requestId,
                   const IBContractLite& contract) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!epoch || epoch != m_epoch || requestId < kFirstMarketRequestId ||
            requestId <= m_largestSentId ||
            contract.secType != "CASH" || contract.symbol.empty() ||
            contract.currency.empty() || contract.exchange.empty() ||
            contract.symbol == contract.currency ||
            m_activeCount >= kMaxActiveRequests)
            return false;
        Entry entry;
        entry.contract = contract;
        m_requests.emplace(requestId, std::move(entry));
        ++m_activeCount;
        m_largestSentId = requestId;
        return true;
    }

    // Actual native watchlist TickPrice fields only. TickSize and other price
    // types must not be passed as a BID/ASK witness. Invalid current legs clear
    // that leg, while the fact that this request was once LIVE is permanent.
    bool ObserveTick(std::uint64_t epoch, int requestId,
                     const std::string& field, double price) {
        if (field != "1" && field != "2") return false;
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        if (found == m_requests.end() || !found->second.active) return false;
        Entry& entry = found->second;
        const bool valid = std::isfinite(price) && price > 0.0;
        if (field == "1") {
            entry.hasBid = valid;
            entry.bid = valid ? price : 0.0;
        } else {
            entry.hasAsk = valid;
            entry.ask = valid ? price : 0.0;
        }
        if (entry.hasBid && entry.hasAsk && entry.bid <= entry.ask)
            entry.hadCompletePair = true;
        return valid;
    }

    // Planning/intent alone is not a 300 witness. A rejected request must
    // never cause the wrapper to send cancelMktData for a nonexistent ticker.
    bool BeginCancel(std::uint64_t epoch, int requestId) {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        if (found == m_requests.end() || !found->second.active ||
            found->second.rejected || found->second.cancelRequested)
            return false;
        found->second.cancelRequested = true;
        return true;
    }

    // Call only when the exact cancelMktData SDK request was actually issued.
    // Retire authority immediately; preserve only its bounded diagnostic
    // identity so a delayed same-epoch 300 can be tied to this real cancellation.
    bool MarkCancelSent(std::uint64_t epoch, int requestId) {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        if (found == m_requests.end() || !found->second.cancelRequested ||
            found->second.cancelSent)
            return false;
        found->second.cancelSent = true;
        RetireLocked(requestId, found->second);
        return true;
    }

    bool CanLocalize(const IBEvent& event, std::uint64_t currentEpoch) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        return CanLocalizeLocked(event, currentEpoch);
    }

    // Preserve the original Error event untouched for OMS/risk diagnostics.
    // 354 removes only a proven pending request; it creates no cancel witness.
    bool ConsumeError(const IBEvent& event, std::uint64_t currentEpoch) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!CanLocalizeLocked(event, currentEpoch)) return false;
        if (event.key == "354") {
            const int requestId = static_cast<int>(event.id);
            Entry& entry = m_requests.find(requestId)->second;
            entry.rejected = true;
            RetireLocked(requestId, entry);
        }
        return true;
    }

    bool GetContract(std::uint64_t epoch, int requestId,
                     IBContractLite& out) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        if (found == m_requests.end()) return false;
        out = found->second.contract;
        return true;
    }

    // Stamp provenance only from the local send/cancel table, never from the
    // localized Broker description. The untouched code/message remain in the
    // same Error event for the normal OMS and risk path.
    bool CorroborateError(IBEvent& event, std::uint64_t currentEpoch,
                          int clientId) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (clientId < 0 || !CanLocalizeLocked(event, currentEpoch)) return false;
        const int requestId = static_cast<int>(event.id);
        Entry& entry = m_requests.find(requestId)->second;
        IBEvent annotated = event;
        annotated.localMarketDataRequestError = true;
        annotated.requestId = requestId;
        annotated.brokerClientId = clientId;
        annotated.contract = entry.contract;
        if (event.key == "354") {
            entry.rejected = true;
            RetireLocked(requestId, entry);
        }
        event = std::move(annotated);
        return true;
    }

    bool KnownSent(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        return FindCurrent(epoch, requestId) != m_requests.end();
    }
    bool CancelRequested(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        return found != m_requests.end() && found->second.cancelRequested;
    }
    bool CancelRecorded(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        return found != m_requests.end() && found->second.cancelSent;
    }
    bool Rejected(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        return found != m_requests.end() && found->second.rejected;
    }
    bool HasCompletePair(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        return found != m_requests.end() && found->second.hadCompletePair;
    }
    bool Pending(std::uint64_t epoch, int requestId) const {
        std::lock_guard<std::mutex> lock(m_mutex);
        const auto found = FindCurrent(epoch, requestId);
        return found != m_requests.end() && found->second.active &&
            !found->second.hadCompletePair && !found->second.rejected;
    }
    std::size_t ActiveCount() const {
        std::lock_guard<std::mutex> lock(m_mutex);
        return m_activeCount;
    }
    std::size_t RetiredCount() const {
        std::lock_guard<std::mutex> lock(m_mutex);
        return m_retired.size();
    }

private:
    struct Entry {
        IBContractLite contract;
        bool active = true;
        bool rejected = false;
        bool cancelRequested = false;
        bool cancelSent = false;
        bool hasBid = false;
        bool hasAsk = false;
        bool hadCompletePair = false;
        double bid = 0.0;
        double ask = 0.0;
    };
    typedef std::map<int, Entry> RequestMap;

    RequestMap::iterator FindCurrent(std::uint64_t epoch, int requestId) {
        return epoch && epoch == m_epoch ? m_requests.find(requestId) :
            m_requests.end();
    }
    RequestMap::const_iterator FindCurrent(std::uint64_t epoch,
                                           int requestId) const {
        return epoch && epoch == m_epoch ? m_requests.find(requestId) :
            m_requests.end();
    }

    bool CanLocalizeLocked(const IBEvent& event,
                           std::uint64_t currentEpoch) const {
        if (event.type != IBEventType::Error || !currentEpoch ||
            currentEpoch != m_epoch || event.connectionEpoch != currentEpoch ||
            event.id < kFirstMarketRequestId || event.id > INT_MAX ||
            !event.account.empty() || !event.order.orderRef.empty() ||
            !event.advancedOrderRejectJson.empty() || event.requestId != 0 ||
            event.brokerClientId != -1 || !event.order.action.empty() ||
            !event.order.orderType.empty() || event.order.totalQuantity != 0 ||
            event.order.cashQuantity != 0 ||
            !event.contract.symbol.empty() || !event.contract.secType.empty() ||
            !event.contract.exchange.empty() || !event.contract.currency.empty())
            return false;
        const auto found = m_requests.find(static_cast<int>(event.id));
        if (found == m_requests.end()) return false;
        const Entry& entry = found->second;
        if (event.key == "354")
            return entry.active && !entry.hadCompletePair && !entry.rejected;
        if (event.key == "300")
            return entry.cancelSent; // A 354 alone can never supply this proof.
        return false;
    }

    void RetireLocked(int requestId, Entry& entry) {
        if (!entry.active) return;
        // Allocate the diagnostic queue record before removing active
        // authority; failed bookkeeping must not claim a completed retirement.
        m_retired.push_back(requestId);
        entry.active = false;
        --m_activeCount;
        while (m_retired.size() > kMaxRetiredRequests) {
            const int oldest = m_retired.front();
            m_retired.pop_front();
            m_requests.erase(oldest);
        }
    }

    mutable std::mutex m_mutex;
    std::uint64_t m_epoch = 0;
    int m_largestSentId = 0;
    std::size_t m_activeCount = 0;
    RequestMap m_requests;
    std::deque<int> m_retired;
};
