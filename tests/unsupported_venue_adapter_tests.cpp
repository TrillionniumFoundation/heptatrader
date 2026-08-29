#include "adapter_ctp/ctp_gateway_adapter.h"
#include "adapter_xt/xt_gateway_adapter.h"

#include <cassert>
#include <string>

int main()
{
    HeptaCTPGatewayAdapter ctp;
    HeptaCTPConfig ctpConfig;
    assert(!ctp.Init(ctpConfig));
    assert(!ctp.Connect());
    assert(std::string(ctp.GetStatusString()) == "VENUE_NOT_IMPLEMENTED");
    ctp.Disconnect();
    assert(std::string(ctp.GetStatusString()) == "VENUE_NOT_IMPLEMENTED");

    HeptaXTGatewayAdapter xt;
    HeptaXTConfig xtConfig;
    assert(!xt.Init(xtConfig));
    assert(!xt.Connect());
    assert(std::string(xt.GetStatusString()) == "XT_TRANSPORT_NOT_BUILT");
    assert(!xt.PollOnce(1));
    assert(!xt.ReqAccountSummary());
    assert(!xt.ReqPositions());
    assert(!xt.ReqMktData("EUR.USD"));

    long long orderId = -1;
    assert(!xt.PlaceOrder("EUR.USD", "BUY", 1.0, 1.0, &orderId));
    assert(orderId == 0);
    assert(!xt.CancelOrder(1));

    XTEvent event;
    bool sawError = false;
    while (xt.TryDequeueEvent(event))
        sawError = sawError || event.type == XTEventType::Error;
    assert(sawError);
    return 0;
}
