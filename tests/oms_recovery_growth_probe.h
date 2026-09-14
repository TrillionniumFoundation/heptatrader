#pragma once

#include <chrono>
#include <sys/resource.h>

// Opt-in bounded measurement against the actual coordinator and journal. All
// orders are synthetic; the only venue callback increments an in-process count.
// No input journal path, broker wrapper, credentials or host service is used.
namespace {
int RunRecoveryGrowthProbe(unsigned requested)
{
    assert(requested > 0 && requested <= 20000);
    const auto path = TempJournalPath();
    const auto expiry = OmsJournal::NowEpochMs() + 86400000;
    const auto commandFor = [&](unsigned i) {
        auto command = MakePlace("growth-" + std::to_string(i));
        command.expiresAtMs = expiry;
        return command;
    };
    unsigned sends = 0, accepted = 0;
    bool paused = false;
    OmsJournalHealthSnapshot health;
    auto callbacks = CancelFixtureCallbacks();
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(1000 + ++sends);
        });
    {
        OmsJournal journal;
        assert(journal.Init(path));
        ExecutionCoordinator coordinator(journal, callbacks);
        for (unsigned i = 0; i < requested; ++i)
        {
            const auto result = coordinator.PlaceOrder(commandFor(i));
            if (result.status != ExecutionCommandStatus::Accepted)
            {
                assert(result.reasonCode == "OMS_NEW_ENTRY_CAPACITY_EXHAUSTED");
                paused = true;
                break;
            }
            ++accepted;
            std::string reason;
            // This is a deliberately synthetic terminal observation, NOT a
            // broker-derived order or economic fill/flatness claim.
            assert(coordinator.RecordOrderTerminalDurably(result.orderId, &reason));
        }
        assert(sends == accepted);
        assert(coordinator.RuntimeObservation().retainedCommands == accepted);
        assert(coordinator.RuntimeObservation().orderOwners == 0);
        health = journal.GetHealthSnapshot();
    }
    std::uint64_t replayNs = 0;
    {
        OmsJournal journal;
        assert(journal.Init(path));
        ExecutionCoordinator recovered(journal, callbacks);
        const auto started = std::chrono::steady_clock::now();
        std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        replayNs = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - started).count());
        const auto state = recovered.RuntimeObservation();
        assert(state.retainedCommands == accepted);
        assert(state.retainedSendAttempts == accepted && state.orderOwners == 0);
        if (accepted)
        {
            assert(recovered.PlaceOrder(commandFor(0)).status == ExecutionCommandStatus::Duplicate);
            assert(recovered.PlaceOrder(commandFor(accepted - 1)).status == ExecutionCommandStatus::Duplicate);
        }
        assert(sends == accepted); // recovery/status never invokes the venue
    }
    struct rusage usage{};
    assert(getrusage(RUSAGE_SELF, &usage) == 0);
    std::cout << "{\"schema\":\"heptatrader.synthetic-recovery-growth.v1\","
              << "\"synthetic\":true,\"broker_io\":false,\"requested_orders\":" << requested
              << ",\"accepted_orders\":" << accepted
              << ",\"entry_paused\":" << (paused ? "true" : "false")
              << ",\"journal_records\":" << health.currentRecords
              << ",\"decoded_bytes\":" << health.currentBytes
              << ",\"max_replay_bytes\":" << health.replayMaxBytes
              << ",\"max_replay_records\":" << health.replayMaxRecords
              << ",\"replay_ns\":" << replayNs
              << ",\"process_peak_rss_kib\":" << usage.ru_maxrss
              << ",\"oldest_and_newest_identity_retained\":true}\n";
    assert(std::remove(path.c_str()) == 0);
    return 0;
}
} // namespace
