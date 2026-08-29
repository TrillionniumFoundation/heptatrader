#pragma once

#include <string>

class IbVenueCorrelationCodec
{
public:
    // IB orderRef is limited to 45 characters. H1 plus unpadded base64url of
    // the 32-byte digest is exactly 45 and reversibly represents the canonical
    // hepta-v1-sha256:<64 lowercase hex> correlation.
    static bool EncodeOrderRef(const std::string& correlationId,
                               std::string& orderRef,
                               std::string& reason);
    static bool DecodeOrderRef(const std::string& orderRef,
                               std::string& correlationId,
                               std::string& reason);
};
