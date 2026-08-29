#pragma once

#include <cstdint>
#include <string>
#include <sys/types.h>

namespace hepta
{

struct PaperTerminalExternalLatchResult
{
    std::string latchContents;
    std::string latchSha256;
    std::string terminalizingLatchSha256;
    std::string capsuleFileSha256;
    std::string capsuleBodySha256;
    std::string recoveryId;
    std::string finalizationId;
    bool replay;

    PaperTerminalExternalLatchResult() : replay(false) {}
};

// Consumes a root-verifier-produced, systemd credential capsule and commits a
// separate external-witness terminal tombstone. The original HPT1 latch is
// opened and bound byte-for-byte but is never replaced or modified.
bool CommitPaperTerminalExternalLatch(
    const std::string& stateDirectory,
    const std::string& capsulePath,
    uid_t expectedOwnerUid,
    gid_t expectedOwnerGid,
    uid_t expectedCapsuleUid,
    gid_t expectedCapsuleGid,
    mode_t expectedCapsuleMode,
    PaperTerminalExternalLatchResult& result,
    std::string& reason);

const char* PaperTerminalExternalLatchFileName();

}
