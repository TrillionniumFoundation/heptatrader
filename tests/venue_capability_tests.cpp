#include "adapter_ctp/ctp_gateway_adapter.h"
#include "adapter_xt/xt_gateway_adapter.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {
void Require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << std::endl;
        std::exit(1);
    }
}
}

int main() {
    {
        HeptaCTPGatewayAdapter ctp;
        HeptaCTPConfig cfg;
        Require(ctp.Init(cfg), "CTP scaffold init should validate its mode");
        Require(!ctp.Connect(), "CTP scaffold must not fake a connection");
        Require(!ctp.IsConnected(), "CTP must remain disconnected");
        Require(std::string(ctp.CapabilityStatus()) ==
                    "EXPERIMENTAL_NO_TRANSPORT",
                "CTP capability must be explicit");
        Require(ctp.LastError() == "CTP_TRANSPORT_NOT_IMPLEMENTED",
                "CTP unsupported reason must be stable");
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.risk.enableOrderSubmission = true;
        Require(xt.Init(cfg), "XT scaffold init should validate its mode");
        Require(!xt.Connect(), "XT scaffold must not fake a connection");
        Require(std::string(xt.CapabilityStatus()) ==
                    "EXPERIMENTAL_NO_TRANSPORT",
                "XT capability must be explicit");
        long long orderId = -1;
        Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
                "XT scaffold must reject placement");
        Require(orderId == 0, "XT scaffold must not manufacture an order id");
        Require(xt.LastRejectReason() == "XT_TRANSPORT_NOT_IMPLEMENTED",
                "XT unsupported reason must be stable");
        xt.OnXtConnected();
        Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
                "manual callback must not enable a missing transport");
        xt.OnXtAccountStatus("connected");
        xt.OnXtAsset(1000000.0, 1000000.0);
        xt.OnXtPosition("600000.SH", 100.0);
        xt.OnXtOrderStatus(1, "Filled", "not Broker evidence");
        xt.OnXtTrade(1, "600000.SH", "BUY", 100.0, 10.0);
        xt.OnXtOrderError(1, "error", "detail");
        xt.OnXtCancelError(1, "error", "detail");
        xt.OnXtAsyncOrderResponse(1, true, "not an acknowledgement");
        xt.OnXtAsyncCancelResponse(1, true, "not an acknowledgement");
        XTEvent event;
        while (xt.TryDequeueEvent(event))
            Require(event.type == XTEventType::Error, "callbacks cannot create economic evidence");
        for (int i = 0; i < 10000; ++i) xt.Connect();
        int diagnostics = 0;
        while (xt.TryDequeueEvent(event))
        {
            ++diagnostics;
            Require(event.type == XTEventType::Error, "unsupported events are errors only");
        }
        Require(diagnostics > 0 && diagnostics <= 64, "unsupported diagnostic queue must be bounded");
        xt.OnXtDisconnected();
        Require(!xt.PollOnce(0), "scaffold never becomes a transport");
        Require(!xt.CancelOrder(1), "XT scaffold must reject cancellation");
        Require(!xt.ReqAccountSummary() && !xt.ReqPositions() &&
                    !xt.ReqMktData("600000.SH"),
                "XT scaffold must reject broker queries");
    }
    std::cout << "venue_capability_tests: PASS" << std::endl;
    return 0;
}
