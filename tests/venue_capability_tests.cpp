#include "adapter_ctp/ctp_gateway_adapter.h"
#include "adapter_xt/xt_gateway_adapter.h"

#include <cstdlib>
#include <limits>
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
        for (int i = 0; i < 10000; ++i)
        {
            Require(!xt.Connect() && !xt.IsConnected(), "missing transport stays disconnected");
            Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
                    "no repeated call enables submission");
            Require(orderId == 0, "unsupported transport never allocates an order ID");
        }
        for (double hostile : {0.0, -1.0, std::numeric_limits<double>::infinity(),
                               std::numeric_limits<double>::quiet_NaN()})
        {
            Require(!xt.PlaceOrder("", "", hostile, hostile, nullptr),
                    "no numeric or identity input enables a missing transport");
            Require(xt.LastRejectReason() == "XT_TRANSPORT_NOT_IMPLEMENTED",
                    "absent transport is the authority-bearing failure");
        }
        xt.Disconnect();
        Require(!xt.IsConnected(), "disconnect never grants connectivity");
        cfg.mode = "INVALID";
        Require(!xt.Init(cfg), "invalid mode rejected after valid initialization");
        Require(!xt.Connect() && !xt.IsConnected(), "invalid reinit leaves no transport");
        cfg.mode = "XT";
        Require(xt.Init(cfg) && !xt.Connect(), "valid reinit still has no transport");
        Require(!xt.CancelOrder(1), "XT scaffold must reject cancellation");
        Require(!xt.ReqAccountSummary() && !xt.ReqPositions() &&
                    !xt.ReqMktData("600000.SH"),
                "XT scaffold must reject broker queries");
    }
    std::cout << "venue_capability_tests: PASS" << std::endl;
    return 0;
}
