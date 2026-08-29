#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <string>
#include <vector>

enum class SessionSupervisorPaperFinalizationState : std::uint32_t
{
    None = 0,
    FencePending = 1,
    FenceComplete = 2,
    AuditSealed = 3
};

const char* SessionSupervisorPaperFinalizationStateName(
    SessionSupervisorPaperFinalizationState state);

struct SessionSupervisorLeaseRecord
{
    std::string templateId;
    std::string issuer;
    std::string token;
    std::string agentId;
    std::string sessionId;
    // Durable exact Execution owner scope.  PAPER recovery must never resolve
    // a changed account/domain from current policy after a restart.
    std::string ownerAccount;
    std::string ownerExecutionDomain;
    std::uint32_t peerUid = 0;
    std::uint64_t expiresAtMs = 0;
    std::uint64_t leaseGeneration = 0;
    std::string predecessorToken;
    std::uint64_t predecessorGeneration = 0;
    bool fencePending = false;
    bool fenceComplete = false;
    std::string fenceReason;
    bool recoveryOnly = false;
    std::string recoveryCommandId;
    // Explicit, durable transition discriminator. Legacy/local PAPER remains
    // false and keeps its established revoke path. External PAPER recovery
    // must commit true before remote recovery queries/mutations and can never
    // downgrade it or use naked revoke/reap as terminal proof.
    bool paperFinalizationRequired = false;
    // HSL7 PAPER finalization is a strict, one-way state machine.  These
    // fields are empty for WATCH and for ordinary PAPER leases.  A finalized
    // record is a non-authorizing tombstone; it must never be provisioned or
    // rotated back into a Tool session.
    SessionSupervisorPaperFinalizationState paperFinalizationState =
        SessionSupervisorPaperFinalizationState::None;
    std::string recoveryId;
    std::string finalizationId;
    std::string expectedOwnerSetSha256;
    std::uint64_t expectedOwnerCount = 0;
    std::string ownerTokenSha256;
    std::string finalizationReceiptSha256;
    std::string finalizationReceipt;
};

struct SessionSupervisorPaperFinalizationAck
{
    std::string recoveryId;
    std::string finalizationId;
    std::string expectedOwnerSetSha256;
    std::uint64_t expectedOwnerCount = 0;
    std::string receiptSha256;
    std::string receipt;
	std::string terminalReceiptSha256;
	std::string terminalReceipt;
    std::string acknowledgingOwnerTokenSha256;
    std::uint64_t acknowledgingOwnerGeneration = 0;
	std::string acknowledgingOwnerIssuer;
	std::string terminalizingOwnerAgentId;
	std::string terminalizingOwnerSessionId;
	std::string terminalizingOwnerAccount;
	std::string terminalizingOwnerExecutionDomain;
};

struct SessionSupervisorLegacyPaperCleanupRequest
{
    std::string expectedIssuer;
    std::string expectedAgentId;
    std::uint32_t expectedPeerUid = 0;
    std::string expectedPreStoreSha256;
    std::string backupPath;
    std::string cleanupLockPath;
    std::uint32_t expectedLockUid = 0;
    std::uint32_t expectedLockGid = 0;
    std::uint32_t expectedSourceUid = 0;
    std::uint32_t expectedSourceGid = 0;
    std::uint32_t expectedSourceMode = 0;
    std::uint32_t expectedKeyUid = 0;
    std::uint32_t expectedKeyGid = 0;
    std::uint32_t expectedKeyMode = 0;
    std::string expectedKeySha256;
};

struct SessionSupervisorLegacyPaperCleanupResult
{
    std::size_t retiredRecords = 0;
    std::string preStoreSha256;
    std::string postStoreSha256;
    bool alreadyMigrated = false;
};

