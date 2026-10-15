#include "../HeptaTrade/execution/ib_paper_execution_stop_research.h"
#include <cmath>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace hepta_execution_stop_research;

namespace {
void Require(bool ok, const char* why) {
    if (!ok) throw std::runtime_error(why);
}
void MustRefuse(const std::function<void()>& call, const char* expected) {
    try { call(); }
    catch (const Refusal& error) {
        Require(std::string(error.what()) == expected, "unexpected refusal code");
        return;
    }
    throw std::runtime_error("unexpectedly accepted unsafe stop");
}
BrokerExposure Ready() {
    BrokerExposure s;
    s.account = "DU-SYNTHETIC";
    s.owner = "synthetic-execution-owner";
    s.originalEntryExecutionId = "synthetic-broker-fill-01";
    s.exactBrokerContractId = 12087792;
    s.executionDomain = "PAPER";
    s.connectionEpoch = 19;
    s.positionConnectionEpoch = 19;
    s.activeOrdersConnectionEpoch = 19;
    s.positionGeneration = 23;
    s.activeOrdersGeneration = 23;
    s.observedAtMs = 10000;
    s.validUntilMs = 11000;
    s.accountAndPositionAttested = true;
    s.activeOrdersComplete = true;
    s.originalEntryBrokerFillAttested = true;
    s.noNewEntryFenceAcknowledged = true;
    s.unresolvedOriginalCommand = false;
    s.activeOrderCount = 0;
    s.brokerQuantity = -89201;
    s.originalDriverQuantity = -89201;
    s.brokerAverageEntry = 1.12054;
    s.brokerBid = 1.12049;
    s.brokerAsk = 1.12051;
    s.minTick = 0.00005;
    return s;
}
BrokerStopWitness Working(const StopProposal& p) {
    BrokerStopWitness s;
    s.account = p.account;
    s.owner = p.owner;
    s.instrument = "EUR.USD";
    s.status = "Submitted";
    s.action = p.order.action;
    s.orderType = p.order.orderType;
    s.timeInForce = p.timeInForce;
    s.orderRef = p.order.orderRef;
    s.exactBrokerContractId = p.exactBrokerContractId;
    s.orderId = 98765;
    s.permanentId = 987654321;
    s.brokerClientId = 1701;
    s.connectionEpoch = p.connectionEpoch;
    s.positionGeneration = p.positionGeneration;
    s.activeOrdersGeneration = p.activeOrdersGeneration+1;
    s.globalActiveOrdersComplete = true;
    s.quoteAndTriggerMethodAttested = true;
    s.quantity = p.order.totalQuantity;
    s.auxPrice = p.order.auxPrice;
    s.filledQuantity = 0.0;
    s.remainingQuantity = p.order.totalQuantity;
    return s;
}
void CandidateContract() {
    const StopProposal p = ProposeUnsentFxStop(Ready(), 10100);
    Require(p.order.action == "BUY" && p.order.orderType == "STP",
            "short must have buy stop");
    Require(p.order.totalQuantity == 89201 && p.order.lmtPrice == 0.0,
            "stop size or limit invalid");
    Require(p.order.auxPrice > 1.12054 && p.order.auxPrice < 1.123,
            "invalid stop threshold");
    Require(p.timeInForce == "GTC" && p.contract.secType == "CASH" &&
            p.contract.exchange == "IDEALPRO",
            "wrong venue contract");
    Require(!p.sendAuthority && !p.maintenanceAuthorized &&
            !p.brokerNativeReduceOnlyProven,
            "research candidate claimed live protection");
    const auto w = Working(p);
    Require(MatchesBrokerWorkingStop(p, w, 1701),
            "genuine frozen broker witness rejected");
}
void LongContract() {
    auto s = Ready();
    s.brokerQuantity = s.originalDriverQuantity = 89201;
    const auto p = ProposeUnsentFxStop(s,10100);
    Require(p.order.action == "SELL" && p.order.auxPrice < s.brokerAverageEntry,
            "long protective direction inverted");
}
void RejectUnfencedAndLive() {
    auto s=Ready();s.executionDomain="LIVE";
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "PAPER_EXACT_ACCOUNT_OWNER_REQUIRED");
    s=Ready();s.originalEntryExecutionId="";
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "PAPER_EXACT_ACCOUNT_OWNER_REQUIRED");
    s=Ready();s.exactBrokerContractId=1234;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "PAPER_EXACT_ACCOUNT_OWNER_REQUIRED");
    s=Ready();s.noNewEntryFenceAcknowledged=false;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_FENCE_POSITION_ORDERS_NOT_ATTESTED");
    s=Ready();s.originalEntryExecutionId="invalid:newline";
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "PAPER_EXACT_ACCOUNT_OWNER_REQUIRED");
    s=Ready();s.originalEntryExecutionId=std::string(40,'b');
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "STOP_SHADOW_ORDER_REF_EXCEEDS_IB_LIMIT");
    s=Ready();s.activeOrderCount=1;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_FENCE_POSITION_ORDERS_NOT_ATTESTED");
    s=Ready();s.unresolvedOriginalCommand=true;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_FENCE_POSITION_ORDERS_NOT_ATTESTED");
}
void RejectStaleAndDivergent() {
    auto s=Ready();s.activeOrdersGeneration=33;
    Require(ProposeUnsentFxStop(s,10100).order.action=="BUY",
            "independent valid generation counters rejected");
    s=Ready();s.activeOrdersConnectionEpoch=18;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_GENERATION_OR_FRESHNESS_UNKNOWN");
    s=Ready();s.validUntilMs=9999;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_GENERATION_OR_FRESHNESS_UNKNOWN");
    s=Ready();s.validUntilMs=14000;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "BROKER_GENERATION_OR_FRESHNESS_UNKNOWN");
    s=Ready();s.originalDriverQuantity=0;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "EXACT_FX_FILL_QUOTE_GRID_REQUIRED");
    s=Ready();s.brokerQuantity=100001;s.originalDriverQuantity=100001;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "FX_QUANTITY_OUTSIDE_ORIGINAL_GRID");
    s=Ready();s.brokerQuantity=-89201.5;s.originalDriverQuantity=-89201.5;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "FX_QUANTITY_OUTSIDE_ORIGINAL_GRID");
}
void RejectAlreadyAdverse() {
    auto s=Ready();
    s.brokerAsk=1.124;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "RISK_THRESHOLD_ALREADY_NEAR_OR_CROSSED");
    s=Ready();s.minTick=0;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "EXACT_FX_FILL_QUOTE_GRID_REQUIRED");
    s=Ready();s.minTick=0.00001;
    MustRefuse([&]{ ProposeUnsentFxStop(s,10100); },
               "FX_CONTRACT_MARKET_RULE_CHANGED");
}
void BrokerWitnessNeverTrustsUncorrelated() {
    const StopProposal p=ProposeUnsentFxStop(Ready(),10100);
    auto b=Working(p);
    b.brokerClientId=1702;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"wrong client accepted");
    b=Working(p);b.status="PreSubmitted";
    Require(!MatchesBrokerWorkingStop(p,b,1701),"pre-submit treated as working");
    b=Working(p);b.permanentId=0;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"no permanent ID accepted");
    b=Working(p);b.orderRef="another-order";
    Require(!MatchesBrokerWorkingStop(p,b,1701),"foreign original entry accepted");
    b=Working(p);b.exactBrokerContractId=98765;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"substituted FX conId accepted");
    b=Working(p);b.filledQuantity=1;b.remainingQuantity-=1;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"partially filled stop treated as fully protecting");
    b=Working(p);b.remainingQuantity=0;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"unknown stop remaining quantity accepted");
    b=Working(p);b.auxPrice+=.00001;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"changed stop accepted");
    b=Working(p);b.action="SELL";
    Require(!MatchesBrokerWorkingStop(p,b,1701),"reversal-risk stop accepted");
    b=Working(p);b.globalActiveOrdersComplete=false;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"partial order inventory accepted");
    b=Working(p);b.connectionEpoch+=1;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"unbound reconnect epoch accepted");
    b=Working(p);b.positionGeneration+=1;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"changed position generation accepted");
    b=Working(p);b.quoteAndTriggerMethodAttested=false;
    Require(!MatchesBrokerWorkingStop(p,b,1701),"unverified trigger method accepted");
}
void DurableSequenceNeverResends() {
    const auto p=ProposeUnsentFxStop(Ready(),10100);
    ExactStopReceiptSequence guard(p);
    Require(guard.Phase()==StopEvidencePhase::NONE,"unexpected initial state");
    Require(guard.RecordPrepared("immutable-command-001"),"prepare refused");
    Require(!guard.RecordPrepared("immutable-command-002"),"duplicate prepare allowed");
    Require(!guard.RecordPossiblySent("other"),"alien command accepted");
    Require(guard.RecordPossiblySent("immutable-command-001"),"uncertain send missing");
    Require(!guard.RecordPossiblySent("immutable-command-001"),"second send allowed");
    auto receipt=Working(p);
    Require(guard.ObserveWorking("immutable-command-001",receipt,1701),
            "original broker acknowledgement not recognized");
    Require(guard.Phase()==StopEvidencePhase::BROKER_WORKING,
            "broker acknowledgement state wrong");
    Require(!guard.MaySubmitAgain() && !guard.MayStopDriver(),
            "research receipt granted transport or shutdown");
    guard.OnBrokerReconnect();
    Require(guard.Phase()==StopEvidencePhase::RECONNECT_UNCONFIRMED,
            "reconnect did not revoke stale evidence");
    Require(!guard.ObserveWorking("immutable-command-001",receipt,1701),
            "stale pre-reconnect receipt replay accepted");
    auto renewed=Ready();
    renewed.connectionEpoch = 20;
    renewed.positionConnectionEpoch = 20;
    renewed.activeOrdersConnectionEpoch = 20;
    renewed.positionGeneration = 24;
    renewed.activeOrdersGeneration = 25;
    renewed.activeOrderCount = 1;
    auto actual=Working(p);
    actual.connectionEpoch = 20;
    actual.positionGeneration = 24;
    actual.activeOrdersGeneration = 26;
    auto wrong=renewed;
    wrong.owner = "different-owner";
    Require(!guard.ObserveReconnectedWorking(
        "immutable-command-001",wrong,actual,1701,10100),
        "owner substitution accepted during reconnect");
    actual.permanentId++;
    Require(!guard.ObserveReconnectedWorking(
        "immutable-command-001",renewed,actual,1701,10100),
        "different permanent ID accepted");
    actual.permanentId--;
    Require(guard.ObserveReconnectedWorking(
        "immutable-command-001",renewed,actual,1701,10100),
        "same permanent ID in new broker epoch refused");
    guard.OnCancelOrPartialFill();
    Require(guard.Phase()==StopEvidencePhase::CANCELLED_OR_PARTIAL &&
            !guard.MaySubmitAgain() && !guard.MayStopDriver(),
            "cancel/partial fill reopened sending or maintenance");
}
void IntentBeforeSendAndUnknownNeverForgesTerminal() {
    const auto p=ProposeUnsentFxStop(Ready(),10100);
    ExactStopReceiptSequence guard(p);
    Require(!guard.RecordPossiblySent("x"),"send without durable intent");
    Require(!guard.ObserveWorking("x",Working(p),1701),
            "broker callback without intent accepted");
    guard.RecordPrepared("x");
    guard.RecordPossiblySent("x");
    guard.OnBrokerReconnect();
    Require(!guard.MaySubmitAgain() && !guard.MayStopDriver(),
            "unknown result enabled unsafe retry");
    Require(guard.Phase()==StopEvidencePhase::RECONNECT_UNCONFIRMED,
            "unknown result fabricated terminal");
    auto renewed=Ready();
    renewed.connectionEpoch=20;
    renewed.positionConnectionEpoch=20;
    renewed.activeOrdersConnectionEpoch=20;
    renewed.positionGeneration=24;
    renewed.activeOrdersGeneration=25;
    renewed.activeOrderCount=1;
    auto b=Working(p);
    b.connectionEpoch=20;
    b.positionGeneration=24;
    b.activeOrdersGeneration=26;
    Require(!guard.ObserveReconnectedWorking("x",renewed,b,1701,10100),
            "unknown prior send invented a permanent order ID");
}
} // namespace

int main() {
    try {
        CandidateContract();LongContract();RejectUnfencedAndLive();
        RejectStaleAndDivergent();RejectAlreadyAdverse();
        BrokerWitnessNeverTrustsUncorrelated();DurableSequenceNeverResends();
        IntentBeforeSendAndUnknownNeverForgesTerminal();
        std::cout << "execution_owner_stop_shadow_negative_checks=PASS" << std::endl;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "FAILED: " << e.what() << std::endl;
        return 1;
    }
}
