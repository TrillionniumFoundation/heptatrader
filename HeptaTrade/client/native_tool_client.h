#pragma once

#include "native_tool_discovery_contract.h"
#include "../tool_host/typed_tool_protocol.h"

#include <cstddef>
#include <mutex>
#include <string>

struct NativeToolClientConfig
{
    std::string socketPath;
    std::string tokenFile;
    std::string sessionToken;
    int timeoutMs = 5000;
    std::size_t maxResponseBytes =
        TradingToolWireLimits::MaximumResultEnvelopeBytes();
};

struct NativeToolClientResult
{
    TypedToolResultEnvelope envelope;
    std::string responseJson;
};

class NativeToolClient
{
public:
    explicit NativeToolClient(const NativeToolClientConfig& config);

    bool Call(TradingToolHostRequest request,
              NativeToolClientResult& result,
              std::string& reason) const;

    static bool ReadSessionToken(const std::string& path,
                                 std::string& token,
                                 std::string& reason);

private:
    bool CallOnce(TradingToolHostRequest request,
                  NativeToolClientResult& result,
                  std::string& reason) const;
    bool EnsureDiscoveryCatalog(const std::string& parentToolCallId,
                                std::string& reason) const;

    NativeToolClientConfig m_config;
    mutable std::mutex m_discoveryMutex;
    mutable NativeToolDiscoveryContract::CatalogSnapshot m_discoveryCatalog;
};
