#include "../HeptaTrade/tool_host/trading_tool_session_catalog.h"

#include <cassert>
#include <iostream>

namespace {

IBContractLite FxContract()
{
    IBContractLite contract;
    contract.symbol = "EUR";
    contract.secType = "CASH";
    contract.exchange = "IDEALPRO";
    contract.currency = "USD";
    return contract;
}

TradingToolSessionContractRegistration Registration(
    const std::string& token,
    const std::string& sessionId,
    const IBContractLite& contract)
{
    TradingToolSessionContractRegistration registration;
    registration.token = token;
    registration.agentId = "agent-" + sessionId;
    registration.sessionId = sessionId;
    registration.expiresAtMs = 10000;
    registration.contracts["EUR.USD"] = contract;
    return registration;
}

} // namespace

int main()
{
    TradingToolSessionContractCatalog catalog;
    std::uint64_t observedRevision = 0;
    catalog.SetObserver([&](const TradingToolSessionContractCatalogSnapshot& snapshot) {
        observedRevision = snapshot.revision;
    });
    std::string reason;
    const IBContractLite fx = FxContract();
    assert(catalog.Register(Registration("token-1", "session-1", fx), reason));
    TradingToolSessionContractCatalogSnapshot snapshot = catalog.GetSnapshot();
    assert(snapshot.revision == 1);
    assert(observedRevision == 1);
    assert(snapshot.sessionCount == 1);
    assert(snapshot.contracts.at("EUR.USD").sessionReferences == 1);

    assert(catalog.Register(Registration("token-2", "session-2", fx), reason));
    snapshot = catalog.GetSnapshot();
    assert(snapshot.revision == 2);
    assert(snapshot.sessionCount == 2);
    assert(snapshot.contracts.at("EUR.USD").sessionReferences == 2);

    IBContractLite conflicting = fx;
    conflicting.primaryExchange = "NYSE";
    assert(!catalog.Register(Registration("token-3", "session-3", conflicting), reason));
    assert(reason == "CATALOG_CONTRACT_CONFLICT");
    assert(catalog.GetSnapshot().revision == 2);

    assert(catalog.Revoke("token-1"));
    snapshot = catalog.GetSnapshot();
    assert(snapshot.revision == 3);
    assert(snapshot.contracts.at("EUR.USD").sessionReferences == 1);
    assert(catalog.Revoke("token-2"));
    snapshot = catalog.GetSnapshot();
    assert(snapshot.revision == 4);
    assert(observedRevision == 4);
    assert(snapshot.sessionCount == 0);
    assert(snapshot.contracts.empty());
    assert(!catalog.Revoke("missing"));
    assert(catalog.GetSnapshot().revision == 4);
    std::cout << "trading_tool_session_catalog_tests: PASS" << std::endl;
    return 0;
}