class SessionSupervisorLeaseStore
{
public:
    ~SessionSupervisorLeaseStore();
    bool Init(const std::string& path, std::string& reason);
    bool Init(const std::string& path, const std::string& keyPath, std::string& reason);
    bool Init(const std::string& path, const std::string& keyPath,
              const std::string& cleanupLockPath,
              std::uint32_t expectedLockUid,
              std::uint32_t expectedLockGid,
              std::string& reason);
    // One-shot, cleanup-only migration for the pre-owner HSL4/HSL5 PAPER
    // layouts. Every PAPER record in the legacy source must match the fixed
    // scope and already be expired and free of predecessor/fence/recovery
    // state. This API never synthesizes an owner binding and therefore cannot
    // turn a legacy record into recovery or entry authority.
    bool MigrateHsl5PaperForTerminalCleanup(
        const std::string& path, const std::string& keyPath,
        const SessionSupervisorLegacyPaperCleanupRequest& request,
        SessionSupervisorLegacyPaperCleanupResult& result,
        std::string& reason);
    bool Put(const SessionSupervisorLeaseRecord& record, std::string& reason);
    bool Remove(const std::string& token, std::string& reason);
    bool Replace(const std::string& currentToken,
                 const SessionSupervisorLeaseRecord& record,
                 std::string& reason);
    // Finalization transitions intentionally bypass generic Replace, which
    // rejects every transition involving a PAPER tombstone.  The exact APIs
    // enforce monotonic state and perform one encrypted-store commit each.
    bool AdvancePaperFinalization(
        const std::string& token,
        SessionSupervisorPaperFinalizationState expectedState,
        const SessionSupervisorLeaseRecord& replacement,
        std::string& reason);
    bool SealPaperFinalizationGroup(
        const std::string& recoveryId,
        const std::string& finalizationId,
        const std::string& expectedOwnerSetSha256,
        std::uint64_t expectedOwnerCount,
        const std::string& receiptSha256,
        const std::string& receipt,
        std::string& reason);
    bool AcknowledgeAndPurgePaperFinalizationGroup(
        const std::string& recoveryId,
        const std::string& finalizationId,
        const std::string& expectedOwnerSetSha256,
        std::uint64_t expectedOwnerCount,
        const std::string& receiptSha256,
		const std::string& terminalReceiptSha256,
		const std::string& terminalReceipt,
        const std::string& acknowledgingOwnerTokenSha256,
        std::uint64_t acknowledgingOwnerGeneration,
		const std::string& acknowledgingOwnerIssuer,
		const std::string& terminalizingOwnerAgentId,
		const std::string& terminalizingOwnerSessionId,
		const std::string& terminalizingOwnerAccount,
		const std::string& terminalizingOwnerExecutionDomain,
        SessionSupervisorPaperFinalizationAck& acknowledgement,
        bool& alreadyAcknowledged,
        std::string& reason);
    bool GetPaperFinalizationAck(
        const std::string& finalizationId,
        SessionSupervisorPaperFinalizationAck& acknowledgement) const;
    bool Get(const std::string& token, SessionSupervisorLeaseRecord& record) const;
    std::vector<SessionSupervisorLeaseRecord> List() const;
    static bool PaperOwnerSetSha256(
        const std::vector<SessionSupervisorLeaseRecord>& records,
        std::string& sha256,
        std::string& reason);

private:
    bool InitStoreState(const std::string& path,
                        const std::string& keyPath,
                        std::string& reason);
    static std::string HexEncode(const std::string& value);
    static bool HexDecode(const std::string& value, std::string& decoded);
    bool LoadKey(const std::string& keyPath, std::string& reason);
    bool LoadKeyForTerminalCleanup(
        const std::string& keyPath,
        const SessionSupervisorLegacyPaperCleanupRequest& request,
        std::string& reason);
    bool LoadEncryptedPlaintextLocked(const std::string& path,
                                      std::string& plaintext,
                                      bool& missing,
                                      std::string& reason,
                                      std::string* encoded = nullptr);
    bool DecodeEncryptedPlaintext(const std::string& encoded,
                                  std::string& plaintext,
                                  std::string& reason) const;
    std::string SerializePlaintext() const;
    bool ParsePlaintext(const std::string& plaintext, std::string& reason);
    bool ParsePlaintextForTerminalCleanup(
        const std::string& plaintext,
        const SessionSupervisorLegacyPaperCleanupRequest& request,
        std::uint64_t nowMs,
        SessionSupervisorLegacyPaperCleanupResult& result,
        std::string& reason);
    bool ParsePlaintextImpl(
        const std::string& plaintext,
        const SessionSupervisorLegacyPaperCleanupRequest* cleanupRequest,
        std::uint64_t nowMs,
        SessionSupervisorLegacyPaperCleanupResult* cleanupResult,
        std::string& reason);
    bool Encrypt(const std::string& plaintext, std::string& ciphertext,
                 std::string& nonce, std::string& tag) const;
    bool Decrypt(const std::string& ciphertext, const std::string& nonce,
                 const std::string& tag, std::string& plaintext) const;
    bool PersistLocked(std::string& reason,
                       std::string* storeSha256 = nullptr);

    mutable std::mutex m_mutex;
    std::string m_path;
    std::vector<unsigned char> m_key;
    std::map<std::string, SessionSupervisorLeaseRecord> m_records;
    std::map<std::string, SessionSupervisorPaperFinalizationAck>
        m_paperFinalizationAcks;
    bool m_sourceMetadataValid = false;
    std::uint64_t m_sourceDevice = 0;
    std::uint64_t m_sourceInode = 0;
    std::uint64_t m_sourceUid = 0;
    std::uint64_t m_sourceGid = 0;
    std::uint32_t m_sourceMode = 0;
    std::uint64_t m_sourceSize = 0;
    std::uint64_t m_sourceNlink = 0;
    std::int64_t m_sourceMtimeSec = 0;
    std::int64_t m_sourceMtimeNsec = 0;
    std::int64_t m_sourceCtimeSec = 0;
    std::int64_t m_sourceCtimeNsec = 0;
    std::string m_sourceSha256;
    int m_cleanupLockFd = -1;
    bool m_createMetadataValid = false;
    std::uint64_t m_createUid = 0;
    std::uint64_t m_createGid = 0;
    std::uint32_t m_createMode = 0;
};
