#pragma once

#include <map>
#include <string>

namespace NativeToolDiscoveryContract
{
static const unsigned int kSchemaVersion = 2;

struct CatalogSnapshot
{
    std::string schemaHash;
    std::map<std::string, std::string> descriptorSchemaHashes;
};

// Validate the complete discovery-v2 payload. A list response establishes a
// name-to-descriptor-hash snapshot. A describe response must match both its
// requested target and the exact descriptor hash established by that prior
// list call on the same client session.
bool Validate(const std::string& discoveryOperation,
              const std::string& payload,
              const std::string& requestedTargetToolName,
              const CatalogSnapshot& expectedCatalog,
              CatalogSnapshot& observedCatalog,
              std::string& reason);
}
