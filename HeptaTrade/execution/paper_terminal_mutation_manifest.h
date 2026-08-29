#pragma once

#include "execution_authority.h"

#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

struct PaperTerminalFenceBinding
{
    AgentExecutionContext owner;
    std::string finalizationId;
    std::string preliminaryReceiptSha256;
    std::uint64_t recoveryIngressFence = 0;
    std::string serviceEpoch;
    std::uint64_t serviceFencingGeneration = 0;
    std::uint64_t serviceProcessId = 0;
    std::uint64_t serviceProcessStartTicks = 0;
    std::uint64_t brokerConnectionEpoch = 0;
    std::string brokerSocketIdentitySha256;
};

struct PaperTerminalMutationRecord
{
    std::string agentId;
    std::string sessionId;
    std::string toolCallId;
    std::string operation;
    std::string venueCorrelationId;
};

struct PaperTerminalMutationUniverse
{
    std::vector<PaperTerminalMutationRecord> commands;
    std::vector<std::string> correlations;
    std::string commandSetSha256;
    std::string correlationSetSha256;
};

struct PaperTerminalMutationManifest
{
    std::string contents;
    std::string fileSha256;
    std::string bodySha256;
    PaperTerminalMutationUniverse universe;
    bool replay = false;
};

bool ValidPaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& binding, std::string& reason);
bool SamePaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& left,
    const PaperTerminalFenceBinding& right);
std::string EncodePaperTerminalFenceBinding(
    const PaperTerminalFenceBinding& binding);
bool DecodePaperTerminalFenceBinding(
    const std::string& encoded, PaperTerminalFenceBinding& binding,
    std::string& reason);

bool BuildPaperTerminalMutationUniverse(
    const std::vector<PaperTerminalMutationRecord>& records,
    PaperTerminalMutationUniverse& universe, std::string& reason);
bool BuildPaperTerminalMutationManifest(
    const PaperTerminalFenceBinding& binding,
    const PaperTerminalMutationUniverse& universe,
    PaperTerminalMutationManifest& manifest, std::string& reason);
bool CommitPaperTerminalMutationManifest(
    const std::string& stateDirectory,
    const PaperTerminalMutationManifest& desired,
    PaperTerminalMutationManifest& committed, std::string& reason);
bool LoadPaperTerminalMutationManifest(
    const std::string& stateDirectory,
    const PaperTerminalFenceBinding& expectedBinding,
    PaperTerminalMutationManifest& manifest, std::string& reason);

const char* PaperTerminalMutationManifestFileName();

// The terminal latch serializer lives with the manifest serializer so the
// runtime state translation unit remains an orchestration/state-commit unit.
// These functions intentionally preserve the original latch helper
// signatures and reason-code contract.
bool WriteTerminalLatchAtomic(
    const std::string& directoryPath,
    const std::string& contents,
    const std::string* expectedExistingContents,
    std::string& reason);
std::string TerminalLatchPrefix(
    const PaperTerminalFenceBinding& binding,
    const std::string& state,
    const PaperTerminalMutationManifest* manifest);
bool ReadSelfStartTicks(std::uint64_t& ticks);
void AppendTerminalAudit(
    std::ostringstream& out, const ExecutionControlResult& audit);

// Decoded, schema-validated HPT2 state used by the runtime replay path.  The
// manifest/core layer owns parsing and cross-file binding so the runtime
// composition layer only applies the resulting immutable state projection.
struct PaperTerminalLatchDecoded
{
    bool preparing = false;
    bool halted = false;
    PaperTerminalFenceBinding binding;
    PaperTerminalMutationManifest manifest;
    ExecutionControlResult terminal;
};

bool DecodePaperTerminalLatchContents(
    const std::string& stateDirectory,
    const std::string& contents,
    PaperTerminalLatchDecoded& decoded,
    std::string& reason);
