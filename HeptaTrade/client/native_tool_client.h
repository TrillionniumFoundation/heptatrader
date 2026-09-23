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

    // Fingerprint the configured socket, effective UID and current credential.
    // It is not the credential and grants no authority. Token-file rotation or
    // a different endpoint intentionally requires explicit recovery handling.
    bool RecoveryBinding(std::string& binding, std::string& reason) const;
    // Resolve the credential once, check the binding, then use that exact
    // snapshot for discovery and this single call. Never retry the mutation.
    bool CallBound(TradingToolHostRequest request, const std::string& binding,
                   NativeToolClientResult& result, std::string& reason) const;

    static bool ReadSessionToken(const std::string& path,
                                 std::string& token,
                                 std::string& reason);

private:
    bool CallPinned(TradingToolHostRequest request,
                    NativeToolClientResult& result, std::string& reason) const;
    bool CallSnapshot(TradingToolHostRequest request,
                      const NativeToolClientConfig& snapshot,
                      const std::string& binding,
                      NativeToolClientResult& result, std::string& reason) const;
    bool ResolveRecoveryConfig(NativeToolClientConfig& config,
                               std::string& binding, std::string& reason) const;
    bool CallOnce(TradingToolHostRequest request,
                  NativeToolClientResult& result,
                  std::string& reason) const;
    bool EnsureDiscoveryCatalog(const std::string& parentToolCallId,
                                std::string& reason) const;

    NativeToolClientConfig m_config;
    mutable std::mutex m_discoveryMutex;
    mutable NativeToolDiscoveryContract::CatalogSnapshot m_discoveryCatalog;
    // Schema discovery is reusable data, never authorization. A different
    // resolved credential/endpoint/UID must obtain its own verified catalog.
    mutable std::string m_discoveryBinding;
};
