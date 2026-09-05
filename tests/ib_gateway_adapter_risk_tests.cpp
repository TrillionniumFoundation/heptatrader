#include "../HeptaTrade/adapter_ib/ib_gateway_adapter.h"

#include <cassert>
#include <deque>
#include <memory>
#include <string>
#include <utility>

namespace
{
class FakeIbApi final : public IIBApiWrapper
{
public:
    bool Connect(const IBConnectParams&) override
    {
        m_connected = true;
        return true;
    }
    void SetConnectionEpoch(std::uint64_t epoch) override
    {
        m_epoch = epoch;
    }
    std::uint64_t GetConnectionEpoch() const override
    {
        return m_epoch;
    }
    void Disconnect() override
    {
        m_connected = false;
    }
    bool IsConnected() const override
    {
        return m_connected;
    }
    const char* GetStatusString() const override
    {
        return m_connected ? "FAKE_CONNECTED" : "FAKE_DISCONNECTED";
    }

    bool ReqAccountSummary() override
    {
        IBEvent value;
        value.type = IBEventType::AccountValue;
        value.account = "DU123456";
        value.key = "NetLiquidation";
        value.value = "100000";
        value.number = 100000.0;
        Push(value);
        IBEvent end;
        end.type = IBEventType::AccountSummaryEnd;
        end.account = "DU123456";
        Push(end);
        return true;
    }
    bool ReqPositions() override
    {
        IBEvent position;
        position.type = IBEventType::PositionSnapshotItem;
        position.account = "DU123456";
        position.key = "CONID:42";
        position.contract = m_positionContract;
        position.number = 10.0;
        Push(position);
        IBEvent end;
        end.type = IBEventType::PositionEnd;
        end.account = "DU123456";
        Push(end);
        return true;
    }
    bool ReqOpenOrders() override
    {
        IBEvent end;
        end.type = IBEventType::OpenOrderEnd;
        end.account = "DU123456";
        Push(end);
        return true;
    }
    bool ReqAllOpenOrders() override
    {
        return ReqOpenOrders();
    }
    bool ReqMktData(int, const IBContractLite&) override
    {
        return true;
    }
    bool CancelMktData(int) override
    {
        return true;
    }
    bool PlaceOrder(long, const IBContractLite&, const IBOrderLite&) override
    {
        placed = true;
        return true;
    }
    bool CancelOrder(long) override
    {
        return true;
    }
    bool PollOnce(int) override
    {
        return !m_events.empty();
    }
    bool TryDequeueEvent(IBEvent& event) override
    {
        if (m_events.empty()) return false;
        event = std::move(m_events.front());
        m_events.pop_front();
        return true;
    }
    long GetLastValidOrderId() const override
    {
        return 100;
    }

    IBContractLite m_positionContract;
    bool placed = false;

private:
    void Push(IBEvent event)
    {
        event.connectionEpoch = m_epoch;
        m_events.push_back(std::move(event));
    }

    bool m_connected = false;
    std::uint64_t m_epoch = 0;
    std::deque<IBEvent> m_events;
};

void Drain(HeptaIBGatewayAdapter& adapter)
{
    IBEvent event;
    while (adapter.TryDequeueEvent(event)) {}
}

void TestCanonicalPositionIdentityFeedsCommonPolicy()
{
    std::unique_ptr<FakeIbApi> fake(new FakeIbApi());
    FakeIbApi* raw = fake.get();
    raw->m_positionContract.symbol = "ES";
    raw->m_positionContract.secType = "FUT";
    raw->m_positionContract.exchange = "CME";
    raw->m_positionContract.currency = "USD";
    raw->m_positionContract.lastTradeDateOrContractMonth = "202612";

    HeptaIBConfig config;
    config.account = "DU123456";
    config.risk.enableOrderSubmission = true;
    config.risk.maxOrderQuantity = 20.0;
    config.risk.maxDailyOrders = 10;
    config.risk.maxOrderNotional = 10000.0;
    config.risk.maxOrdersPerMinute = 10;
    config.risk.maxActiveOrders = 10;
    config.risk.maxGrossPosition = 20.0;
    config.risk.globalKillSwitch = true;
    config.risk.flattenOnly = true;

    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(config));
    assert(adapter.Connect());
    adapter.UpdateReferencePrice(100.0);
    assert(adapter.ReqRiskRefresh());
    Drain(adapter);
    assert(adapter.ReqAuthoritativeOpenOrders());
    Drain(adapter);
    const IBAuthoritativeRiskSnapshot risk =
        adapter.GetAuthoritativeRiskSnapshot();
    assert(risk.complete);
    assert(adapter.GetAuthoritativeCorrelationSnapshot().complete);

    IBOrderLite reduction;
    reduction.action = "SELL";
    reduction.orderType = "MKT";
    reduction.totalQuantity = 2.0;
    long orderId = -1;
    const std::string reductionCorrelation =
        "hepta-v1-sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    assert(adapter.PlaceOrderCorrelated(
        raw->m_positionContract, reduction, reductionCorrelation, &orderId));
    assert(orderId > 0);
    assert(raw->placed);

    // Crossing through zero is not a strict reduction even though gross
    // exposure numerically falls; the common policy must reject it.
    IBOrderLite crossZero = reduction;
    crossZero.totalQuantity = 15.0;
    const std::string crossZeroCorrelation =
        "hepta-v1-sha256:abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd";
    assert(!adapter.PlaceOrderCorrelated(
        raw->m_positionContract, crossZero, crossZeroCorrelation, nullptr));
    assert(adapter.GetLastRejectReason() == "RISK_REDUCE_ONLY_CROSS_ZERO");
}

void TestLiveMutationIsUnsupportedEvenWithLegacyOptInFlags()
{
    std::unique_ptr<FakeIbApi> fake(new FakeIbApi());
    HeptaIBConfig config;
    config.account = "U123456";
    config.risk.enableOrderSubmission = true;
    // Permit the identity through the account allow-list so this assertion
    // reaches the explicit LIVE boundary rather than an earlier config gate.
    config.risk.accountWhitelist = {"*"};
    config.risk.allowLiveTrading = true;
    config.risk.liveKillSwitch = false;

    HeptaIBGatewayAdapter adapter(std::move(fake));
    assert(adapter.Init(config));
    assert(adapter.Connect());
    std::string reason;
    assert(!adapter.RunPreflightChecks(reason));
    assert(reason.rfind("RISK_LIVE_UNSUPPORTED", 0) == 0);
}
}  // namespace

int main()
{
    // The non-IB build uses the deterministic transport stub, but still
    // compiles and links the complete active adapter against the shared risk
    // policy.  A newly initialized adapter must remain fail-closed until the
    // broker connection/identity and authoritative state are ready.
    HeptaIBConfig config;
    config.account = "DU123456";
    config.risk.enableOrderSubmission = true;

    HeptaIBGatewayAdapter adapter;
    assert(adapter.Init(config));

    std::string reason;
    assert(!adapter.RunPreflightChecks(reason));
    assert(reason.rfind("RISK_", 0) == 0);
    TestLiveMutationIsUnsupportedEvenWithLegacyOptInFlags();
    TestCanonicalPositionIdentityFeedsCommonPolicy();
    return 0;
}
