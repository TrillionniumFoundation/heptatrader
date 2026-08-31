#include "../HeptaTrade/adapter_ctp/ctp_gateway_adapter.h"
#include "../HeptaTrade/adapter_xt/xt_gateway_adapter.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {

void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
              << expression << "\n";
    std::abort();
}

#define REQUIRE(expression) \
    Require(static_cast<bool>(expression), #expression, __LINE__)

void TestCtpScaffoldFailsClosed()
{
    HeptaCTPGatewayAdapter adapter;
    HeptaCTPConfig config;
    REQUIRE(!adapter.Init(config));
    REQUIRE(!adapter.Connect());
    adapter.Disconnect();
}

void TestXtScaffoldNeverAcknowledgesOutboundMutations()
{
    HeptaXTGatewayAdapter adapter;
    HeptaXTConfig config;
    config.readOnly = false;
    config.risk.enableOrderSubmission = true;

    REQUIRE(!adapter.Init(config));
    REQUIRE(std::string(adapter.GetStatusString()) ==
            "XT_TRANSPORT_UNAVAILABLE");
    REQUIRE(!adapter.Connect());
    REQUIRE(!adapter.ReqAccountSummary());
    REQUIRE(!adapter.ReqPositions());
    REQUIRE(!adapter.ReqMktData("600000.SH"));

    long long orderId = -1;
    REQUIRE(!adapter.PlaceOrder("600000.SH", "BUY", 100.0, 10.0,
                                &orderId));
    REQUIRE(orderId == 0);
    REQUIRE(!adapter.CancelOrder(1));

    // Even an injected inbound connected callback must not enable outbound
    // sends while no reviewed transport binding exists.
    adapter.OnXtConnected();
    REQUIRE(!adapter.PlaceOrder("600000.SH", "SELL", 100.0, 10.0,
                                &orderId));

    bool sawUnavailable = false;
    XTEvent event;
    while (adapter.TryDequeueEvent(event))
    {
        if (event.type == XTEventType::Error &&
            event.key == "XT_TRANSPORT_UNAVAILABLE")
            sawUnavailable = true;
        REQUIRE(event.type != XTEventType::OrderAck);
        REQUIRE(event.type != XTEventType::CancelAck);
    }
    REQUIRE(sawUnavailable);
}

} // namespace

int main()
{
    TestCtpScaffoldFailsClosed();
    TestXtScaffoldNeverAcknowledgesOutboundMutations();
    return 0;
}
