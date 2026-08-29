#include "ib_venue_correlation.h"

#include <cstddef>
#include <vector>

namespace
{
const char* kPrefix = "hepta-v1-sha256:";
const char* kAlphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

int HexValue(char value)
{
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    return -1;
}

int Base64Value(char value)
{
    for (int i = 0; i < 64; ++i)
        if (kAlphabet[i] == value) return i;
    return -1;
}

std::string Hex(const std::vector<unsigned char>& bytes)
{
    static const char digits[] = "0123456789abcdef";
    std::string value;
    value.reserve(bytes.size() * 2);
    for (std::size_t i = 0; i < bytes.size(); ++i)
    {
        value.push_back(digits[(bytes[i] >> 4) & 0x0f]);
        value.push_back(digits[bytes[i] & 0x0f]);
    }
    return value;
}
}

bool IbVenueCorrelationCodec::EncodeOrderRef(
    const std::string& correlationId, std::string& orderRef, std::string& reason)
{
    orderRef.clear();
    reason.clear();
    const std::string prefix(kPrefix);
    if (correlationId.size() != prefix.size() + 64 ||
        correlationId.compare(0, prefix.size(), prefix) != 0)
    {
        reason = "IB_CORRELATION_FORMAT_INVALID";
        return false;
    }
    std::vector<unsigned char> bytes(32);
    for (std::size_t i = 0; i < bytes.size(); ++i)
    {
        const int high = HexValue(correlationId[prefix.size() + i * 2]);
        const int low = HexValue(correlationId[prefix.size() + i * 2 + 1]);
        if (high < 0 || low < 0)
        {
            reason = "IB_CORRELATION_FORMAT_INVALID";
            return false;
        }
        bytes[i] = static_cast<unsigned char>((high << 4) | low);
    }
    orderRef = "H1";
    unsigned int accumulator = 0;
    int bits = 0;
    for (std::size_t i = 0; i < bytes.size(); ++i)
    {
        accumulator = (accumulator << 8) | bytes[i];
        bits += 8;
        while (bits >= 6)
        {
            bits -= 6;
            orderRef.push_back(kAlphabet[(accumulator >> bits) & 0x3f]);
        }
    }
    if (bits > 0) orderRef.push_back(kAlphabet[(accumulator << (6 - bits)) & 0x3f]);
    if (orderRef.size() != 45)
    {
        orderRef.clear();
        reason = "IB_CORRELATION_ENCODE_FAILED";
        return false;
    }
    reason.clear();
    return true;
}

bool IbVenueCorrelationCodec::DecodeOrderRef(
    const std::string& orderRef, std::string& correlationId, std::string& reason)
{
    correlationId.clear();
    reason.clear();
    if (orderRef.size() != 45 || orderRef.compare(0, 2, "H1") != 0)
    {
        reason = "IB_ORDER_REF_NOT_HEPTA_CORRELATION";
        return false;
    }
    std::vector<unsigned char> bytes;
    bytes.reserve(32);
    unsigned int accumulator = 0;
    int bits = 0;
    for (std::size_t i = 2; i < orderRef.size(); ++i)
    {
        const int value = Base64Value(orderRef[i]);
        if (value < 0)
        {
            reason = "IB_ORDER_REF_CORRELATION_INVALID";
            return false;
        }
        accumulator = (accumulator << 6) | static_cast<unsigned int>(value);
        bits += 6;
        if (bits >= 8)
        {
            bits -= 8;
            bytes.push_back(static_cast<unsigned char>((accumulator >> bits) & 0xff));
        }
    }
    if (bytes.size() != 32 || bits != 2 || (accumulator & 0x03) != 0)
    {
        reason = "IB_ORDER_REF_CORRELATION_INVALID";
        return false;
    }
    correlationId = std::string(kPrefix) + Hex(bytes);
    reason.clear();
    return true;
}
