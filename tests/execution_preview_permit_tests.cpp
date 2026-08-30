#include "../HeptaTrade/execution/unix_execution_service_server.h"
#include "../HeptaTrade/execution/execution_decision_lease_authority.h"
#include "../HeptaTrade/execution/execution_service_protocol.h"

#include <chrono>
#include <cstdint>
#include <cmath>
#include <cstdlib>
#include <atomic>
#include <iostream>
#include <locale>
#include <mutex>
#include <string>
#include <thread>

namespace
{
void Require(bool condition, const char* expression, int line)
{
    if (condition) return;
    std::cerr << "requirement failed at line " << line << ": "
    << expression << '\n';
    std::abort();
}
#define REQUIRE(expression) Require(static_cast<bool>(expression), #expression, __LINE__)

std::int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

bool SamePlacePayload(const PlaceOrderCommand& left,
            const PlaceOrderCommand& right)
{
    return left.context.agentId == right.context.agentId &&
        left.context.sessionId == right.context.sessionId &&
        left.context.toolCallId == right.context.toolCallId &&
        left.context.account == right.context.account &&
        left.context.venue == right.context.venue &&
        left.context.executionDomain == right.context.executionDomain &&
        left.instrument == right.instrument &&
        left.contract.symbol == right.contract.symbol &&
        left.contract.currency == right.contract.currency &&
        left.contract.secType == right.contract.secType &&
        left.order.action == right.order.action &&
        left.order.orderType == right.order.orderType &&
        left.order.totalQuantity == right.order.totalQuantity &&
        left.order.lmtPrice == right.order.lmtPrice &&
        left.timeInForce == right.timeInForce &&
        left.referencePrice == right.referencePrice &&
        left.expiresAtMs == right.expiresAtMs;
}

class FakeAuthority : public ExecutionAuthority,
            public ExecutionReadAuthority
{
public:
    ExecutionCommandResult PlaceOrder(const PlaceOrderCommand& command) override
    {
        ++placeCalls;
        lastPlace = command;
        ExecutionCommandResult result;
        result.status = rejectPlace ? ExecutionCommandStatus::Rejected :
            ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = rejectPlace ? -1 : 42;
        if (rejectPlace) result.reasonCode = "EXECUTION_DECISION_LEASE_BUSY";
        return result;
    }

    ExecutionCommandResult CancelOrder(const CancelOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = command.orderId;
        return result;
    }

    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ++flattenCalls;
        lastFlatten = command;
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 43;
        return result;
    }

    ExecutionCommandResult PreviewOrder(
        const PlaceOrderCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        return result;
    }

    ExecutionCommandResult PreviewFlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.hasAuthoritativeFlattenSnapshot = true;
        result.authoritativeFlattenPositionQuantity = -250.0;
        result.authoritativeFlattenConnectionEpoch = 7;
        result.authoritativeFlattenPositionGeneration = 11;
        result.authoritativeFlattenPlanBinding =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        return result;
    }

    bool IsDurablePlaceReplay(
        const PlaceOrderCommand& command) const override
    {
        return durablePlaceReplay && SamePlacePayload(command, durablePlace);
    }

    bool IsDurableFlattenReplay(
        const FlattenPositionCommand& command) const override
    {
        return durableFlattenReplay &&
  command.context.agentId == durableFlatten.context.agentId &&
  command.context.sessionId == durableFlatten.context.sessionId &&
  command.context.toolCallId == durableFlatten.context.toolCallId &&
  command.instrument == durableFlatten.instrument;
    }

    ExecutionCommandResult ReadAuthoritativeState(
        const ExecutionReadCommand& command) override
    {
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        return result;
    }

    int placeCalls = 0;
    int flattenCalls = 0;
    bool durablePlaceReplay = false;
    bool durableFlattenReplay = false;
    bool rejectPlace = false;
    PlaceOrderCommand durablePlace;
    FlattenPositionCommand durableFlatten;
    PlaceOrderCommand lastPlace;
    FlattenPositionCommand lastFlatten;
};

