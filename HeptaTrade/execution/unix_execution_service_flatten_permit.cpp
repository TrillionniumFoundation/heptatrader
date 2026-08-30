#include "unix_execution_service_server.h"

#include <cerrno>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>
#include <sys/random.h>

namespace
{
void AppendField(std::string& output, const std::string& value)
{
    output.append(std::to_string(value.size()));
    output.push_back(':');
    output.append(value);
    output.push_back('\n');
}

template <typename T>
std::string Number(T value)
{
    std::ostringstream output;
    if (value == 0) return "0";
    output.imbue(std::locale::classic());
    output << std::setprecision(17) << value;
    return output.str();
}

std::string Fingerprint(const FlattenPositionCommand& command)
{
    std::string value("flatten\n");
    AppendField(value, command.context.agentId);
    AppendField(value, command.context.sessionId);
    AppendField(value, command.context.strategy);
    AppendField(value, command.context.account);
    AppendField(value, command.context.venue);
    AppendField(value, command.context.executionDomain);
    AppendField(value, command.context.allowCancelAny ? "1" : "0");
    AppendField(value, command.instrument);
    AppendField(value, command.contract.symbol);
    AppendField(value, command.contract.secType);
    AppendField(value, command.contract.exchange);
    AppendField(value, command.contract.primaryExchange);
    AppendField(value, command.contract.currency);
    AppendField(value, command.contract.lastTradeDateOrContractMonth);
    AppendField(value, command.contract.right);
    AppendField(value, Number(command.contract.strike));
    AppendField(value, command.contract.multiplier);
    AppendField(value, command.contract.tradingClass);
    AppendField(value, command.contract.localSymbol);
    return value;
}

std::string OwnerKey(const FlattenPositionCommand& command)
{
    std::string value;
    AppendField(value, command.context.agentId);
    AppendField(value, command.context.sessionId);
    return value;
}

bool RandomHex(const char* prefix, std::size_t byteCount, std::string& value)
{
    unsigned char bytes[32];
    if (byteCount > sizeof(bytes)) return false;
    std::size_t offset = 0;
    while (offset < byteCount)
    {
        const ssize_t count =
            ::getrandom(bytes + offset, byteCount - offset, 0);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    static const char hex[] = "0123456789abcdef";
    value = prefix;
    for (std::size_t i = 0; i < byteCount; ++i)
    {
        value.push_back(hex[bytes[i] >> 4]);
        value.push_back(hex[bytes[i] & 0x0f]);
    }
    return true;
}

long long EpochNowMs()
{
    return static_cast<long long>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count());
}

bool ValidFlattenPreviewBinding(const ExecutionCommandResult& preview)
{
    return preview.hasAuthoritativeFlattenSnapshot &&
        std::isfinite(preview.authoritativeFlattenPositionQuantity) &&
        preview.authoritativeFlattenConnectionEpoch != 0 &&
        preview.authoritativeFlattenPositionGeneration != 0 &&
        !preview.authoritativeFlattenPlanBinding.empty() &&
        preview.authoritativeFlattenPlanBinding.size() <= 8192;
}
}

bool UnixExecutionServiceServer::IssueFlattenPreviewPermit(
    const FlattenPositionCommand& command,
    const ExecutionCommandResult& preview,
    std::string& permit,
    std::string& mutationCommandId,
    long long& expiresAtMs,
    std::string& reason)
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    expiresAtMs = now + 5000;
    if (!ValidFlattenPreviewBinding(preview))
    {
        reason = "EXECUTION_FLATTEN_PREVIEW_BINDING_MISSING";
        return false;
    }
    if (!RandomHex("sha256:", 32, permit) ||
        !RandomHex("hexec-command-", 16, mutationCommandId))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_GENERATION_FAILED";
        return false;
    }
    std::lock_guard<std::mutex> lock(m_previewMutex);
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.expiresAtMs <= now ||
            it->second.steadyExpiresAt <= steadyNow)
            it = m_previewPermits.erase(it);
        else
            ++it;
    }
    const std::string fingerprint = Fingerprint(command);
    const std::string ownerKey = OwnerKey(command);
    std::string replacedPermit;
    std::size_t ownerCount = 0;
    for (std::unordered_map<std::string, PreviewPermitRecord>::iterator it =
             m_previewPermits.begin(); it != m_previewPermits.end();)
    {
        if (it->second.fingerprint == fingerprint)
        {
            // Replace an exact older preview only after capacity checks pass;
            // a failed replacement must not revoke a usable credential.
            replacedPermit = it->first;
            ++it;
            continue;
        }
        if (it->second.ownerKey == ownerKey) ++ownerCount;
        ++it;
    }
    if (ownerCount >= 8)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_OWNER_CAPACITY_EXCEEDED";
        return false;
    }
    if (m_previewPermits.size() >= 128 && replacedPermit.empty())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_CAPACITY_EXCEEDED";
        return false;
    }
    PreviewPermitRecord record;
    record.fingerprint = fingerprint;
    record.ownerKey = ownerKey;
    record.mutationCommandId = mutationCommandId;
    record.expiresAtMs = expiresAtMs;
    record.steadyExpiresAt = steadyNow + std::chrono::milliseconds(5000);
    record.flattenSnapshot = true;
    record.flattenPositionQuantity =
        preview.authoritativeFlattenPositionQuantity;
    record.flattenConnectionEpoch =
        preview.authoritativeFlattenConnectionEpoch;
    record.flattenPositionGeneration =
        preview.authoritativeFlattenPositionGeneration;
    record.flattenPlanBinding = preview.authoritativeFlattenPlanBinding;
    if (!replacedPermit.empty()) m_previewPermits.erase(replacedPermit);
    m_previewPermits[permit] = record;
    reason.clear();
    return true;
}

