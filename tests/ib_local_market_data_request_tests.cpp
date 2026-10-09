#include "adapter_ib/ib_local_market_data_request.h"
#include "adapter_ib/ib_admission_event_policy.h"
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
void Need(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
IBContractLite Cash() {
    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    return contract;
}
IBEvent Error(int id, const std::string& code, std::uint64_t epoch = 7) {
    IBEvent event;
    event.type = IBEventType::Error;
    event.id = id;
    event.key = code;
    event.connectionEpoch = epoch;
    event.value = "Broker diagnostics remain intact; text is not proof";
    return event;
}
struct Fixture {
    IBLocalMarketDataRequestTracker tracker;
    const int id = 1000287;
    Fixture() {
        tracker.Reset(7);
        Need(tracker.BeginSent(7, id, Cash()), "actual local CASH send registered");
    }
};

void TestObservedPending354AndNoInventedCancel() {
    Fixture f;
    auto rejected = Error(f.id, "354");
    Need(f.tracker.Pending(7, f.id) && f.tracker.CanLocalize(rejected, 7),
         "known actual pending request can localize the observed 354");
    Need(f.tracker.ConsumeError(rejected, 7), "scoped 354 consumed");
    Need(rejected.key == "354" && rejected.id == f.id &&
         !rejected.value.empty(), "original Broker event is not rewritten");
    Need(f.tracker.Rejected(7, f.id) && !f.tracker.Pending(7, f.id) &&
         f.tracker.KnownSent(7, f.id) && f.tracker.ActiveCount() == 0,
         "rejection retires the pending request but preserves bounded identity");
    Need(!f.tracker.BeginCancel(7, f.id) &&
         !f.tracker.MarkCancelSent(7, f.id) &&
         !f.tracker.CancelRecorded(7, f.id),
         "rejected nonexistent ticker must not be cancelled");
    Need(!f.tracker.CanLocalize(Error(f.id, "300"), 7),
         "the observed 354 must not fabricate a cancellation witness for 300");
    Need(!f.tracker.ConsumeError(rejected, 7), "retired request is not pending again");
    Need(!f.tracker.ObserveTick(7, f.id, "1", 1.1),
         "late rejected-request tick cannot restore live authority");
}

void TestProvenanceComesOnlyFromRecordedRequest() {
    Fixture f;
    IBContractLite contract;
    Need(f.tracker.GetContract(7, f.id, contract) && contract.symbol == "EUR" &&
         !f.tracker.GetContract(8, f.id, contract), "contract lookup bound to current local send");
    auto error = Error(f.id, "354");
    const std::string originalMessage = error.value;
    Need(f.tracker.CorroborateError(error, 7, 701), "pending error corroborated from exact table");
    Need(error.localMarketDataRequestError && error.requestId == f.id &&
         error.brokerClientId == 701 && error.contract.secType == "CASH" &&
         error.contract.symbol == "EUR" && error.key == "354" &&
         error.value == originalMessage && f.tracker.Rejected(7, f.id),
         "local provenance stamped while exact Broker rejection remains intact");
    auto unknown = Error(f.id + 1, "354");
    Need(!f.tracker.CorroborateError(unknown, 7, 701) &&
         !unknown.localMarketDataRequestError && unknown.requestId == 0 &&
         unknown.brokerClientId == -1 && unknown.contract.secType.empty(),
         "unknown request cannot gain local provenance");
    Fixture cancelled;
    Need(cancelled.tracker.BeginCancel(7, cancelled.id) &&
         cancelled.tracker.MarkCancelSent(7, cancelled.id), "actual cancellation established");
    auto cancel = Error(cancelled.id, "300");
    Need(cancelled.tracker.CorroborateError(cancel, 7, 701) &&
         cancel.localMarketDataRequestError && cancel.requestId == cancelled.id &&
         cancel.contract.secType == "CASH", "retired exact cancel can still stamp delayed300");
}

void TestLocalErrorStillBlocksActiveAdmission() {
    for (int phase = 0; phase != 3; ++phase) {
        Fixture f;
        auto error = Error(f.id, "354");
        Need(f.tracker.CorroborateError(error, 7, 701) &&
             IsIbCorroboratedCashMarketDataRequestError(error) &&
             IbAdmissionBlockingEvent(error), "exact local error remains generically blocking");
        IBMarketDataAdmissionState admission;
        std::uint64_t generation = 0;
        if (phase >= 1) Need(admission.Begin(generation), "actual admission OPEN");
        if (phase == 2) Need(admission.TryReserve(generation), "actual admission RESERVED");
        admission.ObserveBlockingCallback(false);
        Need(admission.IsFailed() == (phase != 0),
             "local error exempts only Idle; OPEN and RESERVED still fail");
    }
}

void TestRealCancelOnlyAndDelayed300() {
    Fixture f;
    const auto cancelled = Error(f.id, "300");
    Need(!f.tracker.CanLocalize(cancelled, 7) &&
         !f.tracker.MarkCancelSent(7, f.id), "known send alone cannot prove cancel");
    Need(f.tracker.BeginCancel(7, f.id), "exact known request cancellation planned");
    Need(f.tracker.CancelRequested(7, f.id) &&
         !f.tracker.CancelRecorded(7, f.id) &&
         !f.tracker.CanLocalize(cancelled, 7), "cancel intent is not SDK send evidence");
    Need(!f.tracker.BeginCancel(7, f.id), "duplicate cancel not dispatched");
    Need(f.tracker.MarkCancelSent(7, f.id), "actual exact SDK cancellation recorded");
    Need(f.tracker.CancelRecorded(7, f.id) &&
         !f.tracker.Pending(7, f.id) && f.tracker.CanLocalize(cancelled, 7),
         "actual cancel witness survives immediate request retirement");
    Need(f.tracker.ConsumeError(cancelled, 7) &&
         f.tracker.ConsumeError(cancelled, 7), "delayed duplicate 300 remains diagnostic only");
    Need(!f.tracker.MarkCancelSent(7, f.id) &&
         !f.tracker.BeginSent(7, f.id, Cash()), "numeric request ID cannot be reused");
    // A rejection racing the real SDK cancel return must not erase that
    // independently established cancel intent; only MarkCancelSent proves it.
    Fixture raced;
    Need(raced.tracker.BeginCancel(7, raced.id), "racing cancellation intent");
    Need(raced.tracker.ConsumeError(Error(raced.id, "354"), 7), "pending rejection raced cancel");
    Need(!raced.tracker.CanLocalize(Error(raced.id, "300"), 7), "still no actual cancellation");
    Need(raced.tracker.MarkCancelSent(7, raced.id) &&
         raced.tracker.CanLocalize(Error(raced.id, "300"), 7),
         "completed actual cancellation remains distinct from prior rejection");
}

void TestValidPairEndsPendingPermanently() {
    Fixture f;
    Need(f.tracker.ObserveTick(7, f.id, "1", 1.1) &&
         f.tracker.CanLocalize(Error(f.id, "354"), 7), "one genuine leg is still pending");
    Need(f.tracker.ObserveTick(7, f.id, "2", 1.1001) &&
         f.tracker.HasCompletePair(7, f.id) && !f.tracker.Pending(7, f.id),
         "actual complete noncrossed pair becomes LIVE");
    Need(!f.tracker.CanLocalize(Error(f.id, "354"), 7) &&
         !f.tracker.ConsumeError(Error(f.id, "354"), 7), "LIVE request rejection remains global");
    Need(!f.tracker.ObserveTick(7, f.id, "1", 0) &&
         !f.tracker.CanLocalize(Error(f.id, "354"), 7),
         "later invalid or stale legs cannot revert LIVE to exempt pending");
    Fixture crossed;
    crossed.tracker.ObserveTick(7, crossed.id, "1", 1.2);
    crossed.tracker.ObserveTick(7, crossed.id, "2", 1.1);
    Need(!crossed.tracker.HasCompletePair(7, crossed.id), "crossed legs do not prove LIVE");
    crossed.tracker.ObserveTick(7, crossed.id, "2", 1.3);
    Need(crossed.tracker.HasCompletePair(7, crossed.id), "later real valid pair proves LIVE");
    Fixture invalid;
    invalid.tracker.ObserveTick(7, invalid.id, "1", 1.1);
    invalid.tracker.ObserveTick(7, invalid.id, "1", std::numeric_limits<double>::quiet_NaN());
    invalid.tracker.ObserveTick(7, invalid.id, "2", 1.2);
    Need(!invalid.tracker.HasCompletePair(7, invalid.id), "invalid latest bid cannot borrow prior leg");
    Need(!invalid.tracker.ObserveTick(7, invalid.id, "0", 1.1) &&
         !invalid.tracker.ObserveTick(7, invalid.id, "4", 1.1) &&
         !invalid.tracker.ObserveTick(7, invalid.id, "66", 1.1),
         "TickSize, LAST and delayed BID cannot provide live price authority");
}

void TestAllForeignAndOrderScopesRemainGlobal() {
    const std::vector<std::function<void(IBEvent&)>> mutations = {
        [](IBEvent& e) { e.id = -1; }, [](IBEvent& e) { e.id = 398; },
        [](IBEvent& e) { e.id = 1000288; }, [](IBEvent& e) { e.id = static_cast<long long>(INT_MAX) + 1; },
        [](IBEvent& e) { e.connectionEpoch = 0; }, [](IBEvent& e) { e.connectionEpoch = 6; },
        [](IBEvent& e) { e.connectionEpoch = 8; }, [](IBEvent& e) { e.account = "DU_ANY"; },
        [](IBEvent& e) { e.order.orderRef = "hepta-order-reference"; },
        [](IBEvent& e) { e.advancedOrderRejectJson = "{}"; },
        [](IBEvent& e) { e.brokerClientId = 702; }, [](IBEvent& e) { e.requestId = 17; },
        [](IBEvent& e) { e.order.action = "SELL"; },
        [](IBEvent& e) { e.order.totalQuantity = 1; },
        [](IBEvent& e) { e.contract.secType = "STK"; },
        [](IBEvent& e) { e.contract.symbol = "GBP"; },
        [](IBEvent& e) { e.key += "0"; }, [](IBEvent& e) { e.type = IBEventType::TickPrice; }
    };
    for (const auto& mutate : mutations) {
        Fixture f;
        auto denied = Error(f.id, "354"); mutate(denied);
        Need(!f.tracker.CanLocalize(denied, 7) &&
             !f.tracker.ConsumeError(denied, 7) && f.tracker.Pending(7, f.id),
             "untrusted scoped354 stays global and cannot retire real pending request");
        Need(f.tracker.BeginCancel(7, f.id) && f.tracker.MarkCancelSent(7, f.id), "real cancel setup");
        auto cancel = Error(f.id, "300"); mutate(cancel);
        Need(!f.tracker.CanLocalize(cancel, 7), "untrusted scoped300 stays global");
    }
    Fixture f;
    Need(!f.tracker.CanLocalize(Error(f.id, "354"), 8), "caller's future epoch cannot borrow current entry");
    for (const std::string code : {"10197", "326", "202", "201", "1100", "354x", " 354"})
        Need(!f.tracker.CanLocalize(Error(f.id, code), 7), "all unrelated broker errors retain original policy");
}

void TestBoundedRetirementAndReconnect() {
    IBLocalMarketDataRequestTracker tracker;
    Need(!tracker.BeginSent(7, 1000000, Cash()), "actual connection required");
    tracker.Reset(7);
    Need(!tracker.BeginSent(7, 398, Cash()), "order ID range cannot be market data proof");
    IBContractLite stock = Cash(); stock.secType = "STK";
    Need(!tracker.BeginSent(7, 1000000, stock), "non-CASH not covered by narrow witness");
    const int first = 1000000;
    for (int i = 0; i <= IBLocalMarketDataRequestTracker::kMaxRetiredRequests; ++i) {
        const int id = first + i;
        Need(tracker.BeginSent(7, id, Cash()) && tracker.BeginCancel(7, id) &&
             tracker.MarkCancelSent(7, id), "actual sends/cancels retire individually");
    }
    Need(tracker.ActiveCount() == 0 &&
         tracker.RetiredCount() == IBLocalMarketDataRequestTracker::kMaxRetiredRequests,
         "retired storage remains bounded");
    Need(!tracker.KnownSent(7, first) && !tracker.CanLocalize(Error(first, "300"), 7),
         "evicted old cancellation loses diagnostic exemption");
    const int last = first + IBLocalMarketDataRequestTracker::kMaxRetiredRequests;
    Need(tracker.KnownSent(7, last) && tracker.CanLocalize(Error(last, "300"), 7),
         "recent exact cancellation still corroborated");
    Need(!tracker.BeginSent(7, first, Cash()), "eviction cannot permit numeric-ID reuse");
    tracker.Reset(8);
    Need(tracker.ActiveCount() == 0 && tracker.RetiredCount() == 0 &&
         !tracker.CanLocalize(Error(last, "300"), 8), "new connection cannot borrow old witness");
    Need(tracker.BeginSent(8, first, Cash()), "new epoch establishes independent request identity");
    Need(!tracker.ObserveTick(7, first, "1", 1.1) &&
         !tracker.ObserveTick(9, first, "2", 1.2), "old/future ticks cannot complete current pair");
    tracker.Clear();
    Need(!tracker.KnownSent(8, first) && !tracker.BeginSent(8, first + 1, Cash()),
         "disconnect clears all evidence and requires new registered epoch");
    tracker.Reset(9);
    for (int i = 0; i < IBLocalMarketDataRequestTracker::kMaxActiveRequests; ++i)
        Need(tracker.BeginSent(9, first + i, Cash()), "active bound setup");
    Need(!tracker.BeginSent(9, first + 1000, Cash()) &&
         tracker.ActiveCount() == IBLocalMarketDataRequestTracker::kMaxActiveRequests,
         "active capacity never silently evicts a live or pending request");
}
}

int main() {
    try {
        TestObservedPending354AndNoInventedCancel();
        TestProvenanceComesOnlyFromRecordedRequest();
        TestLocalErrorStillBlocksActiveAdmission();
        TestRealCancelOnlyAndDelayed300();
        TestValidPairEndsPendingPermanently();
        TestAllForeignAndOrderScopesRemainGlobal();
        TestBoundedRetirementAndReconnect();
        std::cout << "PASS exact pending354/cancel300 evidence; LIVE, foreign and unknown failures retained; no SDK calls\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
