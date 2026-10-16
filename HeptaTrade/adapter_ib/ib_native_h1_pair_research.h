#pragma once
// IBKR SDK-native EUR/USD Parent+Stop preparation from EXACT H1 original
// Execution identity. Does not contain EClient/placeOrder or a transport API.
// All order identifiers must already be reserved and committed by the actual
// Coordinator+Adapter+OMS boundary. A valid pair is NOT a Broker working stop.
#include "../execution/ib_bracket_h1_identity.h"
#include "Contract.h"
#include "Order.h"
#include "Decimal.h"
#include <cmath>
#include <string>
#include <utility>

namespace hepta_ib_native_h1_transport {
struct NativePair final {
    Contract contract;
    Order parent;
    Order child;
    bool brokerSendAuthorized = false;
    bool stopWorking = false;
    bool driverStopAuthorized = false;
};
inline bool PrepareNativePaperPair(
    const hepta_bracket_h1::FrozenBracket& frozen,
    const hepta_bracket_h1::BoundRefs& refs,
    NativePair& out, std::string& reason)
{
    out = NativePair{};
    if (!hepta_bracket_h1::VerifyFrozenRefs(frozen,refs,reason))
        return false;
    if (frozen.clientId <= 0 || frozen.parentOrderId <= 0 ||
        frozen.childOrderId != frozen.parentOrderId+1 ||
        frozen.contractId != 12087792 ||
        frozen.account.compare(0,2,"DU") != 0 ||
        refs.parentRef.size()!=45 || refs.childRef.size()!=45 ||
        refs.ocaGroup.size()!=45) {
        reason="H1_NATIVE_PARENT_CHILD_SCOPE_MISMATCH";
        return false;
    }
    NativePair result;
    result.contract.conId = frozen.contractId;
    result.contract.symbol = "EUR";
    result.contract.currency = "USD";
    result.contract.secType = "CASH";
    result.contract.exchange = "IDEALPRO";

    result.parent.orderId = frozen.parentOrderId;
    result.parent.clientId = frozen.clientId;
    result.parent.account = frozen.account;
    result.parent.action = frozen.side;
    result.parent.orderType = "MKT";
    result.parent.tif = "DAY";
    result.parent.parentId = 0;
    result.parent.transmit = false;
    result.parent.totalQuantity =
        DecimalFunctions::doubleToDecimal(frozen.quantity);
    result.parent.orderRef = refs.parentRef;
    result.parent.lmtPrice = 0.0;
    result.parent.auxPrice = 0.0;

    result.child.orderId = frozen.childOrderId;
    result.child.clientId = frozen.clientId;
    result.child.account = frozen.account;
    result.child.action = (frozen.side=="BUY") ? "SELL" : "BUY";
    result.child.orderType = "STP";
    result.child.tif = "GTC";
    result.child.parentId = frozen.parentOrderId;
    result.child.transmit = true;
    result.child.totalQuantity =
        DecimalFunctions::doubleToDecimal(frozen.quantity);
    result.child.orderRef = refs.childRef;
    result.child.auxPrice = frozen.stopPrice;
    result.child.lmtPrice = 0.0;
    result.child.triggerMethod = 0; // default; actual IB trigger not qualified
    result.child.ocaGroup = refs.ocaGroup;
    result.child.ocaType = 1;       // proposed cancel-with-block; not qualified
    if (result.parent.totalQuantity == UNSET_DECIMAL ||
        result.child.totalQuantity == UNSET_DECIMAL ||
        DecimalFunctions::decimalToDouble(result.parent.totalQuantity) != frozen.quantity ||
        DecimalFunctions::decimalToDouble(result.child.totalQuantity) != frozen.quantity ||
        result.parent.orderId + 1 != result.child.orderId ||
        result.child.parentId != result.parent.orderId ||
        result.child.account != result.parent.account ||
        !std::isfinite(result.child.auxPrice) ||
        result.child.auxPrice <= 0.0) {
        reason="H1_NATIVE_DECIMAL_OR_GROUP_INCONSISTENT";
        return false;
    }
    out=std::move(result);
    reason.clear();return true;
}
} // namespace hepta_ib_native_h1_transport