class BlockingAuthority : public FakeAuthority
{
public:
    BlockingAuthority(std::atomic<bool>& entered,
                      std::atomic<bool>& release)
        : m_entered(entered), m_release(release)
    {
    }

    ExecutionCommandResult PlaceOrder(
        const PlaceOrderCommand& command) override
    {
        ++placeCalls;
        lastPlace = command;
        m_entered.store(true);
        while (!m_release.load()) std::this_thread::yield();
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 42;
        return result;
    }

    ExecutionCommandResult FlattenPosition(
        const FlattenPositionCommand& command) override
    {
        ++flattenCalls;
        lastFlatten = command;
        m_entered.store(true);
        while (!m_release.load()) std::this_thread::yield();
        ExecutionCommandResult result;
        result.status = ExecutionCommandStatus::Accepted;
        result.commandId = command.context.toolCallId;
        result.orderId = 43;
        return result;
    }

private:
    std::atomic<bool>& m_entered;
    std::atomic<bool>& m_release;
};

PlaceOrderCommand Place(const std::string& callId)
{
    PlaceOrderCommand command;
    command.context.agentId = "permit-agent";
    command.context.sessionId = "permit-session";
    command.context.toolCallId = callId;
    command.context.account = "SIM";
    command.context.venue = "SIMULATOR";
    command.context.executionDomain = "SIM:permit-agent";
    command.contract.symbol = "EUR";
    command.contract.currency = "USD";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 1000.0;
    command.order.lmtPrice = 1.1002;
    command.instrument = "EUR.USD";
    command.timeInForce = "DAY";
    command.referencePrice = 1.1002;
    command.expiresAtMs = NowMs() + 60000;
    return command;
}

FlattenPositionCommand Flatten(const std::string& callId)
{
    FlattenPositionCommand command;
    command.context.agentId = "permit-agent";
    command.context.sessionId = "permit-session";
    command.context.toolCallId = callId;
    command.context.account = "SIM";
    command.context.venue = "SIMULATOR";
    command.context.executionDomain = "SIM:permit-agent";
    command.contract.symbol = "EUR";
    command.contract.currency = "USD";
    command.contract.secType = "CASH";
    command.contract.exchange = "IDEALPRO";
    command.instrument = "EUR.USD";
    return command;
}

class CommaDecimalFacet final : public std::numpunct<char>
{
protected:
    char do_decimal_point() const override { return ','; }
};
}

class PreviewPermitTestAccess
{
public:
    static bool IssuePlace(UnixExecutionServiceServer& server,
                 const PlaceOrderCommand& command,
                 std::string& permit,
                 std::string& mutationCommandId,
                 long long& expiresAtMs,
                 std::string& reason)
    {
        return server.IssuePreviewPermit(
  command, permit, mutationCommandId, expiresAtMs, reason);
    }

    static bool ConsumePlace(UnixExecutionServiceServer& server,
                   const PlaceOrderCommand& command,
                   std::string& reason)
    {
        return server.ConsumePreviewPermit(command, reason);
    }

    static bool IssueFlatten(UnixExecutionServiceServer& server,
                   const FlattenPositionCommand& command,
                   const ExecutionCommandResult& preview,
                   std::string& permit,
                   std::string& mutationCommandId,
                   long long& expiresAtMs,
                   std::string& reason)
    {
        return server.IssueFlattenPreviewPermit(
  command, preview, permit, mutationCommandId, expiresAtMs, reason);
    }

    static bool ConsumeFlatten(UnixExecutionServiceServer& server,
                     FlattenPositionCommand& command,
                     std::string& reason)
    {
        return server.ConsumeFlattenPreviewPermit(command, reason);
    }

    static void Expire(UnixExecutionServiceServer& server,
             const std::string& permit)
    {
        std::lock_guard<std::mutex> lock(server.m_previewMutex);
        std::unordered_map<std::string,
  UnixExecutionServiceServer::PreviewPermitRecord>::iterator found =
      server.m_previewPermits.find(permit);
        REQUIRE(found != server.m_previewPermits.end());
        found->second.steadyExpiresAt =
  std::chrono::steady_clock::now() - std::chrono::milliseconds(1);
    }

