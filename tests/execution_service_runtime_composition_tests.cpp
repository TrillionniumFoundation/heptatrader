#include "../HeptaTrade/execution/execution_service_runtime_composition.h"
#include "../HeptaTrade/execution/execution_coordinator.h"
#include "../HeptaTrade/execution/execution_event_feed.h"
#include "../HeptaTrade/execution/unix_execution_service.h"

#include <cassert>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <limits>
#include <set>
#include <string>
#include <thread>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <vector>

namespace
{
std::string TempDirectory(const char* pattern)
{
    std::string value(pattern);
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    char* created = ::mkdtemp(buffer.data());
    assert(created != nullptr);
    assert(::chmod(created, 0700) == 0);
    return std::string(created);
}

std::string TempSocketPath()
{
    std::string value("/tmp/hepta-execution-runtime-XXXXXX");
    std::vector<char> buffer(value.begin(), value.end());
    buffer.push_back('\0');
    const int temporary = ::mkstemp(buffer.data());
    assert(temporary >= 0);
    ::close(temporary);
    ::unlink(buffer.data());
    return std::string(buffer.data()) + ".sock";
}

int ActivatedSocket(const std::string& path)
{
    const int fd = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    assert(fd >= 0);
    struct sockaddr_un address;
    std::memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    assert(path.size() < sizeof(address.sun_path));
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    assert(::bind(fd, reinterpret_cast<struct sockaddr*>(&address), sizeof(address)) == 0);
    assert(::chmod(path.c_str(), 0600) == 0);
    assert(::listen(fd, 8) == 0);
    return fd;
}

void WriteCredential(const std::string& directory, const std::string& contents)
{
    const std::string path = directory + "/hepta-execution-fence";
    std::ofstream output(path.c_str(), std::ios::out | std::ios::trunc);
    assert(output.is_open());
    output << contents;
    output.close();
    assert(::chmod(path.c_str(), 0400) == 0);
}

ExecutionServiceRuntimeConfig Config(int listenFd,
                                     int eventListenFd,
                                     const std::string& stateDirectory,
                                     const std::string& credentialDirectory)
{
    ExecutionServiceRuntimeConfig config;
    config.mode = ExecutionServiceRuntimeMode::Simulator;
    config.listenFd = listenFd;
    config.eventListenFd = eventListenFd;
    config.allowedGatewayUids.insert(static_cast<std::uint32_t>(::geteuid()));
    config.gatewayContextBinding.agentId = "runtime-test-agent";
    config.gatewayContextBinding.account = "SIM";
    config.gatewayContextBinding.venue = "SIMULATOR";
    config.gatewayContextBinding.executionDomain = "SIM:runtime-test-agent";
    config.stateDirectory = stateDirectory;
    config.journalPath = stateDirectory + "/oms-journal.jsonl";
    config.fenceCredentialPath = credentialDirectory + "/hepta-execution-fence";
    config.ioTimeoutMs = 1000;
    return config;
}

IbPlaceOrderCommand PlaceCommand(const std::string& commandId,
                                 std::uint64_t token = 77,
                                 std::uint64_t generation = 9)
{
    IbPlaceOrderCommand command;
    command.context.agentId = "runtime-test-agent";
    command.context.sessionId = "runtime-test-session";
    command.context.toolCallId = commandId;
    command.context.strategy = "simulator-runtime-test";
    command.context.account = "SIM";
    command.context.venue = "SIMULATOR";
    command.context.executionDomain = "SIM:runtime-test-agent";
    command.context.decisionLeaseFencingToken = token;
    command.context.decisionLeaseGeneration = generation;
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.contract.currency = "USD";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 1000.0;
    command.order.lmtPrice = 1.0990;
    command.timeInForce = "DAY";
    command.instrument = "EUR.USD";
    command.referencePrice = 1.1001;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return command;
}

std::string PreviewStringField(
    const ExecutionCommandResult& preview,
    const std::string& field)
{
    const std::string marker = "\"" + field + "\":\"";
    const std::size_t begin = preview.detail.find(marker);
    assert(begin != std::string::npos);
    const std::size_t value = begin + marker.size();
    const std::size_t end = preview.detail.find('"', value);
    assert(end != std::string::npos);
    return preview.detail.substr(value, end - value);
}

std::uint64_t JsonUnsignedField(
    const std::string& payload,
    const std::string& field)
{
    const std::string marker = "\"" + field + "\":";
    const std::size_t begin = payload.find(marker);
    assert(begin != std::string::npos);
    const std::size_t value = begin + marker.size();
    std::size_t end = value;
    while (end < payload.size() &&
           payload[end] >= '0' && payload[end] <= '9')
        ++end;
    assert(end > value);
    return static_cast<std::uint64_t>(
        std::strtoull(payload.substr(value, end - value).c_str(), nullptr, 10));
}

IbPlaceOrderCommand Previewed(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& input)
{
    IbPlaceOrderCommand command = input;
    command.previewPermit.clear();
    const ExecutionCommandResult preview = client.PreviewOrder(command);
    assert(preview.status == ExecutionCommandStatus::Accepted);
    command.previewPermit = PreviewStringField(preview, "preview_permit");
    command.context.toolCallId = PreviewStringField(preview, "command_id");
    return command;
}

IbCancelOrderCommand CancelCommand(long orderId,
                                   const std::string& commandId,
                                   std::uint64_t token,
                                   std::uint64_t generation)
{
    const IbPlaceOrderCommand place = PlaceCommand(commandId, token, generation);
    IbCancelOrderCommand command;
    command.context = place.context;
    command.orderId = orderId;
    command.instrument = place.instrument;
    command.side = place.order.action;
    return command;
}

ExecutionControlCommand ControlCommand(const IbPlaceOrderCommand& place,
                                       const std::string& commandId,
                                       const std::string& targetCommandId = std::string())
{
    ExecutionControlCommand command;
    command.context = place.context;
    command.context.toolCallId = commandId;
    command.targetCommandId = targetCommandId;
    return command;
}

ExecutionControlCommand ControlCommandWithFence(
    const IbPlaceOrderCommand& place,
    const std::string& commandId,
    std::uint64_t token,
    std::uint64_t generation,
    const std::string& targetCommandId = std::string())
{
    ExecutionControlCommand command = ControlCommand(place, commandId, targetCommandId);
    command.context.decisionLeaseFencingToken = token;
    command.context.decisionLeaseGeneration = generation;
    return command;
}

void AssertGatewayContextRejected(
    UnixExecutionServiceClient& client,
    const IbPlaceOrderCommand& command)
{
    const ExecutionCommandResult result = client.PreviewOrder(command);
    assert(result.status == ExecutionCommandStatus::Rejected);
    assert(result.reasonCode ==
        "EXECUTION_GATEWAY_CONTEXT_BINDING_MISMATCH");
}

void Cleanup(const std::string& socketPath,
             const std::string& eventSocketPath,
             const std::string& stateDirectory,
             const std::string& credentialDirectory)
{
    ::unlink(socketPath.c_str());
    ::unlink(eventSocketPath.c_str());
    ::unlink((stateDirectory + "/oms-journal.jsonl").c_str());
    ::unlink((stateDirectory + "/execution-runtime.lock").c_str());
    ::unlink((credentialDirectory + "/hepta-execution-fence").c_str());
    ::rmdir(stateDirectory.c_str());
    ::rmdir(credentialDirectory.c_str());
}

void TestActivatedRuntimeAndPrivateState()
{
    const std::string stateDirectory = TempDirectory("/tmp/hepta-execution-state-XXXXXX");
    const std::string credentialDirectory = TempDirectory("/tmp/hepta-execution-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    ExecutionServiceIdentity firstServiceIdentity;
    WriteCredential(credentialDirectory, "HFC1\nfencing_token=77\ngeneration=9\n");
    {
        ExecutionServiceRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventSocketPath),
                   stateDirectory, credentialDirectory));
        std::string reason;
        assert(runtime.Start(reason));
        assert(reason.empty());
        assert(runtime.IsRunning());
        assert(runtime.RecoveryReason().empty());
        UnixExecutionServiceClient client(socketPath, 1000);
        UnixExecutionEventFeedClient eventClient(eventSocketPath, 1000);
        std::string identityReason;
        ExecutionServiceIdentity mutationIdentity;
        assert(client.GetServiceIdentity(mutationIdentity, identityReason));
        const ExecutionEventReadResult eventIdentity =
            eventClient.GetServiceIdentity();
        assert(eventIdentity.status == ExecutionEventReadStatus::ServiceIdentity);
        assert(eventIdentity.serviceIdentity.serviceEpoch ==
            mutationIdentity.serviceEpoch);
        assert(eventIdentity.serviceIdentity.serviceFencingGeneration ==
            mutationIdentity.serviceFencingGeneration);
        assert(mutationIdentity.serviceEpoch == runtime.EventHub().StreamEpoch());
        assert(mutationIdentity.serviceFencingGeneration == 9);
        firstServiceIdentity = mutationIdentity;
        {
            IbPlaceOrderCommand mismatched =
                PlaceCommand("runtime-agent-mismatch");
            mismatched.context.agentId = "other-agent";
            AssertGatewayContextRejected(client, mismatched);
        }
        {
            IbPlaceOrderCommand mismatched =
                PlaceCommand("runtime-account-mismatch");
            mismatched.context.account = "OTHER";
            AssertGatewayContextRejected(client, mismatched);
        }
        {
            IbPlaceOrderCommand mismatched =
                PlaceCommand("runtime-venue-mismatch");
            mismatched.context.venue = "OTHER";
            AssertGatewayContextRejected(client, mismatched);
        }
        {
            IbPlaceOrderCommand mismatched =
                PlaceCommand("runtime-domain-mismatch");
            mismatched.context.executionDomain = "SIM:other-agent";
            AssertGatewayContextRejected(client, mismatched);
        }
        const IbPlaceOrderCommand place =
            Previewed(client, PlaceCommand("runtime-place"));
        const ExecutionCommandResult accepted = client.PlaceIbOrder(place);
        if (accepted.status != ExecutionCommandStatus::Accepted)
            std::cerr << "initial place rejected: " << accepted.reasonCode
                      << " detail=" << accepted.detail << std::endl;
        assert(accepted.status == ExecutionCommandStatus::Accepted);
        assert(accepted.orderId == 1000000);
        ExecutionEventFeedRequest eventRequest;
        eventRequest.executionDomain = place.context.executionDomain;
        eventRequest.agentId = place.context.agentId;
        eventRequest.sessionId = place.context.sessionId;
        eventRequest.expectedServiceIdentity = mutationIdentity;
        const ExecutionEventReadResult acceptedEvent = eventClient.Wait(eventRequest);
        assert(acceptedEvent.status == ExecutionEventReadStatus::Event);
        assert(acceptedEvent.event.type == "order.accepted");
        assert(acceptedEvent.event.orderId == accepted.orderId);
        {
            ExecutionEventFeedRequest mismatched = eventRequest;
            mismatched.agentId = "other-agent";
            const ExecutionEventReadResult rejected =
                eventClient.Wait(mismatched);
            assert(rejected.status == ExecutionEventReadStatus::InvalidOwner);
            assert(rejected.reasonCode ==
                "EXECUTION_EVENT_GATEWAY_CONTEXT_BINDING_MISMATCH");
        }
        {
            ExecutionEventFeedRequest mismatched = eventRequest;
            mismatched.executionDomain = "SIM:other-agent";
            const ExecutionEventReadResult rejected =
                eventClient.Wait(mismatched);
            assert(rejected.status == ExecutionEventReadStatus::InvalidOwner);
            assert(rejected.reasonCode ==
                "EXECUTION_EVENT_GATEWAY_CONTEXT_BINDING_MISMATCH");
        }
        const ExecutionCommandResult duplicate = client.PlaceIbOrder(place);
        assert(duplicate.status == ExecutionCommandStatus::Duplicate);
        assert(duplicate.orderId == 1000000);

