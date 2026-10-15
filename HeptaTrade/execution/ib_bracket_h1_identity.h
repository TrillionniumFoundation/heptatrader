#pragma once
// Domain-separated native H1 identities for ONE immutable PAPER EUR/USD bracket.
// No broker, OMS writer, order ID allocator, or stop/maintenance authority.
// This helper is intended to be called AFTER the exact Execution owner has
// durably reserved the group, NEVER as a substitute for that reservation.
#include "../adapter_ib/ib_venue_correlation.h"
#include <openssl/sha.h>
#include <cmath>
#include <cstring>
#include <cstdint>
#include <limits>
#include <string>

namespace hepta_bracket_h1 {
struct FrozenBracket {
    std::string originalCommandKey;
    std::string account;
    std::string owner;
    std::string side;
    std::string executionDomain;
    long contractId = 0;
    int clientId = 0;
    long parentOrderId = 0;
    long childOrderId = 0;
    std::uint64_t connectionEpoch = 0;
    std::uint64_t positionGeneration = 0;
    std::uint64_t ownerFenceToken = 0;
    std::uint64_t intentObservedAtMs = 0;
    double quantity = 0;
    double referencePrice = 0;
    double stopPrice = 0;
    double minTick = 0;
};
struct BoundRefs {
    std::string groupCorrelation;
    std::string parentCorrelation;
    std::string childCorrelation;
    std::string ocaCorrelation;
    std::string parentRef;
    std::string childRef;
    std::string ocaGroup;
    // Never an approval to send native orders, restart or stop a driver.
    bool orderSendAuthority = false;
    bool maintenanceAuthorized = false;
};
inline bool SafeText(const std::string& s, std::size_t maxLen) {
    if (s.empty() || s.size() > maxLen) return false;
    for (unsigned char ch : s)
        if (!(ch >= 'A' && ch <= 'Z') && !(ch >= 'a' && ch <= 'z') &&
            !(ch >= '0' && ch <= '9') && ch != '-' && ch != '_' && ch != '.')
            return false;
    return true;
}
inline void Field(std::string& into, const std::string& value) {
    into += std::to_string(value.size()) + ":" + value + ";";
}
inline std::string HashCanonical(const std::string& value) {
    unsigned char out[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(value.data()), value.size(), out);
    static const char* digits = "0123456789abcdef";
    std::string hex = "hepta-v1-sha256:";
    for (const unsigned char octet : out) {
        hex += digits[(octet >> 4) & 15]; hex += digits[octet & 15];
    }
    return hex;
}
inline bool BuildFrozenRefs(const FrozenBracket& v, BoundRefs& out,
                            std::string& reason) {
    out = BoundRefs{};
    reason.clear();
    if (v.executionDomain != "PAPER" || v.contractId != 12087792 ||
        v.account.compare(0, 2, "DU") != 0 ||
        !SafeText(v.account, 48) || !SafeText(v.owner, 96) ||
        !SafeText(v.originalCommandKey, 96) ||
        (v.side != "BUY" && v.side != "SELL")) {
        reason = "BRACKET_H1_EXACT_PAPER_OWNER_SCOPE"; return false;
    }
    if (v.clientId < 1 || v.parentOrderId < 1 ||
        v.parentOrderId >= std::numeric_limits<std::int32_t>::max()-1 ||
        v.childOrderId != v.parentOrderId + 1 ||
        !v.connectionEpoch || !v.positionGeneration ||
        !v.ownerFenceToken || !v.intentObservedAtMs) {
        reason = "BRACKET_H1_DURABLE_IDENTITY_OR_FENCE_MISSING"; return false;
    }
    if (!std::isfinite(v.quantity) || v.quantity < 1 || v.quantity > 100000 ||
        std::floor(v.quantity) != v.quantity ||
        !std::isfinite(v.referencePrice) || v.referencePrice <= 0 ||
        v.quantity * v.referencePrice > 100000 ||
        !std::isfinite(v.stopPrice) || v.stopPrice <= 0 ||
        v.minTick != 0.00005 ||
        (v.side == "BUY" && v.stopPrice >= v.referencePrice) ||
        (v.side == "SELL" && v.stopPrice <= v.referencePrice)) {
        reason = "BRACKET_H1_AMOUNT_PRICE_OR_RULE_INVALID"; return false;
    }
    const double scaled = v.stopPrice / v.minTick;
    if (!std::isfinite(scaled) || scaled > 1.e12 ||
        scaled <= 0 || std::fabs(scaled-std::round(scaled)) > 1.e-7) {
        reason = "BRACKET_H1_STOP_PRICE_OFF_GRID"; return false;
    }
    std::string canonical;
    Field(canonical,"hepta-bracket-h1-binding/v1");
    Field(canonical,v.originalCommandKey);
    Field(canonical,v.account);
    Field(canonical,v.owner);
    Field(canonical,v.executionDomain);
    Field(canonical,v.side);
    Field(canonical,std::to_string(v.contractId));
    Field(canonical,std::to_string(v.clientId));
    Field(canonical,std::to_string(v.parentOrderId));
    Field(canonical,std::to_string(v.childOrderId));
    Field(canonical,std::to_string(v.connectionEpoch));
    Field(canonical,std::to_string(v.positionGeneration));
    Field(canonical,std::to_string(v.ownerFenceToken));
    Field(canonical,std::to_string(v.intentObservedAtMs));
    Field(canonical,std::to_string(static_cast<std::uint64_t>(v.quantity)));
    Field(canonical,std::to_string(static_cast<std::uint64_t>(std::llround(scaled))));
    // Bind the exact original reference price, even when it is between ticks.
    // Never replace an execution-time quote with a rounded or later price.
    const double refTicks = v.referencePrice/v.minTick;
    if (!std::isfinite(refTicks) || refTicks<=0 || refTicks>1.e12) {
        reason="BRACKET_H1_REFERENCE_UNREPRESENTABLE";return false;
    }
    // Convert IEEE-754 bits to the SAME integer irrespective of CPU endian.
    std::uint64_t refBits=0;
    static_assert(sizeof(refBits)==sizeof(v.referencePrice) &&
                  std::numeric_limits<double>::is_iec559,
                  "IEEE754 binary64 required");
    std::memcpy(&refBits, &v.referencePrice, sizeof(refBits));
    Field(canonical,std::to_string(refBits));
    const std::string group = HashCanonical(canonical);
    auto derive = [&](const char* role) {
        std::string roleData;
        Field(roleData,"hepta-bracket-h1-role/v1");
        Field(roleData,group);
        Field(roleData,role);
        return HashCanonical(roleData);
    };
    BoundRefs candidate;
    candidate.groupCorrelation=group;
    candidate.parentCorrelation=derive("PARENT");
    candidate.childCorrelation=derive("STOP");
    candidate.ocaCorrelation=derive("OCA");
    if (!IbVenueCorrelationCodec::EncodeOrderRef(candidate.parentCorrelation,
        candidate.parentRef,reason) ||
        !IbVenueCorrelationCodec::EncodeOrderRef(candidate.childCorrelation,
        candidate.childRef,reason) ||
        !IbVenueCorrelationCodec::EncodeOrderRef(candidate.ocaCorrelation,
        candidate.ocaGroup,reason)) return false;
    if (candidate.parentRef==candidate.childRef ||
        candidate.parentRef==candidate.ocaGroup ||
        candidate.childRef==candidate.ocaGroup) {
        reason="BRACKET_H1_ROLE_COLLISION";return false;
    }
    out=std::move(candidate);
    return true;
}
inline bool VerifyFrozenRefs(const FrozenBracket& v, const BoundRefs& observed,
                             std::string& reason) {
    BoundRefs expected;
    if (!BuildFrozenRefs(v,expected,reason)) return false;
    if (observed.parentRef!=expected.parentRef ||
        observed.childRef!=expected.childRef ||
        observed.ocaGroup!=expected.ocaGroup ||
        observed.groupCorrelation!=expected.groupCorrelation ||
        observed.parentCorrelation!=expected.parentCorrelation ||
        observed.childCorrelation!=expected.childCorrelation ||
        observed.ocaCorrelation!=expected.ocaCorrelation ||
        observed.orderSendAuthority || observed.maintenanceAuthorized) {
        reason="BRACKET_H1_IMMUTABLE_IDENTITY_CHANGED";return false;
    }
    std::string decoded,decodeReason;
    if (!IbVenueCorrelationCodec::DecodeOrderRef(observed.parentRef,decoded,decodeReason) ||
        decoded!=observed.parentCorrelation ||
        !IbVenueCorrelationCodec::DecodeOrderRef(observed.childRef,decoded,decodeReason) ||
        decoded!=observed.childCorrelation ||
        !IbVenueCorrelationCodec::DecodeOrderRef(observed.ocaGroup,decoded,decodeReason) ||
        decoded!=observed.ocaCorrelation) {
        reason="BRACKET_H1_CORRELATION_NONCANONICAL";return false;
    }
    reason.clear();return true;
}
} // namespace hepta_bracket_h1
