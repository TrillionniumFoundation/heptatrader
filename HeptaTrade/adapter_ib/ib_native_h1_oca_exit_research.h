#pragma once
// Issue #331: frozen H1 OCA ordinary-exit PREPARATION ONLY.
// Actual IB CASH/IDEALPRO stop + later OCA sibling semantics are NOT
// qualified in PAPER. Never call EClient::placeOrder from this file.
// A broker OCA "with block" can reduce an overfill risk but cannot prove
// the position is still open at a later dispatch boundary.
#include "ib_native_h1_pair_research.h"
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>

namespace hepta_ib_h1_oca_exit_research {

struct BrokerStopAndPositionEvidence final {
    std::string account, owner, sessionId, originalCommandKey;
    std::string exactStopRef, exactOcaGroup;
    std::string stopStatus, stopTimeInForce, stopAction;
    std::string originalParentExecutionId;
    long contractId=0, stopNativeOrderId=0;
    long originalParentPermanentId=0, expectedStopPermanentId=0;
    long brokerStopPermanentId=0, proposedExitOrderId=0;
    int brokerClientId=-1, stopOcaType=0;
    std::uint64_t brokerEpoch=0, positionEpoch=0, openOrdersEpoch=0;
    std::uint64_t observedAtMs=0, validUntilMs=0;
    bool exactOriginalBrokerExecution=false;
    bool brokerPositionAuthoritative=false;
    bool globalActiveOrdersComplete=false;
    bool originalStopOpenOrderAttested=false;
    bool originalStopOcaServerAttested=false;
    bool originalStopTriggerMethodAttested=false;
    bool ownerNewEntryFenceAttested=false;
    bool exactExitIdReserved=false;
    bool noUnknownOriginalCommands=false;
    bool noCancelOrReplacePending=false;
    int activeOriginalAccountOrders=-1;
    double originalParentFilledQty=0.0;
    double brokerSignedPosition=0.0;
    double stopOrderTotalQty=0.0;
    double stopOrderFilledQty=0.0;
    double stopOrderRemainingQty=0.0;
    double stopPrice=0.0;
};

struct Candidate final {
    Contract contract;
    Order normalExit;
    std::string exitCorrelation;
    bool sendAuthority=false;
    bool maintenanceAuthority=false;
    bool workingProtectionProven=false;
    bool mayRetry=false;
};

// Pure construction, never an order send admission; snapshot provenance flags
// must be replaced by actual Coordinator/IB owner proofs before deployment.
// For snapshot after reconnect, fresh original permanent-ID reattestation in
// the NEW epoch is mandatory; an old openOrder replay cannot qualify.
inline bool BuildUnsentH1OrdinaryOcaExit(
    const hepta_bracket_h1::FrozenBracket& original,
    const hepta_bracket_h1::BoundRefs& refs,
    const BrokerStopAndPositionEvidence& s,
    std::uint64_t nowMs, Candidate& out, std::string& reason)
{
    out=Candidate{};
    if(!hepta_bracket_h1::VerifyFrozenRefs(original,refs,reason))return false;
    if(original.executionDomain!="PAPER" ||
       s.account!=original.account || s.owner!=original.owner ||
       s.sessionId!=original.sessionId ||
       s.originalCommandKey!=original.originalCommandKey ||
       s.contractId!=original.contractId || s.contractId!=12087792 ||
       s.brokerClientId!=original.clientId ||
       s.stopNativeOrderId!=original.childOrderId ||
       s.exactStopRef!=refs.childRef ||
       s.exactOcaGroup!=refs.ocaGroup ||
       s.stopOcaType!=1 ||
       s.stopStatus!="Submitted" ||
       s.stopTimeInForce!="GTC" ||
       s.stopAction!=(original.side=="BUY"?"SELL":"BUY")) {
        reason="H1_OCA_STOP_OR_OWNER_IDENTITY_MISMATCH";return false;
    }
    if(!s.exactOriginalBrokerExecution ||
       s.originalParentExecutionId.empty() ||
       !s.brokerPositionAuthoritative ||
       !s.globalActiveOrdersComplete ||
       !s.originalStopOpenOrderAttested ||
       !s.originalStopOcaServerAttested ||
       !s.originalStopTriggerMethodAttested ||
       !s.ownerNewEntryFenceAttested ||
       !s.exactExitIdReserved ||
       !s.noUnknownOriginalCommands ||
       !s.noCancelOrReplacePending ||
       s.activeOriginalAccountOrders!=1) {
        reason="H1_OCA_ACCOUNT_ORDER_OR_ORIGINAL_EXIT_UNPROVEN";return false;
    }
    if(s.brokerEpoch<original.connectionEpoch ||
       s.brokerEpoch==0 ||
       s.positionEpoch!=s.brokerEpoch ||
       s.openOrdersEpoch!=s.brokerEpoch ||
       s.observedAtMs==0 || s.observedAtMs>nowMs ||
       s.validUntilMs<s.observedAtMs ||
       s.validUntilMs<nowMs ||
       s.validUntilMs-s.observedAtMs>3000) {
        reason="H1_OCA_BROKER_EPOCH_OR_STALE_SNAPSHOT";return false;
    }
    if(s.originalParentPermanentId<=0 ||
       s.expectedStopPermanentId<=0 ||
       s.brokerStopPermanentId!=s.expectedStopPermanentId ||
       s.brokerStopPermanentId==s.originalParentPermanentId ||
       s.proposedExitOrderId<=original.childOrderId ||
       s.proposedExitOrderId>=std::numeric_limits<std::int32_t>::max()-1) {
        reason="H1_OCA_PERMANENT_ID_OR_NATIVE_LEASE_MISMATCH";return false;
    }
    const double qty=original.quantity;
    const double signedQty=(original.side=="BUY"?qty:-qty);
    if(!std::isfinite(s.brokerSignedPosition) ||
       s.brokerSignedPosition!=signedQty ||
       !std::isfinite(s.originalParentFilledQty) ||
       s.originalParentFilledQty!=qty ||
       !std::isfinite(s.stopOrderTotalQty) ||
       s.stopOrderTotalQty!=qty ||
       !std::isfinite(s.stopOrderRemainingQty) ||
       s.stopOrderRemainingQty!=qty ||
       !std::isfinite(s.stopOrderFilledQty) ||
       s.stopOrderFilledQty!=0.0 ||
       !std::isfinite(s.stopPrice) ||
       s.stopPrice!=original.stopPrice) {
        reason="H1_OCA_PARTIAL_FILL_POSITION_OR_STOP_CHANGED";return false;
    }
    // Preserve the original role-domain hierarchy, but never alias the
    // Parent, Stop, or OCA group H1 value for a NEW orderRef.
    std::string canonical;
    hepta_bracket_h1::Field(canonical,"hepta-bracket-h1-role/v1");
    hepta_bracket_h1::Field(canonical,refs.groupCorrelation);
    hepta_bracket_h1::Field(canonical,"ORDINARY_EXIT");
    Candidate result;
    result.exitCorrelation=hepta_bracket_h1::HashCanonical(canonical);
    if(!IbVenueCorrelationCodec::EncodeOrderRef(
           result.exitCorrelation,result.normalExit.orderRef,reason))return false;
    if(result.normalExit.orderRef==refs.parentRef ||
       result.normalExit.orderRef==refs.childRef ||
       result.normalExit.orderRef==refs.ocaGroup) {
        reason="H1_OCA_EXIT_ROLE_COLLISION";return false;
    }
    result.contract.conId=original.contractId;
    result.contract.symbol="EUR";
    result.contract.currency="USD";
    result.contract.secType="CASH";
    result.contract.exchange="IDEALPRO";
    result.normalExit.orderId=s.proposedExitOrderId;
    result.normalExit.clientId=s.brokerClientId;
    result.normalExit.account=s.account;
    result.normalExit.action=s.stopAction;
    result.normalExit.orderType="MKT";
    result.normalExit.tif="DAY";
    result.normalExit.parentId=0;
    result.normalExit.transmit=true;
    result.normalExit.totalQuantity=DecimalFunctions::doubleToDecimal(qty);
    result.normalExit.ocaGroup=refs.ocaGroup;
    result.normalExit.ocaType=1;
    result.normalExit.outsideRth=false;
    result.normalExit.auxPrice=0.0;
    result.normalExit.lmtPrice=0.0;
    if(result.normalExit.totalQuantity==UNSET_DECIMAL ||
       DecimalFunctions::decimalToDouble(
           result.normalExit.totalQuantity)!=qty) {
        reason="H1_OCA_EXIT_SDK_QUANTITY_UNREPRESENTABLE";return false;
    }
    // Never infer "working" from a synthetic witness or live market snapshot.
    out=std::move(result);
    reason.clear();
    return true;
}

} // namespace hepta_ib_h1_oca_exit_research
