#pragma once

#include "../adapter_ib/ib_api_wrapper.h"

#include <string>

// Builds the stable instrument key used by authoritative IB snapshots. FX and
// simple securities retain their established keys; derivatives include enough
// contract attributes to prevent expiry/strike/right collisions.
std::string BuildIBAuthoritativeInstrumentIdentity(const IBContractLite& contract,
                                                   const std::string& fallbackKey = std::string());