    static std::size_t Size(UnixExecutionServiceServer& server)
    {
        std::lock_guard<std::mutex> lock(server.m_previewMutex);
        return server.m_previewPermits.size();
    }

    static void RevokeOwner(UnixExecutionServiceServer& server,
                  const std::string& agentId,
                  const std::string& sessionId)
    {
        server.RevokePreviewPermitsForOwner(agentId, sessionId);
    }

    static ExecutionCommandResult DispatchPlace(
        UnixExecutionServiceServer& server,
        const PlaceOrderCommand& command)
    {
        return server.DispatchPlaceOrder(command);
    }

    static ExecutionCommandResult DispatchFlatten(
        UnixExecutionServiceServer& server,
        const FlattenPositionCommand& command)
    {
        return server.DispatchFlattenPosition(command);
    }

    static void ValidateResponse(
        UnixExecutionServiceServer& server,
        const ExecutionServiceRequest& request,
        ExecutionCommandResult& result,
        ExecutionControlResult& controlResult,
        bool controlResponse)
    {
        server.ValidateAndBindResponse(
            request, result, controlResult, controlResponse);
    }

    static void SetIdentity(UnixExecutionServiceServer& server,
                            const std::string& epoch,
                            std::uint64_t fencingGeneration)
    {
        server.m_serviceIdentity.serviceEpoch = epoch;
        server.m_serviceIdentity.serviceFencingGeneration = fencingGeneration;
    }
};

namespace
{
void TestPlacePermitSingleUseAndAtomicMismatch()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);

    PlaceOrderCommand preview = Place("preview-one");
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    REQUIRE(!permit.empty());
    REQUIRE(!mutationId.empty());
    REQUIRE(expiry > NowMs());

    PlaceOrderCommand mutation = preview;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;
    REQUIRE(PreviewPermitTestAccess::ConsumePlace(server, mutation, reason));
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(server, mutation, reason));
    REQUIRE(!reason.empty());

    preview = Place("preview-mismatch");
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PlaceOrderCommand wrong = preview;
    wrong.context.toolCallId = mutationId + "-wrong";
    wrong.previewPermit = permit;
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(server, wrong, reason));
    PlaceOrderCommand formerlyCorrect = preview;
    formerlyCorrect.context.toolCallId = mutationId;
    formerlyCorrect.previewPermit = permit;
    // A command-id validation failure must not consume the permit.  The exact
    // mutation can therefore be retried successfully.
    REQUIRE(PreviewPermitTestAccess::ConsumePlace(
        server, formerlyCorrect, reason));
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(
        server, formerlyCorrect, reason));
}

void TestPlacePermitBindsPayloadOwnerExpiryAndRevocation()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;

    PlaceOrderCommand preview = Place("preview-payload");
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PlaceOrderCommand changed = preview;
    changed.context.toolCallId = mutationId;
    changed.previewPermit = permit;
    changed.order.totalQuantity += 1.0;
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(server, changed, reason));
    // Payload mismatch is likewise non-consuming; the original normalized
    // command remains the only command authorized by this permit.
    PlaceOrderCommand original = preview;
    original.context.toolCallId = mutationId;
    original.previewPermit = permit;
    REQUIRE(PreviewPermitTestAccess::ConsumePlace(server, original, reason));

    preview = Place("preview-expired");
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PreviewPermitTestAccess::Expire(server, permit);
    PlaceOrderCommand expired = preview;
    expired.context.toolCallId = mutationId;
    expired.previewPermit = permit;
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(server, expired, reason));

    preview = Place("preview-revoke");
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    REQUIRE(PreviewPermitTestAccess::Size(server) == 1);
    PreviewPermitTestAccess::RevokeOwner(
        server, preview.context.agentId, preview.context.sessionId);
    REQUIRE(PreviewPermitTestAccess::Size(server) == 0);
    PlaceOrderCommand revoked = preview;
    revoked.context.toolCallId = mutationId;
    revoked.previewPermit = permit;
    REQUIRE(!PreviewPermitTestAccess::ConsumePlace(server, revoked, reason));
}

