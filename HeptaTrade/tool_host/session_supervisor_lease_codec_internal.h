#pragma once
// Private format validation shared by in-memory transitions and HSL decoding.
#include "session_supervisor_lease_store.h"
namespace HeptaSessionLeaseCodec {
bool ParseUnsigned(const std::string& value, std::uint64_t maximum, std::uint64_t& parsed);
std::vector<std::string> SplitTabs(const std::string& line);
bool Sha256Hex(const std::string& value, std::string& digest);
bool IsSha256(const std::string& value);
bool IsPrefixedSha256(const std::string& value);
bool FinalizationText(const std::string& value, std::size_t maximum);
bool ValidPaperFinalizationRecord(
    const SessionSupervisorLeaseRecord& record);
bool SameLeaseFields(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right);
bool SameFinalizationBinding(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right);
bool SameFinalizationGroup(const SessionSupervisorLeaseRecord& left,
    const SessionSupervisorLeaseRecord& right);
bool ValidPaperFinalizationAck(
    const SessionSupervisorPaperFinalizationAck& acknowledgement);
bool DecodeCanonicalHex(const std::string& encoded, std::string& decoded);
bool ParseCanonicalUnsigned(const std::string& value,
    std::uint64_t& parsed);
bool ValidPaperFinalizationReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain);
bool ValidPaperTerminalAckReceipt(
    const std::string& receipt,
    const std::string& recoveryId,
    const std::string& finalizationId,
    const std::string& expectedOwnerSetSha256,
    std::uint64_t expectedOwnerCount,
    const std::string& preliminaryReceiptSha256,
    const std::string& expectedOwnerAccount,
    const std::string& expectedOwnerDomain,
    const std::string& expectedOwnerAgentId,
    const std::string& expectedOwnerSessionId,
    std::uint64_t expectedOwnerGeneration);
bool PaperFinalizationReceiptContainsOwner(
    const std::string& receipt,
    const std::string& ownerTokenSha256);
bool RetiredPaperOwner(
    const std::map<std::string,
        SessionSupervisorPaperFinalizationAck>& acknowledgements,
    const std::string& token);
}
