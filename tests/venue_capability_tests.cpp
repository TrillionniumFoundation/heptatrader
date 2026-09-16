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

XTHXQ1ReadEnvelope Identity(std::uint64_t transportEpoch = 1,
                            std::uint64_t connectionEpoch = 1,
                            const std::string& sidecar = "sidecar-a") {
    XTHXQ1ReadEnvelope value;
    value.requestId = "identity-1";
    value.serviceEpoch = "linux-service-a";
    value.transportEpoch = transportEpoch;
    value.sidecarInstanceId = sidecar;
    value.connectionEpoch = connectionEpoch;
    value.operation = "identity";
    value.account = "paper-a";
    value.complete = true;
    return value;
}

XTHXQ1ReadEnvelope Snapshot(const std::string& operation,
                           std::uint64_t generation,
                           char digestCharacter,
                           std::uint64_t rows = 0) {
    XTHXQ1ReadEnvelope value = Identity();
    value.requestId = operation + "-" + std::to_string(generation);
    value.operation = operation;
    value.generation = generation;
    value.complete = true;
    value.rowCount = rows;
    value.snapshotSha256.assign(64, digestCharacter);
    return value;
}

void TestXTReadOnlyProjection() {
    HeptaXTGatewayAdapter xt;
    HeptaXTConfig cfg;
    Require(xt.Init(cfg), "XT projection fixture initializes");
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(Identity()),
            "authenticated identity should bind read-only epochs");
    Require(!xt.IsConnected(), "read-only projection cannot fake transport connectivity");
    Require(!xt.ReadOnlyAuthoritySnapshot().ready,
            "identity alone cannot make authoritative state ready");

    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(
                Snapshot("account_snapshot", 1, 'a', 1)),
            "complete account snapshot accepted");
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(
                Snapshot("position_snapshot", 1, 'b', 2)),
            "complete position snapshot accepted");
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(
                Snapshot("order_snapshot", 1, 'c', 0)),
            "known-empty order snapshot accepted");
    XTHXQ1ReadEnvelope trade = Snapshot("trade_snapshot", 1, 'd', 0);
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(trade),
            "known-empty trade snapshot accepted");
    Require(xt.ReadOnlyAuthoritySnapshot().ready,
            "all four complete barriers should make read-only projection ready");
    Require(!xt.IsConnected(), "READY projection still cannot create a transport");

    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(trade),
            "exact duplicate snapshot is idempotent");
    XTHXQ1ReadEnvelope conflict = trade;
    conflict.snapshotSha256.assign(64, 'e');
    Require(!xt.ApplyAuthenticatedReadOnlyEnvelope(conflict),
            "same generation with different content must conflict");
    Require(xt.LastRejectReason() == "XT_HXQ1_SNAPSHOT_CONFLICT",
            "snapshot conflict reason is explicit");
    Require(!xt.ReadOnlyAuthoritySnapshot().ready,
            "conflict invalidates the affected barrier");
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(
                Snapshot("trade_snapshot", 2, 'f', 1)),
            "newer generation can rebuild a conflicted read barrier");
    Require(xt.ReadOnlyAuthoritySnapshot().ready,
            "newer complete barrier restores read-only readiness");

    XTHXQ1ReadEnvelope nextIdentity = Identity(2, 2, "sidecar-b");
    nextIdentity.requestId = "identity-2";
    Require(xt.ApplyAuthenticatedReadOnlyEnvelope(nextIdentity),
            "new authenticated transport/venue epoch is admitted");
    const XTReadOnlyAuthoritySnapshot reset = xt.ReadOnlyAuthoritySnapshot();
    Require(reset.identityBound && !reset.ready &&
                reset.transportEpoch == 2 && reset.connectionEpoch == 2,
            "new epoch invalidates all old snapshot completeness");
    XTHXQ1ReadEnvelope stale = Snapshot("account_snapshot", 2, 'a', 1);
    Require(!xt.ApplyAuthenticatedReadOnlyEnvelope(stale),
            "old transport/sidecar/venue binding is rejected");
    Require(xt.LastRejectReason() == "XT_HXQ1_STALE_OR_FOREIGN_BINDING",
            "stale binding has a typed failure");

    XTHXQ1ReadEnvelope mutation = nextIdentity;
    mutation.requestId = "mutation-attempt";
    mutation.operation = "place";
    Require(!xt.ApplyAuthenticatedReadOnlyEnvelope(mutation),
            "HXQ1 projection seam rejects mutations");
    Require(xt.LastRejectReason() == "XT_HXQ1_MUTATION_FORBIDDEN",
            "read-only seam mutation reason is explicit");

    long long orderId = -1;
    Require(!xt.PlaceOrder("600000.SH", "BUY", 100.0, 10.0, &orderId),
            "read-only state cannot enable production placement");
    Require(orderId == 0 && !xt.IsConnected(),
            "read-only state never manufactures transport/order authority");
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
                "XT scaffold must reject broker queries without a transport");
    }
    TestXTReadOnlyProjection();
    std::cout << "venue_capability_tests: PASS" << std::endl;
    return 0;
}
