#include "../HeptaTrade/adapter_ib/ib_native_bracket_research.h"
#include <cassert>
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace hepta_execution_bracket_research;
using namespace hepta_execution_bracket_native_research;

namespace {
void Check(bool x, const char* message) { if (!x) throw std::runtime_error(message); }

PreEntrySnapshot Original(const std::string& action) {
    PreEntrySnapshot s;
    s.account="DU-SYNTHETIC";s.owner="pocket4-shadow-execution";
    s.originalCommandKey="native-test-original-001";
    s.executionDomain="PAPER";s.contractId=12087792;s.clientId=991;
    s.reservedParentOrderId=321001;s.reservedChildOrderId=321002;
    s.connectionEpoch=101;s.positionConnectionEpoch=101;
    s.openOrdersConnectionEpoch=101;s.originalPositionGeneration=20;
    s.observedAtMs=100000;s.validUntilMs=101000;
    s.exactAccountOwnerAndContract=true;
    s.brokerIdsReservedWithinExactClient=true;
    s.coordinatorExclusiveEntryOwner=true;
    s.riskSnapshotComplete=true;s.openOrdersComplete=true;
    s.positionFlat=true;s.noUnresolvedOrders=true;
    s.quantity=89200;s.bid=1.12045;s.ask=1.12050;
    s.minTick=.00005;s.action=action;
    return s;
}
void NativeWireCoherence(const std::string& side) {
    const auto s=Original(side);
    const auto pair=BuildNeverSentNativePair(s,100500);
    Check(pair.contract.conId==12087792 &&
          pair.contract.symbol=="EUR" &&
          pair.contract.currency=="USD" &&
          pair.contract.secType=="CASH" &&
          pair.contract.exchange=="IDEALPRO",
          "native exact IB cash contract differs");
    Check(pair.parent.clientId==991 && pair.stop.clientId==991 &&
          pair.parent.orderId==321001 && pair.stop.orderId==321002 &&
          pair.parent.parentId==0 && pair.stop.parentId==321001,
          "native API parent-child identity wrong");
    Check(!pair.parent.transmit && pair.stop.transmit,
          "native TWS transmit sequencing wrong");
    Check(pair.parent.orderType=="MKT" && pair.stop.orderType=="STP" &&
          pair.parent.tif=="DAY" && pair.stop.tif=="GTC",
          "native market/stop time in force mismatch");
    Check(pair.parent.account==s.account && pair.stop.account==s.account &&
          pair.parent.orderRef=="HEPTA-PARENT-native-test-original-001" &&
          pair.stop.orderRef=="HEPTA-STOP-native-test-original-001",
          "native IB order account or original ref mismatch");
    Check(pair.stop.action==(side=="BUY"?"SELL":"BUY") &&
          pair.parent.action==side &&
          DecimalFunctions::decimalToDouble(pair.parent.totalQuantity)==89200 &&
          DecimalFunctions::decimalToDouble(pair.stop.totalQuantity)==89200,
          "IB BID decimals or stop direction wrong");
    const double stop=pair.stop.auxPrice;
    const double ratio=stop / s.minTick;
    Check(std::isfinite(stop) && stop>0.0 &&
          std::fabs(ratio-std::round(ratio))<1e-7,
          "native stop not on market rule 3188 grid");
    Check(side=="BUY"?stop<s.bid:stop>s.ask,
          "native protective threshold already crossed");
    Check(pair.stop.triggerMethod==0 && !pair.stop.outsideRth &&
          pair.stop.ocaGroup=="HEPTA-FX-OCA-native-test-original-001" &&
          pair.stop.ocaType==1,
          "shadow OCA group or trigger proposal mismatched");
    Check(!pair.brokerSendEnabled && !pair.driverStopAuthorized &&
          !pair.ordinaryExitWithoutOcaAllowed,
          "native shadow gained execution or shutdown authority");
}
void BadContractNoNative() {
    auto s=Original("BUY");s.contractId=77;
    try { (void)BuildNeverSentNativePair(s,100500); }
    catch(const Refusal& e) {
        Check(std::string(e.what())=="BRACKET_EXACT_PAPER_SCOPE_REQUIRED",
              "wrong conId refusal");
        return;
    }
    throw std::runtime_error("mismatched IB contract passed native adapter");
}
void LegacyOnePipTickRefused() {
    auto s=Original("SELL");s.minTick=0.00001;
    try { (void)BuildNeverSentNativePair(s,100500); }
    catch(const Refusal& e) {
        Check(std::string(e.what())=="BRACKET_CONTRACT_MARKET_RULE_CHANGED",
              "wrong IB tick refusal");
        return;
    }
    throw std::runtime_error("unverified 0.00001 tick passed native adapter");
}
BrokerOcaStopObservation OcaState(
    const BracketShadowPlan& group)
{
    BrokerOcaStopObservation s;
    s.account=group.account;
    s.owner=group.owner;
    s.originalCommandKey=group.originalCommandKey;
    s.originalStopOrderRef=group.stop.intent.orderRef;
    s.brokerOcaGroup="HEPTA-FX-OCA-"+group.originalCommandKey;
    s.executionDomain="PAPER";
    s.contractId=group.contractId;
    s.stopOrderId=group.stop.orderId;
    s.stopPermanentId=5900001;
    s.originalParentPermanentId=5900002;
    s.reservedExitOrderId=group.stop.orderId+1;
    s.brokerClientId=group.clientId;
    s.connectionEpoch=group.connectionEpoch;
    s.positionConnectionEpoch=group.connectionEpoch;
    s.activeOrdersConnectionEpoch=group.connectionEpoch;
    s.observedAtMs=100000;s.validUntilMs=101000;
    s.originalEntryAndStopAttested=true;
    s.sameBrokerOcaGroupAttested=true;
    s.noNewEntriesAcknowledged=true;
    s.workingStopActive=true;
    s.brokerOrderSetComplete=true;
    s.noUnknownOriginalCommand=true;
    s.activeOrderCount=1;
    s.brokerPosition=group.parent.intent.action=="BUY" ?
        group.parent.intent.totalQuantity : -group.parent.intent.totalQuantity;
    s.stopTotalQuantity=group.stop.intent.totalQuantity;
    s.stopRemainingQuantity=group.stop.intent.totalQuantity;
    return s;
}
void RequireOcaRefusal(
    const BracketShadowPlan& p, const BrokerOcaStopObservation& o,
    const char* expected)
{
    try {(void)ProposeUnsentOcaExit(p,o,100500);}
    catch (const Refusal& e) {
        Check(std::string(e.what())==expected,"unexpected OCA refusal code");
        return;
    }
    throw std::runtime_error("unsafe OCA ordinary exit proposal allowed");
}
void NativeOcaSiblingOnlyAfterWorkingStop(const std::string& side) {
    const auto original=Original(side);
    const auto group=ProposeUnsentBracket(original,100500);
    const auto state=OcaState(group);
    const auto out=ProposeUnsentOcaExit(group,state,100500);
    Check(out.contract.conId==12087792 &&
          out.exit.orderId==state.reservedExitOrderId &&
          out.exit.clientId==state.brokerClientId &&
          out.exit.account==state.account,
          "native OCA broker identity mismatch");
    Check(out.exit.orderType=="MKT" && out.exit.tif=="DAY" &&
          out.exit.parentId==0 && out.exit.transmit,
          "native OCA ordinary close intent wrong");
    Check(out.exit.ocaGroup==state.brokerOcaGroup &&
          out.exit.ocaType==1 &&
          out.exit.action==group.stop.intent.action &&
          out.exit.orderRef=="HEPTA-OCA-CLOSE-"+state.originalCommandKey,
          "native OCA block grouping wrong");
    Check(DecimalFunctions::decimalToDouble(out.exit.totalQuantity)==original.quantity,
          "native OCA quantity mismatch");
    Check(!out.sendAuthority && !out.mayStopDriver &&
          !out.automaticTradingEnabled,
          "shadow OCA minted actual trading permission");
}
void RejectUnsafeOcaEvidence() {
    const auto group=ProposeUnsentBracket(Original("BUY"),100500);
    auto o=OcaState(group);
    o.sameBrokerOcaGroupAttested=false;
    RequireOcaRefusal(group,o,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    o=OcaState(group);o.workingStopActive=false;
    RequireOcaRefusal(group,o,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    o=OcaState(group);o.noNewEntriesAcknowledged=false;
    RequireOcaRefusal(group,o,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    o=OcaState(group);o.noUnknownOriginalCommand=false;
    RequireOcaRefusal(group,o,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    o=OcaState(group);o.activeOrderCount=2;
    RequireOcaRefusal(group,o,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    o=OcaState(group);o.stopRemainingQuantity/=2;
    RequireOcaRefusal(group,o,"OCA_PARTIAL_OR_REVERSED_EXPOSURE");
    o=OcaState(group);o.brokerPosition=0.0;
    RequireOcaRefusal(group,o,"OCA_PARTIAL_OR_REVERSED_EXPOSURE");
    o=OcaState(group);o.executionDomain="LIVE";
    RequireOcaRefusal(group,o,"OCA_EXACT_ORIGINAL_ORDER_SCOPE_MISMATCH");
    o=OcaState(group);o.brokerOcaGroup="another";
    RequireOcaRefusal(group,o,"OCA_EXACT_ORIGINAL_ORDER_SCOPE_MISMATCH");
    o=OcaState(group);o.stopPermanentId=0;
    RequireOcaRefusal(group,o,"OCA_STOP_PERM_ID_OR_EXIT_RESERVATION_MISMATCH");
    o=OcaState(group);o.reservedExitOrderId=group.stop.orderId;
    RequireOcaRefusal(group,o,"OCA_STOP_PERM_ID_OR_EXIT_RESERVATION_MISMATCH");
    o=OcaState(group);o.activeOrdersConnectionEpoch++;
    RequireOcaRefusal(group,o,"OCA_BROKER_EPOCH_OR_FRESHNESS_MISMATCH");
    o=OcaState(group);o.validUntilMs=1;
    RequireOcaRefusal(group,o,"OCA_BROKER_EPOCH_OR_FRESHNESS_MISMATCH");
    auto wide=Original("BUY");wide.originalCommandKey=std::string(31,'x');
    const auto wideGroup=ProposeUnsentBracket(wide,100500);
    RequireOcaRefusal(wideGroup,OcaState(wideGroup),
                      "OCA_SHADOW_ORDER_REF_EXCEEDS_IB_LIMIT");
}
void H1NativeOriginalIdentity(const std::string& side) {
    auto snap=Original(side);
    hepta_bracket_h1::FrozenBracket f;
    f.originalCommandKey=snap.originalCommandKey;
    f.account=snap.account; f.owner=snap.owner;
    f.sessionId="session-native-shadow";
    f.executionDomain=snap.executionDomain;
    f.side=snap.action; f.contractId=snap.contractId;
    f.clientId=snap.clientId; f.parentOrderId=snap.reservedParentOrderId;
    f.childOrderId=snap.reservedChildOrderId;
    f.connectionEpoch=snap.connectionEpoch;
    f.positionGeneration=snap.originalPositionGeneration;
    f.ownerFenceToken=88;
    f.intentObservedAtMs=snap.observedAtMs;
    f.quantity=snap.quantity;
    f.referencePrice=side=="BUY"?snap.ask:snap.bid;
    f.stopPrice=hepta_execution_bracket_research::ProposeUnsentBracket(
        snap,100500).stop.intent.auxPrice;
    f.minTick=snap.minTick;
    hepta_bracket_h1::Reservation r;
    std::string reason;
    Check(hepta_bracket_h1::BuildFrozenRefs(f,r.ids,reason),
          "H1 reserved role derivation failed");
    r.frozen=f; r.replayComplete=true;
    auto wire=BuildNeverSentNativeReservedH1Pair(snap,r,100500);
    Check(wire.parent.orderRef==r.ids.parentRef &&
          wire.stop.orderRef==r.ids.childRef &&
          wire.stop.ocaGroup==r.ids.ocaGroup &&
          wire.parent.orderRef.size()==45 &&
          wire.stop.orderRef.size()==45 &&
          wire.stop.ocaGroup.size()==45,
          "H1 native SDK order refs mismatched");
    Check(!wire.brokerSendEnabled && !wire.driverStopAuthorized,
          "native H1 mapping acquired Broker rights");
    // A valid long original command belongs in the 45-byte H1 role refs.
    // The H1 path must not first reject a longer legacy HEPTA-* string.
    for(int width=64;width<=96;width+=32) {
    auto longSnap=snap;
    longSnap.originalCommandKey=std::string(width,'a');
    auto longReservation=r;
    longReservation.frozen.originalCommandKey=longSnap.originalCommandKey;
    Check(hepta_bracket_h1::BuildFrozenRefs(
          longReservation.frozen,longReservation.ids,reason),
          "valid long original command failed frozen H1 derivation");
    auto longNative=BuildNeverSentNativeReservedH1Pair(
        longSnap,longReservation,100500);
    Check(longNative.parent.orderRef==longReservation.ids.parentRef &&
          longNative.stop.orderRef==longReservation.ids.childRef &&
          longNative.stop.ocaGroup==longReservation.ids.ocaGroup &&
          longNative.parent.orderRef.size()==45 &&
          longNative.stop.orderRef.size()==45 &&
          !longNative.brokerSendEnabled && !longNative.driverStopAuthorized,
          "H1 native order rejected a legal long original command");
    bool rejectedLegacy=false;
    try { (void)BuildNeverSentNativePair(longSnap,100500); }
    catch(const Refusal& e) {
        rejectedLegacy=width>80 ?
            std::string(e.what())=="BRACKET_EXACT_PAPER_SCOPE_REQUIRED" :
            std::string(e.what())=="BRACKET_SHADOW_ORDER_REF_EXCEEDS_IB_LIMIT";
    }
    Check(rejectedLegacy,"legacy long ref silently accepted");
    }
    auto tooLong=snap;
    tooLong.originalCommandKey=std::string(97,'a');
    auto invalid=r;
    invalid.frozen.originalCommandKey=tooLong.originalCommandKey;
    Check(!hepta_bracket_h1::BuildFrozenRefs(
        invalid.frozen,invalid.ids,reason),
        "oversized H1 original key crossed frozen identity bound");
    r.uncertain=true;
    try {
        (void)BuildNeverSentNativeReservedH1Pair(snap,r,100500);
        throw std::runtime_error("unknown OMS result authorized native H1");
    } catch(const hepta_execution_stop_research::Refusal&) {}
    r.uncertain=false;
    snap.reservedChildOrderId++;
    try {
        (void)BuildNeverSentNativeReservedH1Pair(snap,r,100500);
        throw std::runtime_error("substituted native child ID accepted");
    } catch(const hepta_execution_stop_research::Refusal&) {}
}
void H1NativeOcaSiblingOnlyAfterWorkingStop(const std::string& side) {
    const auto snap=Original(side);
    const auto legacy=ProposeUnsentBracket(snap,100500);
    hepta_bracket_h1::FrozenBracket f;
    f.originalCommandKey=snap.originalCommandKey;
    f.account=snap.account; f.owner=snap.owner;
    f.sessionId="isolated-h1-oca-session";
    f.executionDomain=snap.executionDomain;
    f.side=snap.action;f.contractId=snap.contractId;
    f.clientId=snap.clientId;
    f.parentOrderId=snap.reservedParentOrderId;
    f.childOrderId=snap.reservedChildOrderId;
    f.connectionEpoch=snap.connectionEpoch;
    f.positionGeneration=snap.originalPositionGeneration;
    f.ownerFenceToken=88;f.intentObservedAtMs=snap.observedAtMs;
    f.quantity=snap.quantity;
    f.referencePrice=side=="BUY"?snap.ask:snap.bid;
    f.stopPrice=legacy.stop.intent.auxPrice;
    f.minTick=snap.minTick;
    hepta_bracket_h1::Reservation r;
    std::string reason;
    Check(hepta_bracket_h1::BuildFrozenRefs(f,r.ids,reason),
          "H1 OCA original identity derivation failed");
    r.frozen=f;r.replayComplete=true;
    auto state=OcaState(legacy);
    state.originalStopOrderRef=r.ids.childRef;
    state.brokerOcaGroup=r.ids.ocaGroup;
    r.latestNativeOpenOrderEpoch=state.connectionEpoch;
    r.latestNativeStopPermanentId=state.stopPermanentId;
    const auto out=ProposeUnsentH1OcaExit(r,state,100500);
    Check(out.contract.conId==f.contractId &&
          out.exit.orderId==state.reservedExitOrderId &&
          out.exit.clientId==f.clientId && out.exit.account==f.account &&
          out.exit.parentId==0 && out.exit.transmit &&
          out.exit.orderType=="MKT" && out.exit.action==legacy.stop.intent.action &&
          out.exit.tif=="DAY" && out.exit.ocaType==1 &&
          out.exit.ocaGroup==r.ids.ocaGroup &&
          out.exit.orderRef.size()==45 &&
          out.exit.orderRef.compare(0,2,"H1")==0 &&
          out.exit.orderRef!=r.ids.parentRef &&
          out.exit.orderRef!=r.ids.childRef &&
          out.exit.orderRef!=r.ids.ocaGroup &&
          DecimalFunctions::decimalToDouble(out.exit.totalQuantity)==f.quantity &&
          !out.sendAuthority && !out.mayStopDriver &&
          !out.automaticTradingEnabled,
          "H1 OCA sibling native identity/quantity/permissions invalid");
    auto reject=[&](const BrokerOcaStopObservation& witness,
                    const char* expected) {
        try {(void)ProposeUnsentH1OcaExit(r,witness,100500);}
        catch(const Refusal& e) {
            Check(std::string(e.what())==expected,
                  "wrong H1 OCA refusal identity");return;
        }
        throw std::runtime_error("untrusted H1 OCA evidence accepted");
    };
    // A reconnect with the SAME native stop permID must preserve the
    // exact original close identity, even with a higher observation epoch.
    auto renewed=state;
    ++renewed.connectionEpoch;
    renewed.positionConnectionEpoch=renewed.connectionEpoch;
    renewed.activeOrdersConnectionEpoch=renewed.connectionEpoch;
    auto reattested=r;
    reattested.latestNativeOpenOrderEpoch=renewed.connectionEpoch;
    const auto sameClose=ProposeUnsentH1OcaExit(reattested,renewed,100500);
    Check(sameClose.exit.orderRef==out.exit.orderRef &&
          sameClose.exit.ocaGroup==out.exit.ocaGroup,
          "reconnected original stop changed idempotent H1 close identity");
    auto wrong=state;wrong.brokerOcaGroup=r.ids.parentRef;
    reject(wrong,"OCA_EXACT_ORIGINAL_ORDER_SCOPE_MISMATCH");
    wrong=state;wrong.originalStopOrderRef=legacy.stop.intent.orderRef;
    reject(wrong,"OCA_EXACT_ORIGINAL_ORDER_SCOPE_MISMATCH");
    wrong=state;wrong.noNewEntriesAcknowledged=false;
    reject(wrong,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    wrong=state;wrong.brokerOrderSetComplete=false;
    reject(wrong,"OCA_ORIGINAL_STOP_OR_FENCE_UNPROVEN");
    wrong=state;wrong.stopRemainingQuantity/=2;
    reject(wrong,"OCA_PARTIAL_OR_REVERSED_EXPOSURE");
    wrong=state;wrong.activeOrdersConnectionEpoch++;
    reject(wrong,"OCA_BROKER_EPOCH_OR_FRESHNESS_MISMATCH");
    wrong=state;wrong.stopPermanentId=0;
    reject(wrong,"H1_OCA_NATIVE_PERM_ID_OR_REATTESTATION_MISMATCH");
    wrong=state;++wrong.stopPermanentId;
    reject(wrong,"H1_OCA_NATIVE_PERM_ID_OR_REATTESTATION_MISMATCH");
    wrong=state;++wrong.connectionEpoch;
    reject(wrong,"H1_OCA_NATIVE_PERM_ID_OR_REATTESTATION_MISMATCH");
    auto insufficient=r;insufficient.latestNativeStopPermanentId=0;
    bool refusedWithoutNativeStop=false;
    try {(void)ProposeUnsentH1OcaExit(insufficient,state,100500);}
    catch(const Refusal& e) {
        refusedWithoutNativeStop=std::string(e.what())==
            "H1_OCA_NATIVE_PERM_ID_OR_REATTESTATION_MISMATCH";
    }
    Check(refusedWithoutNativeStop,
          "H1 OCA positive working flag hid missing Broker permID");
    auto invalid=r;invalid.uncertain=true;
    try {(void)ProposeUnsentH1OcaExit(invalid,state,100500);}
    catch(const Refusal& e) {
        Check(std::string(e.what())=="H1_OCA_ORIGINAL_RESERVATION_UNVERIFIED",
              "unknown H1 reservation gave OCA exit");return;
    }
    throw std::runtime_error("unknown H1 reservation generated OCA exit");
}
void BadModeNoNative() {
    auto s=Original("BUY");s.executionDomain="LIVE";
    try { (void)BuildNeverSentNativePair(s,100500); }
    catch(const Refusal&) {return;}
    throw std::runtime_error("LIVE routed through PAPER shadow");
}
}
int main() {
    try {
        NativeWireCoherence("BUY"); NativeWireCoherence("SELL");
        NativeOcaSiblingOnlyAfterWorkingStop("BUY");
        NativeOcaSiblingOnlyAfterWorkingStop("SELL");
        RejectUnsafeOcaEvidence();
        H1NativeOriginalIdentity("BUY");H1NativeOriginalIdentity("SELL");
        H1NativeOcaSiblingOnlyAfterWorkingStop("BUY");
        H1NativeOcaSiblingOnlyAfterWorkingStop("SELL");
        BadContractNoNative(); LegacyOnePipTickRefused(); BadModeNoNative();
        std::cout<<"ib_native_bracket_shadow_fields=PASS\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr<<"FAILED "<<e.what()<<'\n';
        return 1;
    }
}
