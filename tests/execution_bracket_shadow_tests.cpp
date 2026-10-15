#include "../HeptaTrade/execution/ib_paper_execution_bracket_research.h"
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
using namespace hepta_execution_bracket_research;
void Require(bool valid, const char* reason) {
    if (!valid) throw std::runtime_error(reason);
}
void Refused(const std::function<void()>& call, const char* expected) {
    try {call();} catch (const Refusal& e) {
        Require(std::string(e.what()) == expected, "unexpected refusal reason");
        return;
    }
    throw std::runtime_error("unsafe bracket accepted");
}
PreEntrySnapshot Fresh(const char* side="BUY") {
    PreEntrySnapshot s;
    s.account="DU-FAKE";s.owner="execution-owner-synthetic";
    s.originalCommandKey="synthetic-entry-00001";
    s.executionDomain="PAPER";s.contractId=12087792;
    s.clientId=1701;s.reservedParentOrderId=30000;s.reservedChildOrderId=30001;
    s.connectionEpoch=100;s.positionConnectionEpoch=100;
    s.openOrdersConnectionEpoch=100;s.originalPositionGeneration=8;
    s.observedAtMs=10000;s.validUntilMs=11000;
    s.exactAccountOwnerAndContract=true;
    s.brokerIdsReservedWithinExactClient=true;
    s.coordinatorExclusiveEntryOwner=true;
    s.riskSnapshotComplete=true;
    s.openOrdersComplete=true;s.positionFlat=true;s.noUnresolvedOrders=true;
    s.brokerActiveOrderCount=0;s.brokerPosition=0;
    s.quantity=89201;s.bid=1.12049;s.ask=1.12051;
    s.minTick=.00005;s.action=side;
    return s;
}
BrokerBracketWitness BrokerPair(const BracketShadowPlan& p) {
    BrokerBracketWitness w;
    w.account=p.account;w.owner=p.owner;
    w.originalCommandKey=p.originalCommandKey;
    w.parentRef=p.parent.intent.orderRef;w.stopRef=p.stop.intent.orderRef;
    w.parentStatus="Filled";w.stopStatus="Submitted";
    w.parentAction=p.parent.intent.action;w.stopAction=p.stop.intent.action;
    w.parentOrderId=p.parent.orderId;w.childOrderId=p.stop.orderId;
    w.childParentId=p.parent.orderId;w.parentPermId=246801;
    w.stopPermId=246802;
    w.originalParentExecutionId="IB-synthetic-original-fill-1";
    w.originalParentExecutionProven=true;
    w.nativeChildLinkedBeforeParentFill=true;
    w.clientId=p.clientId;w.contractId=p.contractId;
    w.connectionEpoch=p.connectionEpoch;
    w.positionConnectionEpoch=p.connectionEpoch;
    w.activeOrderConnectionEpoch=p.connectionEpoch;
    w.activeOrderInventoryComplete=true;
    w.brokerSourceAttested=true;w.stopTriggerMethodAttested=true;
    w.parentQuantity=p.parent.intent.totalQuantity;
    w.stopQuantity=p.stop.intent.totalQuantity;
    w.stopRemaining=p.stop.intent.totalQuantity;
    w.positionQuantity=(p.parent.intent.action=="BUY" ?
       p.parent.intent.totalQuantity : -p.parent.intent.totalQuantity);
    w.stopPrice=p.stop.intent.auxPrice;
    w.orderType="STP";w.timeInForce="GTC";
    return w;
}
void ExactWireGroup() {
    const auto p=ProposeUnsentBracket(Fresh(), 10100);
    Require(p.parent.orderId==30000 && p.stop.orderId==30001 &&
            p.stop.parentId==p.parent.orderId, "parent child identity invalid");
    Require(!p.parent.transmit && p.stop.transmit,
            "parent must be held until last child");
    Require(p.parent.intent.orderType=="MKT" && p.parent.tif=="DAY" &&
            p.stop.intent.orderType=="STP" && p.stop.tif=="GTC",
            "wrong native wire order types");
    Require(p.parent.intent.action=="BUY" && p.stop.intent.action=="SELL",
            "long side stop inverted");
    Require(p.stop.intent.auxPrice < Fresh().bid &&
            p.stop.intent.auxPrice>1.118, "long stop improperly priced");
    Require(p.parent.intent.totalQuantity==p.stop.intent.totalQuantity &&
            p.parent.intent.totalQuantity==89201,"missing entire quantity");
    Require(!p.parent.sendAuthority && !p.stop.sendAuthority &&
            !p.maintenanceAuthorized && !p.actualBrokerProtectionProven &&
            !p.ordinaryExitCompatible,
            "research bundle claimed production protection");
    const auto shortP=ProposeUnsentBracket(Fresh("SELL"),10100);
    Require(shortP.parent.intent.action=="SELL" && shortP.stop.intent.action=="BUY" &&
            shortP.stop.intent.auxPrice > Fresh().ask,
            "short native buy stop invalid");
}
void IdentityAndRiskMustRefuse() {
    auto s=Fresh();s.executionDomain="LIVE";
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_EXACT_PAPER_SCOPE_REQUIRED");
    s=Fresh();s.originalCommandKey="malformed:name";
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_EXACT_PAPER_SCOPE_REQUIRED");
    s=Fresh();s.contractId=1234;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_EXACT_PAPER_SCOPE_REQUIRED");
    s=Fresh();s.coordinatorExclusiveEntryOwner=false;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
    s=Fresh();s.noUnresolvedOrders=false;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
    s=Fresh();s.brokerPosition=10;s.positionFlat=false;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
    s=Fresh();s.openOrdersComplete=false;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
    s=Fresh();s.riskSnapshotComplete=false;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
    s=Fresh();s.brokerActiveOrderCount=1;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_OWNER_FLAT_FENCE_MISSING");
}
void EpochQuoteAndOrderIdMustRefuse() {
    auto s=Fresh();s.openOrdersConnectionEpoch=99;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_BROKER_EPOCH_OR_QUOTE_STALE");
    s=Fresh();s.validUntilMs=10000;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_BROKER_EPOCH_OR_QUOTE_STALE");
    s=Fresh();s.clientId=0;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_BROKER_ORDER_RESERVATION_INVALID");
    s=Fresh();s.reservedChildOrderId=30010;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_BROKER_ORDER_RESERVATION_INVALID");
    s=Fresh();s.quantity=100001;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_FX_QUANTITY_QUOTE_GRID_INVALID");
    s=Fresh();s.quantity=100000;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_ORIGINAL_NOTIONAL_EXCEEDED");
    s=Fresh();s.reservedParentOrderId=2147483647L;
    s.reservedChildOrderId=2147483647L;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_BROKER_ORDER_RESERVATION_INVALID");
    s=Fresh();s.action="HOLD";
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_FX_QUANTITY_QUOTE_GRID_INVALID");
    s=Fresh();s.bid=s.ask+0.01;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_FX_QUANTITY_QUOTE_GRID_INVALID");
    s=Fresh();s.minTick=0;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_FX_QUANTITY_QUOTE_GRID_INVALID");
    s=Fresh();s.minTick=.00001;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_CONTRACT_MARKET_RULE_CHANGED");
    s=Fresh();s.bid=1.118;s.ask=1.121;
    Refused([&]{ProposeUnsentBracket(s,10100);},"BRACKET_STOP_ALREADY_NEAR_OR_CROSSED");
}
void BrokerWitnessMustRefuseSpoofing() {
    const auto p=ProposeUnsentBracket(Fresh(),10100);
    const auto genuine=BrokerPair(p);
    Require(MatchesPair(p,genuine,100),"genuine synthetic pair mismatch");
    auto w=genuine;w.stopRef="other-stop";
    Require(!MatchesPair(p,w,100),"foreign stop accepted");
    w=genuine;w.parentPermId=0;
    Require(!MatchesPair(p,w,100),"missing parent permanentId accepted");
    w=genuine;w.originalParentExecutionProven=false;
    Require(!MatchesPair(p,w,100),"unproved original entry fill accepted");
    w=genuine;w.originalParentExecutionId="";
    Require(!MatchesPair(p,w,100),"missing original fill identity accepted");
    w=genuine;w.nativeChildLinkedBeforeParentFill=false;
    Require(!MatchesPair(p,w,100),"late child link accepted as atomic bracket");
    w=genuine;w.childParentId++;
    Require(!MatchesPair(p,w,100),"wrong childParentId accepted");
    w=genuine;w.clientId++;
    Require(!MatchesPair(p,w,100),"alien IB client accepted");
    w=genuine;w.contractId++;
    Require(!MatchesPair(p,w,100),"different IB contract accepted");
    w=genuine;w.stopStatus="PreSubmitted";
    Require(!MatchesPair(p,w,100),"untransmitted stop called protected");
    w=genuine;w.parentStatus="PartiallyFilled";
    Require(!MatchesPair(p,w,100),"partial parent considered fully covered");
    w=genuine;w.stopRemaining--;
    Require(!MatchesPair(p,w,100),"partial stop considered fully covered");
    w=genuine;w.positionQuantity/=2;
    Require(!MatchesPair(p,w,100),"mismatched physical position accepted");
    w=genuine;w.stopTriggerMethodAttested=false;
    Require(!MatchesPair(p,w,100),"unverified trigger accepted");
    w=genuine;w.activeOrderInventoryComplete=false;
    Require(!MatchesPair(p,w,100),"incomplete open-order set accepted");
    w=genuine;w.activeOrderConnectionEpoch++;
    Require(!MatchesPair(p,w,100),"foreign epoch accepted");
}
void NoTransmissionsOrRestartsInStateMachine() {
    const auto p=ProposeUnsentBracket(Fresh(),10100);
    ShadowBracketSequence m(p);
    Require(m.Phase()==BracketPhase::NO_INTENT, "initial phase incorrect");
    Require(!m.ParentHeld(p.originalCommandKey,true),"unpersisted parent accepted");
    Require(!m.DurablePrepare(p.originalCommandKey,false),"undurable OMS accepted");
    Require(m.DurablePrepare(p.originalCommandKey,true),"real durable flag refused");
    Require(!m.DurablePrepare(p.originalCommandKey,true),"duplicate original key accepted");
    Require(!m.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
            "child released before parent was held");
    Require(m.ParentHeld(p.originalCommandKey,true),"untransmitted parent not recorded");
    Require(!m.ParentHeld(p.originalCommandKey,true),"parent sent twice");
    Require(!m.ParentHeld("alien",true),"alien command staged");
    Require(m.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
            "child dispatch disposition unavailable");
    Require(!m.ChildReleaseMayHaveBeenSent(p.originalCommandKey),
            "duplicate transmit accepted");
    Require(m.AttestPair(BrokerPair(p)),"synthetic paired broker receipt refused");
    Require(m.Phase()==BracketPhase::BROKER_PAIR_ATTESTED,
            "validated synthetic pair not accepted");
    Require(!m.MaySendAnotherOrder() && !m.MayOrdinaryExitWithoutOca() &&
            !m.MayStopDriver() && !m.IsLiveProtectionQualified(),
            "shadow group granted real authority");
    m.OnBrokerDisconnectOrExecutionRestart();
    Require(m.Phase()==BracketPhase::RECONNECT_UNCONFIRMED,
            "reconnect retained stale protection");
    Require(!m.ReattestPair(BrokerPair(p)),
            "old broker epoch reattested as new");
    auto newEpoch=BrokerPair(p);
    newEpoch.connectionEpoch=101;
    newEpoch.activeOrderConnectionEpoch=101;
    newEpoch.positionConnectionEpoch=101;
    newEpoch.stopPermId++;
    Require(!m.ReattestPair(newEpoch),"different stop permId accepted");
    newEpoch.stopPermId--;
    Require(m.ReattestPair(newEpoch),"same broker group not reattested");
    m.OnPartialFillOrManualCloseWithoutOca();
    Require(m.Phase()==BracketPhase::PROTECTION_BROKEN,
            "ordinary exit or partial failed to revoke");
    Require(!m.MayStopDriver(),"unsafe maintenance after partial");
}
void UnknownChildReleaseCannotReplay() {
    const auto p=ProposeUnsentBracket(Fresh(),10100);
    ShadowBracketSequence m(p);
    Require(m.DurablePrepare(p.originalCommandKey,true),"prepare failed");
    Require(m.ParentHeld(p.originalCommandKey,true),"hold failed");
    Require(m.ChildReleaseMayHaveBeenSent(p.originalCommandKey),"child uncertainty failed");
    m.OnBrokerDisconnectOrExecutionRestart();
    Require(m.Phase()==BracketPhase::RECONNECT_UNCONFIRMED,
            "uncertain broker terminal fabricated");
    Require(!m.AttestPair(BrokerPair(p)) && !m.MaySendAnotherOrder() &&
            !m.MayStopDriver(), "uncertain order caused unsafe action");
}
// Fake broker model: parent is NOT executed when held; parent+child only become
// active together when the child transmit flag commits. Not an IBKR API claim.
class SimulatedTws final {
public:
    bool held=false,active=false,driverAlive=true,brokerConnected=true;
    bool Stage(const BracketShadowPlan& p) {
        if (!brokerConnected || held || active || p.parent.transmit) return false;
        held=true;return true;
    }
    bool Release(const BracketShadowPlan& p) {
        if (!brokerConnected || !held || active || !p.stop.transmit ||
            p.stop.parentId != p.parent.orderId) return false;
        held=false;active=true;return true;
    }
    void KillDriver() {driverAlive=false;}
    void RestartTwsBeforeChild() {held=false;brokerConnected=false;}
    bool OrdinaryExit(const BracketShadowPlan&) const {return false;} // no OCA
};
void SimulatedDriverDeathAndManualExitRace() {
    const auto p=ProposeUnsentBracket(Fresh(),10100);
    SimulatedTws venue;
    Require(venue.Stage(p),"parent not staged");
    Require(!venue.active,"held parent unexpectedly executable");
    venue.RestartTwsBeforeChild();
    Require(!venue.Release(p) && !venue.active,
            "untransmitted lost parent still released");
    SimulatedTws venue2;
    Require(venue2.Stage(p) && venue2.Release(p),"child transmit did not commit");
    venue2.KillDriver();
    Require(venue2.active && !venue2.driverAlive,
            "simulated working child disappeared with driver");
    Require(!venue2.OrdinaryExit(p),
            "unprotected ordinary close allowed to overfill");
}
} // namespace

int main() {
    try {
        ExactWireGroup();IdentityAndRiskMustRefuse();EpochQuoteAndOrderIdMustRefuse();
        BrokerWitnessMustRefuseSpoofing();NoTransmissionsOrRestartsInStateMachine();
        UnknownChildReleaseCannotReplay();SimulatedDriverDeathAndManualExitRace();
        std::cout<<"execution_bracket_shadow_group_checks=PASS\n";
        return 0;
    } catch(const std::exception& e) {
        std::cerr<<"FAILED: "<<e.what()<<"\n";return 1;
    }
}