#include "../execution/ib_bracket_h1_oms_reservation.h"
#include <vector>
namespace hepta_ib_native_h1_transport {
enum class PairDispatchStage {
    Refused,
    ParentPossiblySent,
    ChildPossiblySent,
    LocalCallsReturnedBrokerUnconfirmed
};
struct PairDispatchResult {
    PairDispatchStage stage=PairDispatchStage::Refused;
    std::string reason;
    bool liveProtectionProven=false;
    bool mayRetry=false;
};
// Compile in the isolated test executable ONLY. This is NOT a broker order
// egress interface in the installed Execution/IBApiWrapper target.
#ifdef HEPTA_IB_NATIVE_BRACKET_FAKE_DISPATCH_TEST
template<class IsolatedFakeEClient>
PairDispatchResult ExerciseDurablePairTransport(
    OmsJournal& isolatedJournal,
    const hepta_bracket_h1::Reservation& reserved,
    IsolatedFakeEClient& sender)
{
    PairDispatchResult out;
    if (!reserved.replayComplete || reserved.uncertain ||
        reserved.sendAuthorized || reserved.driverStopAllowed) {
        out.reason="H1_FAKE_TRANSPORT_INVALID_RESEARCH_SCOPE";return out;
    }
    std::vector<OmsJournalEvent> prior;
    const int count=isolatedJournal.Replay(
        [&](const OmsJournalEvent& e){ prior.push_back(e); });
    hepta_bracket_h1::Reservation validated;
    std::string reason;
    if (count<0 ||
        !hepta_bracket_h1::ConfirmReplay(
            prior,reserved.frozen,validated,reason) ||
        !hepta_bracket_h1::VerifyFrozenRefs(
            reserved.frozen,reserved.ids,reason)) {
        out.reason="H1_FAKE_TRANSPORT_JOURNAL_OR_IDENTITY_UNKNOWN";return out;
    }
    NativePair pair;
    if (!PrepareNativePaperPair(reserved.frozen,reserved.ids,pair,reason)) {
        out.reason="H1_FAKE_TRANSPORT_NATIVE_PAIR_INVALID";return out;
    }
    if(!hepta_bracket_h1::MarkPotentialSend(
        isolatedJournal,reserved,false,reason)){
        out.reason="H1_FAKE_TRANSPORT_PARENT_PRE_SEND_NOT_DURABLE";return out;
    }
    out.stage=PairDispatchStage::ParentPossiblySent;
    try {
        sender.placeOrder(pair.parent.orderId,pair.contract,pair.parent);
    } catch (...) {
        out.reason="H1_FAKE_TRANSPORT_PARENT_SEND_UNKNOWN";return out;
    }
    if(!hepta_bracket_h1::MarkPotentialSend(
        isolatedJournal,reserved,true,reason)) {
        out.reason="H1_FAKE_TRANSPORT_CHILD_PRE_SEND_NOT_DURABLE";return out;
    }
    out.stage=PairDispatchStage::ChildPossiblySent;
    try {
        sender.placeOrder(pair.child.orderId,pair.contract,pair.child);
    } catch (...) {
        out.reason="H1_FAKE_TRANSPORT_CHILD_SEND_UNKNOWN";return out;
    }
    out.stage=PairDispatchStage::LocalCallsReturnedBrokerUnconfirmed;
    out.reason="H1_FAKE_TRANSPORT_REQUIRES_REAL_IB_BROKER_CALLBACK";
    // Even a local SDK placeOrder return is NOT an original permanent-ID,
    // server-held protective order, OCA or independent stop witness.
    return out;
}
#endif
} // namespace hepta_ib_native_h1_transport
