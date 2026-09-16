#pragma once

#include <cassert>
#include <string>
#include <vector>

inline PaperTerminalFenceBinding MakeTerminalCapacityBinding()
{
    PaperTerminalFenceBinding binding;
    binding.owner.agentId = "capacity-agent";
    binding.owner.sessionId = "capacity-session";
    binding.owner.account = "DU-CAPACITY";
    binding.owner.executionDomain = "PAPER";
    binding.finalizationId = "capacity-finalization";
    binding.preliminaryReceiptSha256 = "sha256:" + std::string(64, 'a');
    binding.recoveryIngressFence = 7;
    binding.serviceEpoch = "service-epoch-capacity";
    binding.serviceFencingGeneration = 9;
    binding.serviceProcessId = 123;
    binding.serviceProcessStartTicks = 456;
    binding.brokerConnectionEpoch = 11;
    binding.brokerSocketIdentitySha256 = "sha256:" + std::string(64, 'b');
    return binding;
}

inline void TestTerminalManifestHasNo4096HistoryCeiling()
{
    std::vector<PaperTerminalMutationRecord> records;
    records.reserve(5000);
    for (int i = 0; i < 5000; ++i)
    {
        PaperTerminalMutationRecord record;
        record.agentId = "agent";
        record.sessionId = "session";
        record.toolCallId = "command-" + std::to_string(i);
        record.operation = "place";
        record.venueCorrelationId = "corr-" + std::to_string(i);
        records.push_back(record);
    }
    PaperTerminalMutationUniverse universe;
    std::string reason;
    assert(BuildPaperTerminalMutationUniverse(records, universe, reason));
    assert(universe.commandCount == 5000);
    assert(universe.correlationCount == 5000);
    PaperTerminalMutationManifest manifest;
    assert(BuildPaperTerminalMutationManifest(
        MakeTerminalCapacityBinding(), universe, manifest, reason));
    assert(manifest.contents.compare(0, 5, "HPM2\n") == 0);
    assert(manifest.universe.commandCount == 5000);
    assert(manifest.universe.correlationCount == 5000);
    assert(manifest.contents.size() < 4096);
    assert(manifest.contents.find("command=") == std::string::npos);
    assert(manifest.contents.find("correlation=") == std::string::npos);

    PaperTerminalMutationUniverse partitioned;
    const std::string empty =
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    std::vector<PaperTerminalMutationRecord> tail;
    assert(BuildPaperTerminalPartitionedUniverse(
        1000000000ULL, empty, 900000000ULL, empty,
        tail, partitioned, reason));
    assert(partitioned.compactSummary);
    assert(partitioned.commandCount == 1000000000ULL);
    assert(partitioned.correlationCount == 900000000ULL);
    assert(partitioned.commands.empty());
    assert(partitioned.correlations.empty());
    assert(BuildPaperTerminalMutationManifest(
        MakeTerminalCapacityBinding(), partitioned, manifest, reason));
    assert(manifest.contents.size() < 4096);
}