bool UnixExecutionServiceServer::ConsumeFlattenPreviewPermit(
    FlattenPositionCommand& command,
    std::string& reason)
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> lock(m_previewMutex);
    const std::unordered_map<std::string, PreviewPermitRecord>::iterator found =
        m_previewPermits.find(command.previewPermit);
    if (found == m_previewPermits.end())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED";
        return false;
    }
    const PreviewPermitRecord record = found->second;
    if (record.expiresAtMs <= now ||
        record.steadyExpiresAt <= steadyNow)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_EXPIRED";
        return false;
    }
    if (record.fingerprint != Fingerprint(command))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH";
        return false;
    }
    if (record.mutationCommandId != command.context.toolCallId)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH";
        return false;
    }
    if (!record.flattenSnapshot ||
        !std::isfinite(record.flattenPositionQuantity) ||
        record.flattenConnectionEpoch == 0 ||
        record.flattenPositionGeneration == 0 ||
        record.flattenPlanBinding.empty() ||
        record.flattenPlanBinding.size() > 8192)
    {
        reason = "EXECUTION_FLATTEN_PREVIEW_BINDING_MISSING";
        return false;
    }
    // Erase only after expiry, payload, command-id and authoritative snapshot
    // validation all pass.  A rejected retry therefore leaves the credential
    // available for the exact legitimate command.
    m_previewPermits.erase(found);
    command.hasAuthoritativePreviewSnapshot = true;
    command.previewPositionQuantity = record.flattenPositionQuantity;
    command.previewPositionConnectionEpoch = record.flattenConnectionEpoch;
    command.previewPositionGeneration = record.flattenPositionGeneration;
    command.authoritativePreviewPlanBinding = record.flattenPlanBinding;
    // Do not let the one-time credential escape into a later dispatch or be
    // accidentally logged/reused by a caller holding the command object.
    command.previewPermit.clear();
    reason.clear();
    return true;
}

bool UnixExecutionServiceServer::ValidateFlattenPreviewPermit(
    const FlattenPositionCommand& command,
    std::string& reason) const
{
    const long long now = EpochNowMs();
    const std::chrono::steady_clock::time_point steadyNow =
        std::chrono::steady_clock::now();
    std::lock_guard<std::mutex> lock(m_previewMutex);
    const std::unordered_map<std::string, PreviewPermitRecord>::const_iterator
        found = m_previewPermits.find(command.previewPermit);
    if (found == m_previewPermits.end())
    {
        reason = "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED";
        return false;
    }
    const PreviewPermitRecord& record = found->second;
    if (record.expiresAtMs <= now ||
        record.steadyExpiresAt <= steadyNow)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_EXPIRED";
        return false;
    }
    if (record.fingerprint != Fingerprint(command))
    {
        reason = "EXECUTION_PREVIEW_PERMIT_ORDER_MISMATCH";
        return false;
    }
    if (record.mutationCommandId != command.context.toolCallId)
    {
        reason = "EXECUTION_PREVIEW_PERMIT_COMMAND_ID_MISMATCH";
        return false;
    }
    if (!record.flattenSnapshot ||
        !std::isfinite(record.flattenPositionQuantity) ||
        record.flattenConnectionEpoch == 0 ||
        record.flattenPositionGeneration == 0 ||
        record.flattenPlanBinding.empty() ||
        record.flattenPlanBinding.size() > 8192)
    {
        reason = "EXECUTION_FLATTEN_PREVIEW_BINDING_MISSING";
        return false;
    }
    reason.clear();
    return true;
}
