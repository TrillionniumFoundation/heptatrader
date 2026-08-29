#pragma once

#include "execution_authority.h"

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <set>
#include <string>

enum class ExecutionServiceOperation;

// Client-only contract for the Agent-facing Gateway.  This header must not
// expose the privileged UnixExecutionServiceServer or permit authority.
class UnixExecutionServiceClient : public ExecutionAuthority,
                                   public ExecutionControlAuthority,
                                   public ExecutionReadAuthority
{
public:
    // An empty server UID set means the current effective UID. Cross-identity
    // deployments must pass the dedicated Execution Service UID explicitly.
    explicit UnixExecutionServiceClient(const std::string& socketPath,
                                        int ioTimeoutMs = 3000,
                                        std::size_t maxResponseBytes = 32768,
                                        const std::set<std::uint32_t>&
                                            allowedServerUids =
                                                std::set<std::uint32_t>());

    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override;
    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override;
    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override;
    ExecutionControlResult QueryCommandStatus(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult FenceSessionOwner(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult ReleaseSessionOwnerFence(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult ReconcileAuthoritativeState(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult RecoveryAuditOwner(
        const ExecutionControlCommand& command) override;
    ExecutionControlResult TerminalizeRecoveryOwner(
        const ExecutionControlCommand& command) override;
    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override;
    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override;
    ExecutionCommandResult PreviewFlattenPosition(
        const FlattenPositionCommand& command) override;

    // Gateway callers must dispatch with the exact daemon identity pair that
    // was validated across both the mutation and event sockets. These methods
    // never re-read or substitute the client's mutation-only identity cache.
    ExecutionCommandResult PlaceIbOrderWithIdentity(
        const IbPlaceOrderCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionCommandResult CancelIbOrderWithIdentity(
        const IbCancelOrderCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionCommandResult FlattenPositionWithIdentity(
        const FlattenPositionCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult QueryCommandStatusWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult FenceSessionOwnerWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult ReleaseSessionOwnerFenceWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult ReconcileAuthoritativeStateWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult RecoveryAuditOwnerWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionControlResult TerminalizeRecoveryOwnerWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionCommandResult ReadAuthoritativeStateWithIdentity(
        const ExecutionReadCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionCommandResult PreviewOrderWithIdentity(
        const PlaceOrderCommand& command,
        const ExecutionServiceIdentity& identity);
    ExecutionCommandResult PreviewFlattenPositionWithIdentity(
        const FlattenPositionCommand& command,
        const ExecutionServiceIdentity& identity);

    bool GetServiceIdentity(ExecutionServiceIdentity& identity,
                            std::string& reason);
    void InvalidateServiceIdentity(const ExecutionServiceIdentity& identity);

private:
    ExecutionControlResult DispatchControlWithIdentity(
        const ExecutionControlCommand& command,
        const ExecutionServiceIdentity& identity,
        ExecutionServiceOperation operation);
    ExecutionCommandResult Call(
        const std::string& commandId,
        const std::string& requestBody,
        const ExecutionServiceIdentity& expectedIdentity);
    ExecutionControlResult CallControl(
        const std::string& commandId,
        const std::string& requestBody,
        const ExecutionServiceIdentity& expectedIdentity);

    std::string m_socketPath;
    int m_ioTimeoutMs;
    std::size_t m_maxResponseBytes;
    std::set<std::uint32_t> m_allowedServerUids;
    std::mutex m_serviceIdentityMutex;
    ExecutionServiceIdentity m_serviceIdentity;
};