void TestFlattenPermitInjectsExactAuthoritativeGeneration()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    FlattenPositionCommand previewCommand = Flatten("flatten-preview");
    const ExecutionCommandResult preview =
        authority.PreviewFlattenPosition(previewCommand);
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssueFlatten(
        server, previewCommand, preview, permit, mutationId, expiry, reason));

    FlattenPositionCommand wrong = previewCommand;
    wrong.context.toolCallId = mutationId;
    wrong.instrument = "GBP.USD";
    wrong.previewPermit = permit;
    REQUIRE(!PreviewPermitTestAccess::ConsumeFlatten(server, wrong, reason));

    FlattenPositionCommand mutation = previewCommand;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;
    REQUIRE(PreviewPermitTestAccess::ConsumeFlatten(server, mutation, reason));
    REQUIRE(mutation.previewPermit.empty());
    REQUIRE(mutation.hasAuthoritativePreviewSnapshot);
    REQUIRE(mutation.previewPositionQuantity == -250.0);
    REQUIRE(mutation.previewPositionConnectionEpoch == 7);
    REQUIRE(mutation.previewPositionGeneration == 11);
    REQUIRE(mutation.authoritativePreviewPlanBinding ==
        preview.authoritativeFlattenPlanBinding);
    REQUIRE(!PreviewPermitTestAccess::ConsumeFlatten(server, mutation, reason));
}

void TestFlattenFingerprintsCanonicalizeZeroAndLocale()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    FlattenPositionCommand previewCommand = Flatten("flatten-zero");
    previewCommand.contract.strike = -0.0;
    const ExecutionCommandResult preview =
        authority.PreviewFlattenPosition(previewCommand);
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssueFlatten(
        server, previewCommand, preview, permit, mutationId, expiry, reason));
    FlattenPositionCommand mutation = previewCommand;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;
    mutation.contract.strike = 0.0;
    REQUIRE(PreviewPermitTestAccess::ConsumeFlatten(server, mutation, reason));

    FlattenPositionCommand localeCommand = Flatten("flatten-locale");
    localeCommand.contract.strike = 1234.5;
    const std::locale prior = std::locale();
    std::locale::global(std::locale(std::locale::classic(),
                                    new CommaDecimalFacet()));
    REQUIRE(PreviewPermitTestAccess::IssueFlatten(
        server, localeCommand, preview, permit, mutationId, expiry, reason));
    std::locale::global(prior);
    mutation = localeCommand;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;
    REQUIRE(PreviewPermitTestAccess::ConsumeFlatten(server, mutation, reason));

    FlattenPositionCommand durable = Flatten("flatten-dispatch-zero");
    durable.contract.strike = -0.0;
    durable.previewPermit.clear();
    authority.durableFlatten = durable;
    authority.durableFlattenReplay = true;
    REQUIRE(PreviewPermitTestAccess::DispatchFlatten(server, durable).status ==
        ExecutionCommandStatus::Accepted);
    durable.contract.strike = 0.0;
    const ExecutionCommandResult replay =
        PreviewPermitTestAccess::DispatchFlatten(server, durable);
    REQUIRE(replay.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(authority.flattenCalls == 1);
}

