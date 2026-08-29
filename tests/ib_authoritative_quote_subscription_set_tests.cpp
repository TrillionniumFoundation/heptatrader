#include "state/ib_authoritative_quote_subscription_set.h"
#include "state/ib_contract_identity.h"

#include <cassert>
#include <map>
#include <string>

namespace {

IBContractLite FxContract()
{
    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    return contract;
}

IBContractLite OptionContract()
{
    IBContractLite contract;
    contract.symbol = "SPY";
    contract.secType = "OPT";
    contract.exchange = "SMART";
    contract.currency = "USD";
    contract.localSymbol = "SPY  260821C00550000";
    return contract;
}

IBContractLite FutureContract()
{
    IBContractLite contract;
    contract.symbol = "CL";
    contract.secType = "FUT";
    contract.exchange = "NYMEX";
    contract.currency = "USD";
    contract.lastTradeDateOrContractMonth = "202609";
    contract.tradingClass = "CL";
    return contract;
}

IBEvent Tick(std::uint64_t epoch, int requestId, const std::string& field, double price)
{
    IBEvent event;
    event.type = IBEventType::TickPrice;
    event.connectionEpoch = epoch;
    event.id = requestId;
    event.key = field;
    event.number = price;
    return event;
}

int RequestIdFor(const IBAuthoritativeQuoteSubscriptionPlan& plan,
                 const std::string& instrument)
{
    for (std::size_t i = 0; i < plan.subscriptions.size(); ++i)
        if (plan.subscriptions[i].instrument == instrument)
            return plan.subscriptions[i].requestId;
    return 0;
}

}

