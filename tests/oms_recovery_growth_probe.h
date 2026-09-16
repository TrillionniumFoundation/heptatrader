#pragma once

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <string>
#include <vector>
#include <sys/resource.h>
#include <sys/wait.h>
#include <unistd.h>

// Opt-in bounded measurement against the actual coordinator and journal. All
// orders are synthetic; the only venue callback increments an in-process count.
// No input journal path, broker wrapper, credentials or host service is used.
namespace {

int RunPython(const std::vector<std::string>& arguments)
{
    std::vector<char*> argv;
    argv.reserve(arguments.size() + 1U);
    for (std::size_t i = 0; i < arguments.size(); ++i)
        argv.push_back(const_cast<char*>(arguments[i].c_str()));
    argv.push_back(nullptr);
    const pid_t child = ::fork();
    assert(child >= 0);
    if (child == 0)
    {
        ::execvp(argv[0], argv.data());
        ::_exit(127);
    }
    int status = 0;
    while (::waitpid(child, &status, 0) < 0)
        assert(errno == EINTR);
    if (!WIFEXITED(status)) return 128;
    return WEXITSTATUS(status);
}

void RemoveGenerationFixture(const std::string& store)
{
    const std::vector<std::string> command = {
        "python3", "-c",
        "import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)",
        store};
    assert(RunPython(command) == 0);
}

void TestNativeGenerationRecoveryAndPermanentIdentity()
{
#ifndef HEPTA_SOURCE_ROOT
#error "HEPTA_SOURCE_ROOT is required for the native generation fixture"
#endif
    const std::string path = TempJournalPath();
    const std::string store = path + ".generations";
    const std::string lifecycle =
        std::string(HEPTA_SOURCE_ROOT) + "/scripts/hepta_oms_lifecycle.py";
    const auto expiry = OmsJournal::NowEpochMs() + 86400000;
    auto oldCommand = MakePlace("generation-old-command");
    oldCommand.expiresAtMs = expiry;
    int sends = 0;
    auto callbacks = CancelFixtureCallbacks();
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(7100 + ++sends);
        });
    {
        OmsJournal journal;
        assert(journal.Init(path));
        ExecutionCoordinator coordinator(journal, callbacks);
        const auto result = coordinator.PlaceOrder(oldCommand);
        assert(result.status == ExecutionCommandStatus::Accepted);
        std::string reason;
        assert(coordinator.RecordOrderTerminalDurably(result.orderId, &reason));
        assert(coordinator.RuntimeObservation().orderOwners == 0);
        assert(sends == 1);
    }

    // Exercise the exact v2 stopped-state producer after the real writer has
    // released its shared flock. It seals immutable history, atomically rotates
    // the active path to a lineage-bound tail, then publishes CURRENT and
    // CURRENT.runtime. Native restart must consume that exact format.
    const std::vector<std::string> build = {
        "python3", lifecycle, "seal", "--journal", path,
        "--store", store, "--stopped-state"};
    assert(RunPython(build) == 0);

    {
        OmsJournal journal;
        assert(journal.Init(path));
        assert(!journal.GetHealthSnapshot().capacityKnown);
        ExecutionCoordinator recovered(journal, callbacks);
        std::string reason;
        assert(recovered.RecoverFromJournal(reason));
        const OmsJournalHealthSnapshot capacity = journal.GetHealthSnapshot();
        assert(capacity.capacityKnown);
        assert(capacity.currentBytes == 0);
        assert(capacity.currentRecords == 0);
        assert(recovered.RuntimeObservation().retainedCommands == 0);

        ExecutionCommandResult status;
        assert(recovered.GetCommandStatus(
            oldCommand.context.agentId, oldCommand.context.sessionId,
            oldCommand.context.toolCallId, status));
        assert(status.status == ExecutionCommandStatus::Accepted);
        assert(recovered.PlaceOrder(oldCommand).status == ExecutionCommandStatus::Duplicate);
        auto conflict = oldCommand;
        conflict.order.totalQuantity += 1.0;
        assert(recovered.PlaceOrder(conflict).reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
        assert(sends == 1); // disk lookup never calls the venue

        auto newCommand = MakePlace("generation-new-command");
        newCommand.expiresAtMs = expiry;
        const auto next = recovered.PlaceOrder(newCommand);
        assert(next.status == ExecutionCommandStatus::Accepted);
        assert(sends == 2); // capacity was adopted, so new entry is not UNKNOWN

        std::vector<std::int64_t> attempts;
        recovered.GetPlaceSendAttemptTimes(
            oldCommand.context.account, oldCommand.context.executionDomain,
            0, attempts);
        assert(attempts.size() >= 2); // one disk-backed sealed attempt + one hot tail
    }
    assert(std::remove(path.c_str()) == 0);
    RemoveGenerationFixture(store);
}

int RunRecoveryGrowthProbe(unsigned requested)
{
    assert(requested > 0 && requested <= 20000);
    if (requested == 16)
        TestNativeGenerationRecoveryAndPermanentIdentity();
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