void TestDurableReplayBypassesPermitOnlyForExactPayload()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    PlaceOrderCommand durable = Place("durable-place-command");
    durable.previewPermit.clear();
    authority.durablePlace = durable;
    authority.durablePlaceReplay = true;

    ExecutionCommandResult result =
        PreviewPermitTestAccess::DispatchPlace(server, durable);
    REQUIRE(result.status == ExecutionCommandStatus::Accepted);
    REQUIRE(authority.placeCalls == 1);

    PlaceOrderCommand changed = durable;
    changed.order.totalQuantity += 5.0;
    result = PreviewPermitTestAccess::DispatchPlace(server, changed);
    REQUIRE(result.status == ExecutionCommandStatus::Rejected);
    REQUIRE(authority.placeCalls == 1);
    REQUIRE(!result.reasonCode.empty());

    FlattenPositionCommand durableFlatten = Flatten("durable-flatten-command");
    durableFlatten.previewPermit.clear();
    authority.durableFlatten = durableFlatten;
    authority.durableFlattenReplay = true;
    result = PreviewPermitTestAccess::DispatchFlatten(server, durableFlatten);
    REQUIRE(result.status == ExecutionCommandStatus::Accepted);
    REQUIRE(authority.flattenCalls == 1);

    FlattenPositionCommand changedFlatten = durableFlatten;
    changedFlatten.instrument = "GBP.USD";
    result = PreviewPermitTestAccess::DispatchFlatten(server, changedFlatten);
    REQUIRE(result.status == ExecutionCommandStatus::Rejected);
    REQUIRE(authority.flattenCalls == 1);
}

void TestLeaseFailureLeavesPermitRetryable()
{
    FakeAuthority authority;
    const std::shared_ptr<ExecutionDecisionLeaseAuthority> leases(
        new ExecutionDecisionLeaseAuthority());
    UnixExecutionServiceServer server(authority, nullptr, leases);

    // Occupy the instrument lease with a different owner.  The permit below
    // is valid, but dispatch must fail before consuming it while the lease is
    // unavailable.
    PlaceOrderCommand blocker = Place("lease-blocker");
    blocker.context.agentId = "other-agent";
    blocker.context.sessionId = "other-session";
    std::string reason;
    REQUIRE(leases->Authorize(
        blocker.context, blocker.instrument, reason));

    PlaceOrderCommand preview = Place("lease-preview");
    std::string permit;
    std::string mutationId;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PlaceOrderCommand mutation = preview;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;
    ExecutionCommandResult result =
        PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(result.status == ExecutionCommandStatus::Rejected);
    REQUIRE(result.reasonCode == "EXECUTION_DECISION_LEASE_BUSY");
    REQUIRE(authority.placeCalls == 0);

    leases->FenceOwner(
        blocker.context.agentId, blocker.context.sessionId);
    result = PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(result.status == ExecutionCommandStatus::Accepted);
    REQUIRE(authority.placeCalls == 1);
}

