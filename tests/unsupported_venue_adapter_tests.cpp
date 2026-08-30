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
    assert(orderId == -1);
    assert(!xt.CancelOrder(1));

    // Drain request-level unsupported diagnostics before exercising the
    // callback bridge; those diagnostics intentionally retain operation-
    // specific detail text.
    XTEvent event;
    bool sawError = false;
    while (xt.TryDequeueEvent(event))
    {
        sawError = sawError || event.type == XTEventType::Error;
        assert(event.type == XTEventType::Error);
    }
    assert(sawError);

    // The callback bridge is a future transport seam, not an authority in
    // the unsupported scaffold.  Synthetic callback injection must remain a
    // typed rejection and must never advertise connection, order, fill or
    // acknowledgement success.
    xt.OnXtConnected();
    xt.OnXtDisconnected("injected");
    xt.OnXtAccountStatus("ready");
    xt.OnXtAsset(100.0, 50.0);
    xt.OnXtPosition("EUR.USD", 1.0);
    xt.OnXtOrderStatus(7, "Filled", "injected");
    xt.OnXtTrade(7, "EUR.USD", "BUY", 1.0, 1.0);
    xt.OnXtOrderError(7, "E", "injected");
    xt.OnXtCancelError(7, "E", "injected");
    xt.OnXtAsyncOrderResponse(7, true, "injected");
    xt.OnXtAsyncCancelResponse(7, true, "injected");
    assert(std::string(xt.GetStatusString()) == "XT_TRANSPORT_NOT_BUILT");

    sawError = false;
    while (xt.TryDequeueEvent(event))
    {
        sawError = sawError || event.type == XTEventType::Error;
        assert(event.type == XTEventType::Error);
        assert(event.value == "XT_TRANSPORT_NOT_BUILT");
    }
    assert(sawError);
    return 0;
}
