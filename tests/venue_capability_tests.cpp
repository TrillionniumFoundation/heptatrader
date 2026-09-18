#include "adapter_ctp/ctp_gateway_adapter.h"
#include "adapter_xt/xt_gateway_adapter.h"

#include <cstdlib>
#include <limits>
#include <iostream>
#include <sstream>
#include <string>

namespace {
void Require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << std::endl;
        std::exit(1);
    }
}

std::string ExtractJsonString(const std::string& json, const std::string& field)
{
    const std::string marker = "\"" + field + "\":\"";
    const std::size_t start = json.find(marker);
    Require(start != std::string::npos, "HXQ1 request string field missing");
    const std::size_t value = start + marker.size();
    const std::size_t end = json.find('"', value);
    Require(end != std::string::npos, "HXQ1 request string field unterminated");
    return json.substr(value, end - value);
}

std::uint64_t ExtractJsonUnsigned(const std::string& json, const std::string& field)
{
    const std::string marker = "\"" + field + "\":";
    const std::size_t start = json.find(marker);
    Require(start != std::string::npos, "HXQ1 request integer field missing");
    std::size_t value = start + marker.size();
    std::size_t end = value;
    while (end < json.size() && json[end] >= '0' && json[end] <= '9') ++end;
    Require(end > value, "HXQ1 request integer field invalid");
    return static_cast<std::uint64_t>(std::stoull(json.substr(value, end - value)));
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
        Require(!xt.Connect(), "XT without an admitted transport must not fake a connection");
        Require(std::string(xt.CapabilityStatus()) ==
                    "EXPERIMENTAL_NO_TRANSPORT",
                "XT missing-transport capability must be explicit");
        long long orderId = -1;
        Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
                "XT without transport must reject placement");
        Require(orderId == 0, "XT scaffold must not manufacture an order id");
        Require(xt.LastRejectReason() == "XT_TRANSPORT_NOT_IMPLEMENTED",
                "XT unsupported reason must be stable");
        for (int i = 0; i < 1000; ++i)
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
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.account = "QMT-SIM";
        cfg.serviceEpoch = "svc-1";
        cfg.connectionEpoch = 7;
        cfg.peerProfileSha256 = std::string(64, 'a');
        std::uint64_t observedRequests = 0;
        cfg.admittedReadOnlyExchange = [&observedRequests](
                const std::string& requestFrame, std::string& responseFrame) {
            std::string request;
            Require(HeptaXTGatewayAdapter::DecodeFrame(requestFrame, request),
                    "HXQ1 request frame must decode");
            Require(request.find("\"protocol\":\"HXQ1\"") != std::string::npos,
                    "HXQ1 protocol identity missing");
            Require(ExtractJsonUnsigned(request, "version") == 1,
                    "HXQ1 version mismatch");
            const std::uint64_t requestId = ExtractJsonUnsigned(request, "request_id");
            const std::string operation = ExtractJsonString(request, "operation");
            Require(ExtractJsonString(request, "service_epoch") == "svc-1",
                    "HXQ1 service epoch not bound");
            Require(ExtractJsonUnsigned(request, "connection_epoch") == 7,
                    "HXQ1 connection epoch not bound");
            Require(ExtractJsonString(request, "account") == "QMT-SIM",
                    "HXQ1 account not bound");
            Require(ExtractJsonString(request, "venue_command_id").empty(),
                    "read-only HXQ1 request must not carry mutation identity");
            ++observedRequests;
            std::ostringstream response;
            response << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
                     << requestId
                     << ",\"service_epoch\":\"svc-1\",\"connection_epoch\":7"
                     << ",\"operation\":\"" << operation
                     << "\",\"account\":\"QMT-SIM\",\"ok\":true";
            if (operation == "identity")
                response << "}";
            else if (operation == "account_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.account.v1\","
                         << "\"generation\":11,\"complete\":true,\"currency\":\"CNY\","
                         << "\"cash\":1000,\"total_asset\":1500,\"available_cash\":900}}";
            else if (operation == "position_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.positions.v1\","
                         << "\"generation\":11,\"complete\":true,\"positions\":["
                         << "{\"instrument\":\"600000.SH\",\"quantity\":100,"
                         << "\"sellable_quantity\":80,\"cost\":10.5}]}}";
            else if (operation == "quote_subscribe")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.quote.v1\","
                         << "\"generation\":12,\"complete\":true,"
                         << "\"instrument\":\"600000.SH\",\"bid\":10.1,\"ask\":10.2,"
                         << "\"observed_at_ms\":12345}}";
            else
                return false;
            return HeptaXTGatewayAdapter::EncodeFrame(response.str(), responseFrame);
        };
        Require(xt.Init(cfg), "valid HXQ1 read-only profile must initialize");
        Require(std::string(xt.CapabilityStatus()) == "EXPERIMENTAL_READ_ONLY_HXQ1",
                "read-only HXQ1 capability must be explicit");
        Require(xt.Connect() && xt.IsConnected(),
                "admitted HXQ1 identity handshake should establish read-only connection");
        Require(xt.LastRejectReason() == "XT_READ_ONLY_CONNECTED",
                "identity handshake is connection evidence, not snapshot readiness");
        Require(!xt.AccountPositionReadReady(),
                "identity handshake alone must not create authoritative read readiness");
        Require(xt.ReqAccountSummary(), "HXQ1 account snapshot request should round-trip");
        Require(!xt.AccountPositionReadReady(),
                "account snapshot alone must not complete the account/position barrier");
        Require(xt.ReqPositions(), "HXQ1 position snapshot request should round-trip");
        Require(xt.AccountPositionReadReady(),
                "same-epoch/same-generation account and position snapshots should be ready");
        HeptaXTAccountSnapshot account;
        Require(xt.GetAccountSnapshot(account) && account.generation == 11 &&
                    account.currency == "CNY" && account.totalAsset == 1500.0 &&
                    account.availableCash == 900.0,
                "typed account snapshot did not retain authoritative fields");
        HeptaXTPositionSnapshot positions;
        Require(xt.GetPositionSnapshot(positions) && positions.generation == 11 &&
                    positions.positions.size() == 1 &&
                    positions.positions[0].instrument == "600000.SH" &&
                    positions.positions[0].quantity == 100.0 &&
                    positions.positions[0].sellableQuantity == 80.0,
                "typed position snapshot did not retain authoritative fields");
        Require(xt.ReqMktData("600000.SH"), "HXQ1 quote subscribe should round-trip");
        HeptaXTQuoteSnapshot quote;
        Require(xt.GetQuoteSnapshot("600000.SH", quote) &&
                    quote.generation == 12 && quote.bid == 10.1 &&
                    quote.ask == 10.2 && quote.observedAtMs == 12345,
                "typed quote snapshot did not retain authoritative fields");
        Require(observedRequests == 4, "exact read-only request count mismatch");
        long long orderId = -1;
        Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
                "read-only HXQ1 must not enable order mutation");
        Require(orderId == 0 && xt.LastRejectReason() == "XT_MUTATION_DISABLED",
                "read-only XT mutation refusal must be explicit");
        Require(!xt.CancelOrder(1) && xt.LastRejectReason() == "XT_MUTATION_DISABLED",
                "read-only XT cancel must remain disabled");
        xt.Disconnect();
        Require(!xt.IsConnected(), "read-only disconnect clears connection state");
        Require(!xt.AccountPositionReadReady(),
                "disconnect invalidates read readiness");
        Require(!xt.GetAccountSnapshot(account) && !xt.GetPositionSnapshot(positions) &&
                    !xt.GetQuoteSnapshot("600000.SH", quote),
                "disconnect must clear cached authoritative read state");
        Require(!xt.ReqPositions() && xt.LastRejectReason() == "XT_READ_ONLY_NOT_CONNECTED",
                "read-only requests fail closed after disconnect");
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.account = "QMT-SIM";
        cfg.serviceEpoch = "svc-generation";
        cfg.connectionEpoch = 13;
        cfg.peerProfileSha256 = std::string(64, 'c');
        cfg.admittedReadOnlyExchange = [](const std::string& requestFrame,
                                          std::string& responseFrame) {
            std::string request;
            Require(HeptaXTGatewayAdapter::DecodeFrame(requestFrame, request),
                    "generation fixture request frame must decode");
            const std::uint64_t requestId =
                ExtractJsonUnsigned(request, "request_id");
            const std::string operation =
                ExtractJsonString(request, "operation");
            std::ostringstream response;
            response << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
                     << requestId
                     << ",\"service_epoch\":\"svc-generation\","
                     << "\"connection_epoch\":13,\"operation\":\"" << operation
                     << "\",\"account\":\"QMT-SIM\",\"ok\":true";
            if (operation == "identity")
                response << "}";
            else if (operation == "account_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.account.v1\","
                         << "\"generation\":31,\"complete\":true,\"currency\":\"CNY\","
                         << "\"cash\":1000,\"total_asset\":1000,\"available_cash\":1000}}";
            else if (operation == "position_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.positions.v1\","
                         << "\"generation\":32,\"complete\":true,\"positions\":[]}}";
            else
                return false;
            return HeptaXTGatewayAdapter::EncodeFrame(
                response.str(), responseFrame);
        };
        Require(xt.Init(cfg) && xt.Connect(),
                "generation mismatch fixture should connect");
        Require(xt.ReqAccountSummary() && xt.ReqPositions(),
                "known-empty position snapshot is valid completed evidence");
        HeptaXTPositionSnapshot emptyPositions;
        Require(xt.GetPositionSnapshot(emptyPositions) &&
                    emptyPositions.positions.empty(),
                "known-empty positions require a completed empty snapshot");
        Require(!xt.AccountPositionReadReady(),
                "mixed snapshot generations must not be treated as one barrier");
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.account = "QMT-SIM";
        cfg.serviceEpoch = "svc-invalid-payload";
        cfg.connectionEpoch = 14;
        cfg.peerProfileSha256 = std::string(64, 'd');
        cfg.admittedReadOnlyExchange = [](const std::string& requestFrame,
                                          std::string& responseFrame) {
            std::string request;
            Require(HeptaXTGatewayAdapter::DecodeFrame(requestFrame, request),
                    "invalid payload fixture request must decode");
            const std::uint64_t requestId =
                ExtractJsonUnsigned(request, "request_id");
            const std::string operation =
                ExtractJsonString(request, "operation");
            std::ostringstream response;
            response << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
                     << requestId
                     << ",\"service_epoch\":\"svc-invalid-payload\","
                     << "\"connection_epoch\":14,\"operation\":\"" << operation
                     << "\",\"account\":\"QMT-SIM\",\"ok\":true";
            if (operation == "identity")
                response << "}";
            else if (operation == "account_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.account.v1\","
                         << "\"generation\":41,\"complete\":true,\"currency\":\"CNY\","
                         << "\"cash\":1000,\"total_asset\":1500,"
                         << "\"available_cash\":1600}}";
            else
                return false;
            return HeptaXTGatewayAdapter::EncodeFrame(
                response.str(), responseFrame);
        };
        Require(xt.Init(cfg) && xt.Connect(),
                "invalid payload fixture should reach read-only connection");
        Require(!xt.ReqAccountSummary(),
                "economically invalid account payload must fail closed");
        Require(xt.LastRejectReason() == "XT_ACCOUNT_SNAPSHOT_INVALID",
                "invalid account payload reason must be stable");
        HeptaXTAccountSnapshot absent;
        Require(!xt.GetAccountSnapshot(absent) &&
                    !xt.AccountPositionReadReady(),
                "invalid account payload must not retain stale authoritative state");
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.account = "QMT-SIM";
        cfg.serviceEpoch = "svc-number-grammar";
        cfg.connectionEpoch = 15;
        cfg.peerProfileSha256 = std::string(64, 'e');
        std::string hostileNumber = "0";
        cfg.admittedReadOnlyExchange = [&hostileNumber](
                const std::string& requestFrame, std::string& responseFrame) {
            std::string request;
            Require(HeptaXTGatewayAdapter::DecodeFrame(requestFrame, request),
                    "number grammar fixture request must decode");
            const std::uint64_t requestId =
                ExtractJsonUnsigned(request, "request_id");
            const std::string operation =
                ExtractJsonString(request, "operation");
            std::ostringstream response;
            response << "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":"
                     << requestId
                     << ",\"service_epoch\":\"svc-number-grammar\","
                     << "\"connection_epoch\":15,\"operation\":\""
                     << operation
                     << "\",\"account\":\"QMT-SIM\",\"ok\":true";
            if (operation == "identity")
                response << "}";
            else if (operation == "account_snapshot")
                response << ",\"payload\":{\"schema\":\"heptatrader.xt.account.v1\","
                         << "\"generation\":51,\"complete\":true,\"currency\":\"CNY\","
                         << "\"cash\":" << hostileNumber
                         << ",\"total_asset\":1500,\"available_cash\":900}}";
            else
                return false;
            return HeptaXTGatewayAdapter::EncodeFrame(
                response.str(), responseFrame);
        };
        Require(xt.Init(cfg) && xt.Connect(),
                "number grammar fixture should reach read-only connection");
        const char* hostileNumbers[] = {
            "0x1p2", ".5", "1.", "01", "-01"
        };
        for (const char* hostile : hostileNumbers)
        {
            hostileNumber = hostile;
            Require(!xt.ReqAccountSummary(),
                    "non-JSON numeric spelling must fail closed");
            Require(xt.LastRejectReason() == "XT_ACCOUNT_SNAPSHOT_INVALID",
                    "non-JSON numeric spelling must have stable rejection");
            HeptaXTAccountSnapshot absent;
            Require(!xt.GetAccountSnapshot(absent) &&
                        !xt.AccountPositionReadReady(),
                    "rejected numeric spelling must not retain account authority");
        }
    }
    {
        HeptaXTGatewayAdapter xt;
        HeptaXTConfig cfg;
        cfg.account = "QMT-SIM";
        cfg.serviceEpoch = "svc-2";
        cfg.connectionEpoch = 9;
        cfg.peerProfileSha256 = std::string(64, 'b');
        cfg.admittedReadOnlyExchange = [](const std::string&, std::string& response) {
            return HeptaXTGatewayAdapter::EncodeFrame(
                "{\"protocol\":\"HXQ1\",\"version\":1,\"request_id\":999,\"service_epoch\":\"svc-2\",\"connection_epoch\":9,\"operation\":\"identity\",\"account\":\"QMT-SIM\",\"ok\":true}",
                response);
        };
        Require(xt.Init(cfg), "mismatched-response fixture should initialize");
        Require(!xt.Connect(), "mismatched HXQ1 response must fail closed");
        Require(xt.LastRejectReason() == "XT_HXQ1_RESPONSE_BINDING_INVALID",
                "HXQ1 response binding failure must be explicit");
    }
    std::cout << "venue_capability_tests: PASS" << std::endl;
    return 0;
}