void TestConcurrentPlaceRetryReturnsInFlightThenReplay()
{
    std::atomic<bool> entered(false);
    std::atomic<bool> release(false);
    BlockingAuthority authority(entered, release);
    UnixExecutionServiceServer server(authority);

    PlaceOrderCommand preview = Place("concurrent-place-preview");
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PlaceOrderCommand mutation = preview;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;

    ExecutionCommandResult firstResult;
    std::thread first([&]() {
        firstResult = PreviewPermitTestAccess::DispatchPlace(
            server, mutation);
    });
    while (!entered.load()) std::this_thread::yield();

    // The first caller has consumed the one-time permit but has not yet
    // returned an authority result. An exact concurrent retry must receive a
    // typed uncertain/in-flight response, never permit-unknown.
    const ExecutionCommandResult concurrent =
        PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(concurrent.status == ExecutionCommandStatus::Uncertain);
    REQUIRE(concurrent.reasonCode == "EXECUTION_COMMAND_IN_FLIGHT");
    REQUIRE(authority.placeCalls == 1);

    release.store(true);
    first.join();
    REQUIRE(firstResult.status == ExecutionCommandStatus::Accepted);

    const ExecutionCommandResult replay =
        PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(replay.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(replay.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(authority.placeCalls == 1);

    PlaceOrderCommand changed = mutation;
    changed.order.totalQuantity += 1.0;
    const ExecutionCommandResult conflict =
        PreviewPermitTestAccess::DispatchPlace(server, changed);
    REQUIRE(conflict.status == ExecutionCommandStatus::Rejected);
    REQUIRE(conflict.reasonCode == "IDEMPOTENCY_KEY_CONFLICT");
    REQUIRE(authority.placeCalls == 1);
}

void TestConcurrentFlattenRetryReturnsInFlightThenReplay()
{
    std::atomic<bool> entered(false);
    std::atomic<bool> release(false);
    BlockingAuthority authority(entered, release);
    UnixExecutionServiceServer server(authority);

    FlattenPositionCommand preview = Flatten("concurrent-flatten-preview");
    const ExecutionCommandResult flattenPreview =
        authority.PreviewFlattenPosition(preview);
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssueFlatten(
        server, preview, flattenPreview, permit, mutationId, expiry, reason));
    FlattenPositionCommand mutation = preview;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;

    ExecutionCommandResult firstResult;
    std::thread first([&]() {
        firstResult = PreviewPermitTestAccess::DispatchFlatten(
            server, mutation);
    });
    while (!entered.load()) std::this_thread::yield();

    const ExecutionCommandResult concurrent =
        PreviewPermitTestAccess::DispatchFlatten(server, mutation);
    REQUIRE(concurrent.status == ExecutionCommandStatus::Uncertain);
    REQUIRE(concurrent.reasonCode == "EXECUTION_COMMAND_IN_FLIGHT");
    REQUIRE(authority.flattenCalls == 1);

    release.store(true);
    first.join();
    REQUIRE(firstResult.status == ExecutionCommandStatus::Accepted);
    const ExecutionCommandResult replay =
        PreviewPermitTestAccess::DispatchFlatten(server, mutation);
    REQUIRE(replay.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(replay.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(authority.flattenCalls == 1);
}

void TestConcurrentDurableReplayIsClaimedBeforeAuthority()
{
    std::atomic<bool> entered(false);
    std::atomic<bool> release(false);
    BlockingAuthority authority(entered, release);
    UnixExecutionServiceServer server(authority);

    PlaceOrderCommand durable = Place("concurrent-durable-place");
    durable.previewPermit.clear();
    authority.durablePlace = durable;
    authority.durablePlaceReplay = true;

    ExecutionCommandResult firstResult;
    std::thread first([&]() {
        firstResult = PreviewPermitTestAccess::DispatchPlace(
            server, durable);
    });
    while (!entered.load()) std::this_thread::yield();
    const ExecutionCommandResult concurrent =
        PreviewPermitTestAccess::DispatchPlace(server, durable);
    REQUIRE(concurrent.status == ExecutionCommandStatus::Uncertain);
    REQUIRE(concurrent.reasonCode == "EXECUTION_COMMAND_IN_FLIGHT");
    REQUIRE(authority.placeCalls == 1);
    release.store(true);
    first.join();
    REQUIRE(firstResult.status == ExecutionCommandStatus::Accepted);
    const ExecutionCommandResult replay =
        PreviewPermitTestAccess::DispatchPlace(server, durable);
    REQUIRE(replay.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(authority.placeCalls == 1);

    // Repeat the same assertion for flatten's durable hook. A fresh server is
    // used so the synchronization flags are not shared with the place call.
    std::atomic<bool> flattenEntered(false);
    std::atomic<bool> flattenRelease(false);
    BlockingAuthority flattenAuthority(flattenEntered, flattenRelease);
    UnixExecutionServiceServer flattenServer(flattenAuthority);
    FlattenPositionCommand durableFlatten = Flatten(
        "concurrent-durable-flatten");
    durableFlatten.previewPermit.clear();
    flattenAuthority.durableFlatten = durableFlatten;
    flattenAuthority.durableFlattenReplay = true;

    ExecutionCommandResult firstFlattenResult;
    std::thread firstFlatten([&]() {
        firstFlattenResult = PreviewPermitTestAccess::DispatchFlatten(
            flattenServer, durableFlatten);
    });
    while (!flattenEntered.load()) std::this_thread::yield();
    const ExecutionCommandResult concurrentFlatten =
        PreviewPermitTestAccess::DispatchFlatten(
            flattenServer, durableFlatten);
    REQUIRE(concurrentFlatten.status == ExecutionCommandStatus::Uncertain);
    REQUIRE(concurrentFlatten.reasonCode == "EXECUTION_COMMAND_IN_FLIGHT");
    REQUIRE(flattenAuthority.flattenCalls == 1);
    flattenRelease.store(true);
    firstFlatten.join();
    REQUIRE(firstFlattenResult.status == ExecutionCommandStatus::Accepted);
    const ExecutionCommandResult flattenReplay =
        PreviewPermitTestAccess::DispatchFlatten(
            flattenServer, durableFlatten);
    REQUIRE(flattenReplay.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(flattenAuthority.flattenCalls == 1);
}

void TestAuthorityRejectedAfterConsumeIsDeterministicallyReplayed()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    PlaceOrderCommand preview = Place("authority-reject-after-consume");
    std::string permit;
    std::string mutationId;
    std::string reason;
    long long expiry = 0;
    REQUIRE(PreviewPermitTestAccess::IssuePlace(
        server, preview, permit, mutationId, expiry, reason));
    PlaceOrderCommand mutation = preview;
    mutation.context.toolCallId = mutationId;
    mutation.previewPermit = permit;

    // The lease gate has passed, so this rejection is returned by the
    // authority after the one-time permit transition. It must not be retried
    // as a fresh venue command when the same command id is presented again.
    authority.rejectPlace = true;
    const ExecutionCommandResult first =
        PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(first.status == ExecutionCommandStatus::Rejected);
    REQUIRE(first.reasonCode == "EXECUTION_DECISION_LEASE_BUSY");
    REQUIRE(authority.placeCalls == 1);

    authority.rejectPlace = false;
    const ExecutionCommandResult replay =
        PreviewPermitTestAccess::DispatchPlace(server, mutation);
    REQUIRE(replay.status == ExecutionCommandStatus::Rejected);
    REQUIRE(replay.reasonCode == "EXECUTION_DECISION_LEASE_BUSY");
    REQUIRE(authority.placeCalls == 1);
}

void TestWireResponseSanitizesExceptionDetails()
{
    FakeAuthority authority;
    UnixExecutionServiceServer server(authority);
    // Give the response codec a valid service identity so this test exercises
    // the complete post-dispatch admission/encode path.
    PreviewPermitTestAccess::SetIdentity(
        server, "hexec-v6-wire-test", 1);

    ExecutionServiceRequest request;
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.place.context.toolCallId = "wire-place";
    ExecutionCommandResult result;
    result.status = ExecutionCommandStatus::Uncertain;
    result.commandId = "wire-place";
    result.reasonCode = "IB_PLACE_OUTCOME_UNCERTAIN";
    result.detail = "/private/venue/socket credential=secret";
    ExecutionControlResult controlResult;
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.reasonCode == "IB_PLACE_OUTCOME_UNCERTAIN");
    REQUIRE(result.detail == "execution authority outcome is uncertain");
    REQUIRE(result.detail.find("secret") == std::string::npos);
    std::string body;
    std::string reason;
    REQUIRE(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
    REQUIRE(body.find("secret") == std::string::npos);

    result.reasonCode.clear();
    result.detail = "another /private/path credential=secret";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.reasonCode == "EXECUTION_AUTHORITY_EXCEPTION");
    REQUIRE(result.detail == "execution authority outcome is uncertain");

    // A malformed/raw exception string in a control result must not survive
    // either reason-code or detail serialization.
    request.operation = ExecutionServiceOperation::QueryCommandStatus;
    request.control.context.toolCallId = "wire-control";
    request.control.targetCommandId = "target-command";
    controlResult = ExecutionControlResult();
    controlResult.status = ExecutionCommandStatus::Rejected;
    controlResult.commandId = "wire-control";
    controlResult.targetCommandId = "target-command";
    controlResult.reasonCode = "/private/control credential=secret";
    controlResult.detail = "adapter exception: /private/control credential=secret";
    result = ExecutionCommandResult();
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, true);
    REQUIRE(controlResult.reasonCode == "EXECUTION_CONTROL_REJECTED");
    REQUIRE(controlResult.detail == "execution control request was rejected");
    REQUIRE(controlResult.detail.find("secret") == std::string::npos);
    REQUIRE(ExecutionServiceProtocol::EncodeControlResponse(
        controlResult, body, reason));
    REQUIRE(body.find("secret") == std::string::npos);

    // Accepted and Duplicate are also authority-controlled outcomes.  A
    // mutation may retain a short ordinary detail, but exception/path/
    // credential text must not cross the final Unix response boundary.
    request.operation = ExecutionServiceOperation::PlaceIbOrder;
    request.place.context.toolCallId = "wire-accepted";
    result = ExecutionCommandResult();
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = "wire-accepted";
    result.reasonCode = "IB_PLACE_ACCEPTED";
    result.detail = "adapter exception at /private/venue credential=secret";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.status == ExecutionCommandStatus::Accepted);
    REQUIRE(result.detail.empty());
    REQUIRE(result.detail.find("secret") == std::string::npos);
    REQUIRE(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
    REQUIRE(body.find("secret") == std::string::npos);

    // A bounded ordinary detail may contain a non-path slash (for example a
    // ratio or market symbol).  Keep that compatibility while the explicit
    // absolute/path forms above remain fail-closed.
    result.detail = "fill ratio 1/2";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.status == ExecutionCommandStatus::Accepted);
    REQUIRE(result.detail == "fill ratio 1/2");

    result.status = ExecutionCommandStatus::Duplicate;
    result.commandId = "wire-accepted";
    result.reasonCode = "DUPLICATE_TOOL_CALL";
    result.detail = "replayed from /private/journal credential=secret";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(result.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(result.detail == "duplicate tool call");
    REQUIRE(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
    REQUIRE(body.find("secret") == std::string::npos);

    // Duplicate is a replay outcome, so it receives a stable reason even if
    // an embedded authority omitted one.
    result.reasonCode.clear();
    result.detail = "replayed safely";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.status == ExecutionCommandStatus::Duplicate);
    REQUIRE(result.reasonCode == "DUPLICATE_TOOL_CALL");
    REQUIRE(result.detail == "replayed safely");

    // Read-only preview details are structured JSON.  An unsafe accepted
    // payload is fail-closed as a typed rejection rather than being emitted
    // as partially trusted preview metadata.
    request.operation = ExecutionServiceOperation::PreviewOrder;
    request.place.context.toolCallId = "wire-preview";
    result = ExecutionCommandResult();
    result.status = ExecutionCommandStatus::Accepted;
    result.commandId = "wire-preview";
    result.reasonCode = "EXECUTION_PREVIEW_READY";
    result.detail = "{\"authoritative_preview\":\"/private/venue credential=secret\"}";
    PreviewPermitTestAccess::ValidateResponse(
        server, request, result, controlResult, false);
    REQUIRE(result.status == ExecutionCommandStatus::Rejected);
    REQUIRE(result.reasonCode == "EXECUTION_PREVIEW_RESPONSE_INVALID");
    REQUIRE(result.detail == "execution authority response was invalid");
    REQUIRE(result.detail.find("secret") == std::string::npos);
    REQUIRE(ExecutionServiceProtocol::EncodeResponse(result, body, reason));
    REQUIRE(body.find("secret") == std::string::npos);
}
}

int main()
{
    TestPlacePermitSingleUseAndAtomicMismatch();
    TestPlacePermitBindsPayloadOwnerExpiryAndRevocation();
    TestFlattenPermitInjectsExactAuthoritativeGeneration();
    TestFlattenFingerprintsCanonicalizeZeroAndLocale();
    TestDurableReplayBypassesPermitOnlyForExactPayload();
    TestLeaseFailureLeavesPermitRetryable();
    TestConcurrentPlaceRetryReturnsInFlightThenReplay();
    TestConcurrentFlattenRetryReturnsInFlightThenReplay();
    TestConcurrentDurableReplayIsClaimedBeforeAuthority();
    TestAuthorityRejectedAfterConsumeIsDeterministicallyReplayed();
    TestWireResponseSanitizesExceptionDetails();
    return 0;
}
