#pragma once

#include "strategy_runtime_control.h"
#include <memory>

// A copy of verified opaque bytes, not a process/venue capability or a claim
// that this is the latest checkpoint. Issued only by the bounded local store.
class VerifiedStrategyCheckpoint
{
public:
    bool IsValid() const noexcept { return static_cast<bool>(m_data); }
    const StrategyArtifactDescriptor& Descriptor() const;
    const std::string& Payload() const;
    const std::string& PayloadDigest() const;
    const std::string& RecordDigest() const;
    std::uint64_t Sequence() const noexcept;
    std::uint64_t SourceGeneration() const noexcept;
    std::uint64_t SavedAtMs() const noexcept;

private:
    struct Data;
    std::shared_ptr<const Data> m_data;
    friend class StrategyCheckpointStore;
};

struct StrategyCheckpointResult
{
    bool accepted = false;
    bool duplicate = false;
    // True means rename may have published new bytes, but durability was not
    // acknowledged. Exceptions after persistence starts are also uncertain.
    bool uncertain = false;
    const char* reasonCode = "CHECKPOINT_NOT_READY";
    std::string attemptedRecordDigest;
    VerifiedStrategyCheckpoint checkpoint;
};

// Linux, local cooperating writers only. The pre-existing absolute directory
// must be private (0700). No hidden directory creation or permission repair.
// The caller selects a trusted path/descriptor and retains the expected record
// digest independently. This object never executes or deserializes payloads.
class StrategyCheckpointStore
{
public:
    StrategyCheckpointStore(std::string directory, std::string filename,
                            StrategyArtifactDescriptor descriptor,
                            std::size_t maximumPayloadBytes = 1024u * 1024u);
    static const char* Version() noexcept { return "hepta.strategy-checkpoint-store.v1"; }

    // Empty expected digest requires absence; it is NOT trust-on-first-use.
    // Existing bytes require their exact independently selected SHA-256 digest.
    StrategyCheckpointResult Load(const std::string& expectedRecordDigest);
    // Load is mandatory. A new sequence must equal the loaded sequence + 1.
    // Duplicates require the same sequence, generation, time and payload bytes.
    StrategyCheckpointResult Save(std::uint64_t sequence,
                                  std::uint64_t sourceGeneration,
                                  const std::string& payload,
                                  std::uint64_t savedAtMs);
    bool IsReady() const;

private:
    std::string m_directory;
    std::string m_filename;
    StrategyArtifactDescriptor m_descriptor;
    std::size_t m_maximumPayloadBytes;
    mutable std::mutex m_mutex;
    bool m_ready = false;
    bool m_haveBinding = false;
    std::uintmax_t m_directoryDevice = 0, m_directoryInode = 0;
    std::uintmax_t m_lockDevice = 0, m_lockInode = 0;
    VerifiedStrategyCheckpoint m_current;
};