        const std::uint64_t quoteNow =
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
        struct InvalidQuote
        {
            double bid;
            double ask;
            std::uint64_t observedAtMs;
            std::uint64_t staleAfterMs;
            const char* reasonCode;
        };
        const InvalidQuote invalidQuotes[] = {
            {std::numeric_limits<double>::quiet_NaN(), 1.1002,
             quoteNow, quoteNow + 5000,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {-1.0, 1.1002, quoteNow, quoteNow + 5000,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {0.0, 1.1002, quoteNow, quoteNow + 5000,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {1.1003, 1.1002, quoteNow, quoteNow + 5000,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {1.1000, 1.1002, quoteNow + 1000, quoteNow + 5000,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {1.1000, 1.1002, quoteNow, quoteNow - 1,
             "AUTHORITATIVE_QUOTE_UNAVAILABLE"},
            {1.1000, 1.1002, quoteNow - 100, quoteNow - 1,
             "AUTHORITATIVE_QUOTE_STALE"}
        };
        for (std::size_t i = 0;
             i < sizeof(invalidQuotes) / sizeof(invalidQuotes[0]); ++i)
        {
            runtime.Venue().SetQuoteObserved(
                "EUR.USD", invalidQuotes[i].bid, invalidQuotes[i].ask,
                invalidQuotes[i].observedAtMs,
                invalidQuotes[i].staleAfterMs);
            const ExecutionCommandResult invalidPreview =
                client.PreviewOrder(PlaceCommand(
                    "runtime-invalid-quote-" + std::to_string(i)));
            assert(invalidPreview.status ==
                   ExecutionCommandStatus::Rejected);
            assert(invalidPreview.reasonCode ==
                   invalidQuotes[i].reasonCode);
        }
        runtime.Venue().SetQuote("EUR.USD", 1.1000, 1.1002);
        const IbPlaceOrderCommand quoteRace =
            Previewed(client, PlaceCommand("runtime-quote-race"));
        runtime.Venue().SetQuoteObserved(
            "EUR.USD", 1.1003, 1.1002,
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()),
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()) + 5000);
        const ExecutionCommandResult invalidAtFinalPlace =
            client.PlaceIbOrder(quoteRace);
        assert(invalidAtFinalPlace.status ==
               ExecutionCommandStatus::Rejected);
        assert(invalidAtFinalPlace.reasonCode ==
               "AUTHORITATIVE_QUOTE_UNAVAILABLE");
        runtime.Venue().SetQuote("EUR.USD", 1.1000, 1.1002);

        IbPlaceOrderCommand staleInput = PlaceCommand("runtime-stale", 76, 9);
        staleInput.order.totalQuantity = 0.0;
        const ExecutionCommandResult stale = client.PreviewOrder(staleInput);
        assert(stale.status == ExecutionCommandStatus::Rejected);
        assert(stale.reasonCode == "INVALID_ORDER");
        const ExecutionCommandResult staleCancel = client.CancelIbOrder(
            CancelCommand(-1, "runtime-stale-cancel", 76, 9));
        assert(staleCancel.status == ExecutionCommandStatus::Rejected);
        assert(staleCancel.reasonCode == "INVALID_CANCEL");

        const std::string staleTokenQueryId = "runtime-status-stale-token";
        const ExecutionControlResult upstreamFenceIndependent =
            client.QueryCommandStatus(ControlCommandWithFence(
                place, staleTokenQueryId, 76, 8,
                place.context.toolCallId));
        assert(upstreamFenceIndependent.status == ExecutionCommandStatus::Accepted);
        assert(upstreamFenceIndependent.targetStatus ==
            ExecutionCommandStatus::Accepted);
        const ExecutionControlResult status = client.QueryCommandStatus(
            ControlCommand(place, "runtime-status", place.context.toolCallId));
        assert(status.status == ExecutionCommandStatus::Accepted);
        assert(status.targetCommandId == place.context.toolCallId);
        assert(status.targetStatus == ExecutionCommandStatus::Accepted);
        assert(status.orderId == accepted.orderId);

        IbPlaceOrderCommand gbp = PlaceCommand("runtime-place-gbp", 8001, 17);
        gbp.contract.symbol = "GBP";
        gbp.contract.currency = "USD";
        gbp.instrument = "GBP.USD";
        gbp.order.lmtPrice = 1.2490;
        gbp.referencePrice = 1.2501;
        gbp = Previewed(client, gbp);
        const ExecutionCommandResult gbpAccepted = client.PlaceIbOrder(gbp);
        assert(gbpAccepted.status == ExecutionCommandStatus::Accepted);
        assert(gbpAccepted.orderId == 1000001);
        IbCancelOrderCommand gbpCancel;
        gbpCancel.context = gbp.context;
        gbpCancel.context.toolCallId = "runtime-cancel-gbp";
        gbpCancel.context.decisionLeaseFencingToken = 9001;
        gbpCancel.context.decisionLeaseGeneration = 23;
        gbpCancel.orderId = gbpAccepted.orderId;
        gbpCancel.instrument = gbp.instrument;
        gbpCancel.side = gbp.order.action;
        const ExecutionCommandResult gbpCancelled =
            client.CancelIbOrder(gbpCancel);
        assert(gbpCancelled.status == ExecutionCommandStatus::Accepted);

        ExecutionControlCommand wrongOwner = ControlCommand(
            place, "runtime-status-wrong-owner", place.context.toolCallId);
        wrongOwner.context.sessionId = "another-session";
        const ExecutionControlResult hidden = client.QueryCommandStatus(wrongOwner);
        assert(hidden.status == ExecutionCommandStatus::Rejected);
        assert(hidden.reasonCode == "EXECUTION_COMMAND_NOT_FOUND");

        const ExecutionCommandResult cancelled = client.CancelIbOrder(
            CancelCommand(accepted.orderId, "runtime-cancel", 77, 9));
        assert(cancelled.status == ExecutionCommandStatus::Accepted);

        const IbPlaceOrderCommand previewBeforeFence =
            Previewed(client, PlaceCommand("runtime-preview-before-fence"));
        const ExecutionControlResult fenced = client.FenceSessionOwner(
            ControlCommand(place, "runtime-fence"));
        assert(fenced.status == ExecutionCommandStatus::Accepted);
        assert(fenced.affectedCount == 2);
        assert(runtime.Coordinator().IsSessionOwnerFenced(
            place.context.agentId, place.context.sessionId));
        const ExecutionCommandResult fencedPlace = client.PreviewOrder(
            PlaceCommand("runtime-fenced-place"));
        assert(fencedPlace.status == ExecutionCommandStatus::Rejected);
        assert(fencedPlace.reasonCode == "SESSION_OWNER_FENCED");

        runtime.Venue().Process();
        const ExecutionControlResult released = client.ReleaseSessionOwnerFence(
            ControlCommand(place, "runtime-release"));
        assert(released.status == ExecutionCommandStatus::Accepted);
        assert(released.affectedCount == 2);
        assert(!runtime.Coordinator().IsSessionOwnerFenced(
            place.context.agentId, place.context.sessionId));
        const ExecutionCommandResult revokedPreviewAfterRelease =
            client.PlaceIbOrder(previewBeforeFence);
        assert(revokedPreviewAfterRelease.status ==
               ExecutionCommandStatus::Rejected);
        assert(revokedPreviewAfterRelease.reasonCode ==
               "EXECUTION_PREVIEW_PERMIT_UNKNOWN_OR_CONSUMED");
        const ExecutionCommandResult afterRelease = client.PlaceIbOrder(
            Previewed(client, PlaceCommand("runtime-after-release")));
        assert(afterRelease.status == ExecutionCommandStatus::Accepted);
        assert(afterRelease.orderId == 1000002);

        const ExecutionCommandResult afterReleaseCancelled = client.CancelIbOrder(
            CancelCommand(afterRelease.orderId, "runtime-after-release-cancel", 77, 9));
        assert(afterReleaseCancelled.status == ExecutionCommandStatus::Accepted);
        runtime.Venue().Process();
        ExecutionOrderOwner unreconciledOwner;
        assert(runtime.Coordinator().GetOrderOwner(
            afterRelease.orderId, unreconciledOwner));
        const ExecutionControlResult reconciled = client.ReconcileAuthoritativeState(
            ControlCommand(place, "runtime-reconcile"));
        assert(reconciled.status == ExecutionCommandStatus::Accepted);
        assert(!reconciled.mutationBlocked);
        assert(reconciled.affectedCount == 1);
        assert(!runtime.Coordinator().GetOrderOwner(
            afterRelease.orderId, unreconciledOwner));

        struct stat journal;
        assert(::stat((stateDirectory + "/oms-journal.jsonl").c_str(), &journal) == 0);
        assert(S_ISREG(journal.st_mode));
        assert((journal.st_mode & 0777) == 0600);
        runtime.Stop();
        runtime.Stop();
        assert(!runtime.IsRunning());
        struct stat socketMetadata;
        assert(::lstat(socketPath.c_str(), &socketMetadata) == 0);
        assert(S_ISSOCK(socketMetadata.st_mode));
    }

    // A fresh Simulator process has no active venue orders. Replay must
    // durably reconcile the prior owner to terminal rather than retaining a
    // coordinator owner for an order absent from the authoritative venue.
    assert(::unlink(socketPath.c_str()) == 0);
    assert(::unlink(eventSocketPath.c_str()) == 0);
    {
        ExecutionServiceRuntimeComposition restarted(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventSocketPath),
                   stateDirectory, credentialDirectory));
        std::string reason;
        assert(restarted.Start(reason));
        UnixExecutionEventFeedClient restartedEvents(eventSocketPath, 1000);
        ExecutionEventFeedRequest oldEpochRequest;
        oldEpochRequest.executionDomain = "SIM:runtime-test-agent";
        oldEpochRequest.agentId = "runtime-test-agent";
        oldEpochRequest.sessionId = "runtime-test-session";
        oldEpochRequest.expectedServiceIdentity = firstServiceIdentity;
        const ExecutionEventReadResult epochChanged = restartedEvents.Wait(oldEpochRequest);
        assert(epochChanged.status ==
            ExecutionEventReadStatus::ServiceIdentityMismatch);
        assert(epochChanged.serviceIdentity.serviceEpoch !=
            firstServiceIdentity.serviceEpoch);
        assert(epochChanged.serviceIdentity.serviceFencingGeneration ==
            firstServiceIdentity.serviceFencingGeneration);
        ExecutionOrderOwner owner;
        assert(!restarted.Coordinator().GetOrderOwner(1000000, owner));
    }
    {
        OmsJournal journal;
        assert(journal.Init(stateDirectory + "/oms-journal.jsonl"));
        bool foundTerminalReconcile = false;
        assert(journal.Replay([&foundTerminalReconcile](const OmsJournalEvent& event) {
            if (event.eventType == "order_owner_reconciled_terminal" && event.orderId == 1000000)
                foundTerminalReconcile = true;
        }) >= 0);
        assert(foundTerminalReconcile);
    }
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestDegradedRecoveryStillServesFailClosed()
{
    const std::string stateDirectory = TempDirectory("/tmp/hepta-execution-degraded-XXXXXX");
    const std::string credentialDirectory = TempDirectory("/tmp/hepta-execution-degraded-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    WriteCredential(credentialDirectory, "HFC1\nfencing_token=77\ngeneration=9\n");
    {
        OmsJournal journal;
        assert(journal.Init(stateDirectory + "/oms-journal.jsonl"));
        OmsJournalEvent intent;
        intent.eventType = "order_intent";
        intent.tsMs = OmsJournal::NowEpochMs();
        intent.reqId = "unfinished-command";
        intent.clientReqId = intent.reqId;
        intent.eventId = intent.reqId;
        intent.traceId = "runtime-test-session";
        intent.source = "agent.tool:runtime-test-agent";
        intent.venue = "SIMULATOR";
        intent.account = "SIM";
        intent.instrument = "EUR.USD";
        intent.side = "BUY";
        intent.status = "intent_recorded";
        intent.requestHash = "sha256:unfinished-command";
        intent.venueCorrelationId = "hepta-v1-unfinished-command";
        assert(journal.Append(intent));
    }
    assert(::chmod((stateDirectory + "/oms-journal.jsonl").c_str(), 0600) == 0);
    {
        ExecutionServiceRuntimeComposition runtime(
            Config(ActivatedSocket(socketPath), ActivatedSocket(eventSocketPath),
                   stateDirectory, credentialDirectory));
        std::string reason;
        assert(runtime.Start(reason));
        assert(runtime.IsRunning());
        assert(runtime.RecoveryReason() == "RECOVERY_RECONCILE_REQUIRED");
        std::string blockedReason;
        assert(runtime.IsMutationBlocked(&blockedReason));
        assert(blockedReason == "RECOVERY_RECONCILE_REQUIRED");
        UnixExecutionServiceClient client(socketPath, 1000);
        const ExecutionCommandResult blocked = client.PreviewOrder(PlaceCommand("new-command"));
        assert(blocked.status == ExecutionCommandStatus::Rejected);
        assert(blocked.reasonCode == "MUTATION_BLOCKED");
        const IbPlaceOrderCommand controlBase = PlaceCommand("control-base");
        const ExecutionControlResult unfinished = client.QueryCommandStatus(
            ControlCommand(controlBase, "query-unfinished", "unfinished-command"));
        assert(unfinished.status == ExecutionCommandStatus::Accepted);
        assert(unfinished.targetStatus == ExecutionCommandStatus::Uncertain);
        assert(unfinished.reasonCode == "RECOVERY_RECONCILE_REQUIRED");
        assert(unfinished.mutationBlocked);
        const ExecutionControlResult reconciled = client.ReconcileAuthoritativeState(
            ControlCommand(controlBase, "reconcile-unfinished"));
        assert(reconciled.status == ExecutionCommandStatus::Accepted);
        assert(!reconciled.mutationBlocked);
        assert(reconciled.affectedCount == 1);
        const ExecutionControlResult resolved = client.QueryCommandStatus(
            ControlCommand(controlBase, "query-resolved", "unfinished-command"));
        assert(resolved.status == ExecutionCommandStatus::Accepted);
        assert(resolved.targetStatus == ExecutionCommandStatus::Rejected);
        assert(resolved.reasonCode == "AUTHORITATIVE_CORRELATION_NOT_FOUND");
    }
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestSimulatorQuoteFeedRefreshesBeyondTtlAndStopsCleanly()
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-execution-quote-feed-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-execution-quote-feed-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    WriteCredential(credentialDirectory,
        "HFC1\nfencing_token=77\ngeneration=9\n");
    ExecutionServiceRuntimeConfig config =
        Config(ActivatedSocket(socketPath), ActivatedSocket(eventSocketPath),
               stateDirectory, credentialDirectory);
    config.simulatorQuoteTtlMs = 80;
    config.simulatorQuoteRefreshIntervalMs = 10;
    ExecutionServiceRuntimeComposition runtime(config);
    std::string reason;
    assert(runtime.Start(reason));
    assert(runtime.IsRunning());

    const std::uint64_t initialNow =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
    const MarketQuoteSnapshot initial =
        runtime.Venue().GetQuoteSnapshot("EUR.USD", initialNow);
    assert(initial.IsFresh(initialNow));

    // Do not poll the Venue or invoke an Agent/read path while the original
    // quote expires. The first observation after the sleep must already show
    // that the daemon-owned background feed refreshed it.
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
    const std::uint64_t refreshedNow =
        static_cast<std::uint64_t>(OmsJournal::NowEpochMs());
    const MarketQuoteSnapshot refreshed =
        runtime.Venue().GetQuoteSnapshot("EUR.USD", refreshedNow);
    assert(refreshedNow > initial.staleAfterMs);
    assert(refreshed.observedAtMs > initial.observedAtMs);
    assert(refreshed.IsFresh(refreshedNow));

    UnixExecutionServiceClient client(socketPath, 1000);
    ExecutionReadCommand quoteRead;
    quoteRead.context = PlaceCommand("quote-feed-authoritative-read").context;
    quoteRead.query = "market.get_quote";
    quoteRead.instrument = "EUR.USD";
    const ExecutionCommandResult read =
        client.ReadAuthoritativeState(quoteRead);
    assert(read.status == ExecutionCommandStatus::Accepted);
    assert(read.detail.find("\"subscription_state\":\"active\"") !=
           std::string::npos);
    assert(read.detail.find("\"stale\":false") != std::string::npos);
    assert(JsonUnsignedField(read.detail, "observed_at_ms") >=
           refreshed.observedAtMs);
    ExecutionReadCommand accountRead;
    accountRead.context = PlaceCommand("simulator-account-read").context;
    accountRead.query = "account.get_summary";
    const ExecutionCommandResult account =
        client.ReadAuthoritativeState(accountRead);
    assert(account.status == ExecutionCommandStatus::Accepted);
    assert(account.detail.find("\"source\":\"SIMULATOR\"") !=
           std::string::npos);
    assert(account.detail.find("\"account_complete\":true") !=
           std::string::npos);
    ExecutionReadCommand riskRead;
    riskRead.context = PlaceCommand("simulator-risk-read").context;
    riskRead.query = "risk.get_limits";
    const ExecutionCommandResult risk =
        client.ReadAuthoritativeState(riskRead);
    assert(risk.status == ExecutionCommandStatus::Accepted);
    assert(risk.detail.find("\"gross_absolute_position\":0") !=
           std::string::npos);
    const ExecutionCommandResult preview =
        client.PreviewOrder(PlaceCommand("quote-feed-authoritative-preview"));
    assert(preview.status == ExecutionCommandStatus::Accepted);
    assert(preview.detail.find("\"authoritative_preview\":{") !=
           std::string::npos);
    assert(JsonUnsignedField(preview.detail, "observed_at_ms") >=
           refreshed.observedAtMs);
    assert(runtime.IsRunning());

    runtime.Stop();
    assert(!runtime.IsRunning());
    const MarketQuoteSnapshot stopped =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    const MarketQuoteSnapshot afterStop =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    assert(afterStop.observedAtMs == stopped.observedAtMs);
    assert(afterStop.staleAfterMs == stopped.staleAfterMs);
    std::cout
        << "{\"schema\":\"hepta.simulator-authoritative-quote-feed-evidence.v1\","
        << "\"owner\":\"execution-service\","
        << "\"feed_source\":\"deterministic-in-process\","
        << "\"ttl_ms\":" << config.simulatorQuoteTtlMs << ','
        << "\"refresh_interval_ms\":"
        << config.simulatorQuoteRefreshIntervalMs << ','
        << "\"initial_observed_at_ms\":" << initial.observedAtMs << ','
        << "\"initial_stale_after_ms\":" << initial.staleAfterMs << ','
        << "\"post_ttl_observed_at_ms\":" << refreshed.observedAtMs << ','
        << "\"post_ttl_stale_after_ms\":" << refreshed.staleAfterMs << ','
        << "\"old_ttl_elapsed\":true,"
        << "\"post_ttl_fresh\":true,"
        << "\"refresh_trigger\":\"execution-owned-periodic\","
        << "\"stop_joined\":true,"
        << "\"post_stop_refresh_count\":0}"
        << std::endl;
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestSimulatorQuoteFeedRollsBackAfterServerStartFailure()
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-execution-quote-rollback-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-execution-quote-rollback-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    WriteCredential(credentialDirectory,
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const int invalidEventFd = ::open("/dev/null", O_RDONLY | O_CLOEXEC);
    assert(invalidEventFd >= 0);
    ExecutionServiceRuntimeConfig config =
        Config(ActivatedSocket(socketPath), invalidEventFd,
               stateDirectory, credentialDirectory);
    config.simulatorQuoteTtlMs = 80;
    config.simulatorQuoteRefreshIntervalMs = 10;
    ExecutionServiceRuntimeComposition runtime(config);
    std::string reason;
    assert(!runtime.Start(reason));
    assert(reason == "EXECUTION_EVENT_ACTIVATED_FD_INVALID");
    assert(!runtime.IsRunning());
    const MarketQuoteSnapshot stopped =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    assert(stopped.observedAtMs != 0);
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    const MarketQuoteSnapshot afterRollback =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    assert(afterRollback.observedAtMs == stopped.observedAtMs);
    assert(afterRollback.staleAfterMs == stopped.staleAfterMs);
    std::cout
        << "{\"schema\":\"hepta.simulator-quote-feed-start-rollback-evidence.v1\","
        << "\"server_start_failed\":true,"
        << "\"feed_joined\":true,"
        << "\"post_failure_refresh_count\":0}"
        << std::endl;
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestSimulatorQuoteFeedRollsBackAfterCommandServerStartFailure()
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-execution-command-rollback-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-execution-command-rollback-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    WriteCredential(credentialDirectory,
        "HFC1\nfencing_token=77\ngeneration=9\n");
    const int invalidCommandFd = ::open("/dev/null", O_RDONLY | O_CLOEXEC);
    assert(invalidCommandFd >= 0);
    ExecutionServiceRuntimeConfig config =
        Config(invalidCommandFd, ActivatedSocket(eventSocketPath),
               stateDirectory, credentialDirectory);
    config.simulatorQuoteTtlMs = 80;
    config.simulatorQuoteRefreshIntervalMs = 10;
    ExecutionServiceRuntimeComposition runtime(config);
    std::string reason;
    assert(!runtime.Start(reason));
    assert(reason == "EXECUTION_ACTIVATED_FD_NOT_LISTENING_UNIX_STREAM");
    assert(!runtime.IsRunning());
    const MarketQuoteSnapshot stopped =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    assert(stopped.observedAtMs != 0);
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
    const MarketQuoteSnapshot afterRollback =
        runtime.Venue().GetQuoteSnapshot(
            "EUR.USD",
            static_cast<std::uint64_t>(OmsJournal::NowEpochMs()));
    assert(afterRollback.observedAtMs == stopped.observedAtMs);
    assert(afterRollback.staleAfterMs == stopped.staleAfterMs);
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void AssertInvalidCredentialContents(const std::string& contents)
{
    const std::string stateDirectory = TempDirectory("/tmp/hepta-execution-invalid-XXXXXX");
    const std::string credentialDirectory = TempDirectory("/tmp/hepta-execution-invalid-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    WriteCredential(credentialDirectory, contents);
    const int listenFd = ActivatedSocket(socketPath);
    const int eventListenFd = ActivatedSocket(eventSocketPath);
    {
        ExecutionServiceRuntimeComposition runtime(
            Config(listenFd, eventListenFd, stateDirectory, credentialDirectory));
        std::string reason;
        assert(!runtime.Start(reason));
        assert(reason == "EXECUTION_FENCE_CREDENTIAL_INVALID");
        errno = 0;
        assert(::fcntl(listenFd, F_GETFD) == -1);
        assert(errno == EBADF);
        errno = 0;
        assert(::fcntl(eventListenFd, F_GETFD) == -1);
        assert(errno == EBADF);
    }
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestInvalidCredentialClosesOwnedActivatedFd()
{
    AssertInvalidCredentialContents("not-a-fence-credential\n");
    AssertInvalidCredentialContents(
        "HFC1\nfencing_token=+77\ngeneration=9\n");
    AssertInvalidCredentialContents(
        "HFC1\nfencing_token= 77\ngeneration=9\n");
    std::string embeddedNull = "HFC1\nfencing_token=77";
    embeddedNull.push_back('\0');
    embeddedNull.append("suffix\ngeneration=9\n");
    AssertInvalidCredentialContents(embeddedNull);
}

void AssertUnsafeCredentialMetadataRejected(mode_t mode, bool addHardLink)
{
    const std::string stateDirectory =
        TempDirectory("/tmp/hepta-execution-unsafe-state-XXXXXX");
    const std::string credentialDirectory =
        TempDirectory("/tmp/hepta-execution-unsafe-credentials-XXXXXX");
    const std::string socketPath = TempSocketPath();
    const std::string eventSocketPath = TempSocketPath();
    const std::string credentialPath =
        credentialDirectory + "/hepta-execution-fence";
    const std::string hardLinkPath = credentialDirectory + "/fence-hard-link";
    WriteCredential(credentialDirectory,
        "HFC1\nfencing_token=77\ngeneration=9\n");
    assert(::chmod(credentialPath.c_str(), mode) == 0);
    if (addHardLink)
        assert(::link(credentialPath.c_str(), hardLinkPath.c_str()) == 0);

    const int listenFd = ActivatedSocket(socketPath);
    const int eventListenFd = ActivatedSocket(eventSocketPath);
    {
        ExecutionServiceRuntimeComposition runtime(
            Config(listenFd, eventListenFd, stateDirectory, credentialDirectory));
        std::string reason;
        assert(!runtime.Start(reason));
        assert(reason == "EXECUTION_FENCE_CREDENTIAL_UNSAFE");
        errno = 0;
        assert(::fcntl(listenFd, F_GETFD) == -1 && errno == EBADF);
        errno = 0;
        assert(::fcntl(eventListenFd, F_GETFD) == -1 && errno == EBADF);
    }
    if (addHardLink) assert(::unlink(hardLinkPath.c_str()) == 0);
    Cleanup(socketPath, eventSocketPath, stateDirectory, credentialDirectory);
}

void TestUnsafeCredentialMetadataClosesOwnedActivatedFd()
{
    AssertUnsafeCredentialMetadataRejected(0600, false);
    AssertUnsafeCredentialMetadataRejected(0400, true);
}
}

int main()
{
    TestActivatedRuntimeAndPrivateState();
    TestDegradedRecoveryStillServesFailClosed();
    TestSimulatorQuoteFeedRefreshesBeyondTtlAndStopsCleanly();
    TestSimulatorQuoteFeedRollsBackAfterServerStartFailure();
    TestSimulatorQuoteFeedRollsBackAfterCommandServerStartFailure();
    std::cout
        << "execution_service_runtime_composition_evidence:"
        << " quote_feed=execution_owned_periodic"
        << " old_ttl_elapsed=verified"
        << " post_ttl_authoritative_read=verified"
        << " post_ttl_authoritative_preview=verified"
        << " stop_join=verified"
        << " start_failure_rollback=verified"
        << std::endl;
    TestInvalidCredentialClosesOwnedActivatedFd();
    TestUnsafeCredentialMetadataClosesOwnedActivatedFd();
    std::cout << "execution_service_runtime_composition_tests: PASS" << std::endl;
    return 0;
}