int main()
{
    AuthoritativeTradingSnapshotStore store;
    IBAuthoritativeQuoteSubscriptionSet subscriptions(store, 2001);
    const IBContractLite fx = FxContract();
    const IBContractLite option = OptionContract();
    const std::string fxInstrument = BuildIBAuthoritativeInstrumentIdentity(fx);
    const std::string optionInstrument = BuildIBAuthoritativeInstrumentIdentity(option);
    std::map<std::string, IBContractLite> contracts;
    contracts[fxInstrument] = fx;
    contracts[optionInstrument] = option;
    std::string reason;
    assert(subscriptions.Configure(contracts, fxInstrument, reason));
    assert(reason.empty());
    assert(subscriptions.DesiredCount() == 2);

    const IBAuthoritativeQuoteSubscriptionPlan first =
        subscriptions.BeginCycle(7, 1, 1000);
    assert(first.accepted);
    assert(first.subscriptions.size() == 2);
    assert(first.cancelRequestIds.empty());
    const int fxRequest = RequestIdFor(first, fxInstrument);
    const int optionRequest = RequestIdFor(first, optionInstrument);
    assert(fxRequest > 0 && optionRequest > 0 && fxRequest != optionRequest);
    assert(subscriptions.RecordDispatchResult(1, fxRequest, true));
    assert(subscriptions.RecordDispatchResult(1, optionRequest, true));
    IBAuthoritativeQuoteSubscriptionHealth health = subscriptions.GetHealth();
    assert(health.desiredRevision == 1);
    assert(health.generation == 1);
    assert(health.contracts.at(fxInstrument).active);
    assert(health.contracts.at(fxInstrument).dispatchAccepted);
    assert(subscriptions.ConsumeTick(Tick(7, fxRequest, "1", 1.1000), 1001).status ==
        IBAuthoritativeQuoteConsumeStatus::Applied);
    assert(subscriptions.ConsumeTick(Tick(7, fxRequest, "2", 1.1002), 1002).status ==
        IBAuthoritativeQuoteConsumeStatus::Applied);
    assert(!subscriptions.IsComplete());
    assert(subscriptions.ConsumeTick(Tick(7, optionRequest, "1", 4.10), 1003).status ==
        IBAuthoritativeQuoteConsumeStatus::Applied);
    const IBAuthoritativeQuoteConsumeResult completed =
        subscriptions.ConsumeTick(Tick(7, optionRequest, "2", 4.20), 1004);
    assert(completed.completedNow);
    assert(completed.cycleComplete);
    assert(subscriptions.IsComplete());
    health = subscriptions.GetHealth();
    assert(health.complete);
    assert(health.contracts.at(optionInstrument).quote.HasQuote());
    AuthoritativeTradingSnapshot snapshot = store.GetSnapshot(1004);
    assert(snapshot.quotesState.complete);
    assert(snapshot.quotes.size() == 2);
    assert(subscriptions.GetPrimaryQuote().HasQuote());
    assert(health.contracts.at(fxInstrument).quote.bidObservedAtMs == 1001);
    assert(health.contracts.at(fxInstrument).quote.askObservedAtMs == 1002);
    assert(health.contracts.at(fxInstrument).quote.CompositeObservedAtMs() == 1001);
    assert(health.contracts.at(fxInstrument).quote.LivenessObservedAtMs() == 1002);

    for (int delayedField = 66; delayedField <= 68; ++delayedField)
    {
        assert(subscriptions.ConsumeTick(
            Tick(7, fxRequest, std::to_string(delayedField), 9.9),
            1040 + delayedField).status ==
            IBAuthoritativeQuoteConsumeStatus::Ignored);
    }
    health = subscriptions.GetHealth();
    assert(health.contracts.at(fxInstrument).quote.bidObservedAtMs == 1001);
    assert(health.contracts.at(fxInstrument).quote.askObservedAtMs == 1002);
    assert(health.contracts.at(fxInstrument).quote.lastObservedAtMs == 0);
    assert(store.GetSnapshot(1108).quotes.at(
        fxInstrument).state.updatedAtMs == 1002);

    assert(subscriptions.ConsumeTick(
        Tick(7, fxRequest, "1", 1.1001), 1050).write.accepted);
    health = subscriptions.GetHealth();
    assert(health.contracts.at(fxInstrument).quote.bidObservedAtMs == 1050);
    assert(health.contracts.at(fxInstrument).quote.askObservedAtMs == 1002);
    assert(health.contracts.at(fxInstrument).quote.CompositeObservedAtMs() == 1002);
    assert(health.contracts.at(fxInstrument).quote.LivenessObservedAtMs() == 1050);
    assert(store.GetSnapshot(1050).quotes.at(
        fxInstrument).state.updatedAtMs == 1050);

    assert(subscriptions.ConsumeTick(
        Tick(7, fxRequest, "4", 1.10015), 1070).write.accepted);
    health = subscriptions.GetHealth();
    assert(health.contracts.at(fxInstrument).quote.lastObservedAtMs == 1070);
    assert(health.contracts.at(fxInstrument).quote.CompositeObservedAtMs() == 1002);
    assert(health.contracts.at(fxInstrument).quote.LivenessObservedAtMs() == 1050);
    assert(store.GetSnapshot(1070).quotes.at(
        fxInstrument).state.updatedAtMs == 1050);

    assert(subscriptions.ConsumeTick(
        Tick(7, fxRequest, "2", 1.1003), 1080).write.accepted);
    health = subscriptions.GetHealth();
    assert(health.contracts.at(fxInstrument).quote.askObservedAtMs == 1080);
    assert(health.contracts.at(fxInstrument).quote.CompositeObservedAtMs() == 1050);
    assert(health.contracts.at(fxInstrument).quote.LivenessObservedAtMs() == 1080);
    assert(store.GetSnapshot(1080).quotes.at(
        fxInstrument).state.updatedAtMs == 1080);

    const IBAuthoritativeQuoteSubscriptionPlan second =
        subscriptions.BeginCycle(7, 2, 1100);
    assert(second.accepted);
    assert(second.cancelRequestIds.size() == 2);
    assert(!store.GetSnapshot(1100).quotesState.complete);
    assert(subscriptions.ConsumeTick(Tick(7, fxRequest, "1", 1.2), 1101).status ==
        IBAuthoritativeQuoteConsumeStatus::Ignored);
    const int secondFxRequest = RequestIdFor(second, fxInstrument);
    assert(subscriptions.ConsumeTick(Tick(6, secondFxRequest, "1", 1.2), 1102).status ==
        IBAuthoritativeQuoteConsumeStatus::Ignored);
    std::map<std::string, IBContractLite> expandedContracts = contracts;
    const IBContractLite futureDuringCycle = FutureContract();
    const std::string futureDuringCycleInstrument =
        BuildIBAuthoritativeInstrumentIdentity(futureDuringCycle);
    expandedContracts[futureDuringCycleInstrument] = futureDuringCycle;
    assert(subscriptions.Configure(expandedContracts, fxInstrument, reason, true));
    health = subscriptions.GetHealth();
    assert(health.desiredRevision == 2);
    assert(health.contracts.at(futureDuringCycleInstrument).active == false);
    assert(subscriptions.AbortCycle(2).size() == 2);

    assert(subscriptions.Configure(contracts, fxInstrument, reason));
    const IBAuthoritativeQuoteSubscriptionPlan incrementalBase =
        subscriptions.BeginCycle(10, 20, 1150);
    assert(incrementalBase.subscriptions.size() == 2);
    const int incrementalFxRequest = RequestIdFor(incrementalBase, fxInstrument);
    assert(subscriptions.RecordDispatchResult(20, incrementalFxRequest, true));
    const int incrementalOptionRequest = RequestIdFor(incrementalBase, optionInstrument);
    assert(subscriptions.RecordDispatchResult(20, incrementalOptionRequest, true));
    subscriptions.ConsumeTick(Tick(10, incrementalFxRequest, "1", 1.1), 1151);
    subscriptions.ConsumeTick(Tick(10, incrementalFxRequest, "2", 1.2), 1152);
    subscriptions.ConsumeTick(Tick(10, incrementalOptionRequest, "1", 4.1), 1153);
    subscriptions.ConsumeTick(Tick(10, incrementalOptionRequest, "2", 4.2), 1154);
    assert(subscriptions.Configure(expandedContracts, fxInstrument, reason, true));
    const IBAuthoritativeQuoteSubscriptionPlan incremental =
        subscriptions.BeginCycle(10, 21, 1160);
    assert(incremental.cancelRequestIds.empty());
    assert(incremental.subscriptions.size() == 1);
    assert(incremental.subscriptions[0].instrument == futureDuringCycleInstrument);
    assert(RequestIdFor(incremental, fxInstrument) == 0);
    assert(subscriptions.GetHealth().contracts.at(fxInstrument).requestId == incrementalFxRequest);
    assert(subscriptions.AbortCycle(21).size() == 3);

    const IBContractLite future = FutureContract();
    const std::string futureInstrument = BuildIBAuthoritativeInstrumentIdentity(future);
    std::map<std::string, IBContractLite> futureOnly;
    futureOnly[futureInstrument] = future;
    assert(subscriptions.Configure(futureOnly, futureInstrument, reason));
    const IBAuthoritativeQuoteSubscriptionPlan third =
        subscriptions.BeginCycle(8, 3, 1200);
    assert(third.accepted && third.subscriptions.size() == 1);
    const int futureRequest = third.subscriptions[0].requestId;
    assert(subscriptions.RecordDispatchResult(3, futureRequest, true));
    subscriptions.ConsumeTick(Tick(8, futureRequest, "1", -40.0), 1201);
    const IBAuthoritativeQuoteConsumeResult negative =
        subscriptions.ConsumeTick(Tick(8, futureRequest, "2", -39.5), 1202);
    assert(negative.completedNow);
    assert(store.GetSnapshot(1202).quotes.find(futureInstrument)->second.value.bid == -40.0);
    assert(subscriptions.AbortCycle(3).size() == 1);

    AuthoritativeTradingSnapshotStore dispatchFailureStore;
    IBAuthoritativeQuoteSubscriptionSet dispatchFailure(dispatchFailureStore);
    std::map<std::string, IBContractLite> fxOnly;
    fxOnly[fxInstrument] = fx;
    assert(dispatchFailure.Configure(fxOnly, fxInstrument, reason));
    const IBAuthoritativeQuoteSubscriptionPlan failedPlan =
        dispatchFailure.BeginCycle(9, 1, 1300);
    assert(failedPlan.accepted && failedPlan.subscriptions.size() == 1);
    const int failedRequest = failedPlan.subscriptions[0].requestId;
    assert(dispatchFailure.RecordDispatchResult(1, failedRequest, false));
    dispatchFailure.ConsumeTick(Tick(9, failedRequest, "1", 1.0), 1301);
    dispatchFailure.ConsumeTick(Tick(9, failedRequest, "2", 1.1), 1302);
    assert(!dispatchFailure.IsComplete());
    assert(!dispatchFailureStore.GetSnapshot(1302).quotesState.complete);

    AuthoritativeTradingSnapshotStore rejectedStore;
    IBAuthoritativeQuoteSubscriptionSet rejected(rejectedStore);
    std::map<std::string, IBContractLite> mismatched;
    mismatched["WRONG"] = fx;
    assert(!rejected.Configure(mismatched, "WRONG", reason));
    assert(reason == "QUOTE_CONTRACT_IDENTITY_MISMATCH");
    return 0;
}
