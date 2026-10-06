#include "execution_coordinator_test_support.h"

#include <sys/syscall.h>

// One exact-inode sync failure in this test executable only. No production
// fault injection or alternate journal provider is introduced.
namespace CoordinatorSyncFault {
std::atomic<bool> armed{false};
dev_t device = 0;
ino_t inode = 0;
}
extern "C" int fdatasync(int fd) {
    if (CoordinatorSyncFault::armed.load()) {
        struct stat value;
        if (::fstat(fd, &value) == 0 && value.st_dev == CoordinatorSyncFault::device &&
            value.st_ino == CoordinatorSyncFault::inode && CoordinatorSyncFault::armed.exchange(false)) {
            errno = EIO;
            return -1;
        }
    }
    return ::syscall(SYS_fdatasync, fd);
}

namespace hepta_execution_coordinator_test {

std::string TempJournalPath()
{
    const char* root = std::getenv("TMPDIR");
    const std::string pattern = std::string(root != nullptr && root[0] == '/' ? root : "/tmp") +
        "/hepta-execution-coordinator-XXXXXX";
    std::vector<char> path(pattern.begin(), pattern.end()); path.push_back(0);
    const int fd = mkstemp(path.data());
    assert(fd >= 0);
    close(fd);
    return std::string(path.data());
}

IbPlaceOrderCommand MakePlace(const std::string& callId, const std::string& agentId)
{
    IbPlaceOrderCommand command;
    command.context.agentId = agentId;
    command.context.sessionId = "session-1";
    command.context.toolCallId = callId;
    command.context.strategy = "test";
    command.context.account = "DU123";
    command.contract.symbol = "EUR";
    command.contract.secType = "CASH";
    command.contract.currency = "USD";
    command.contract.exchange = "IDEALPRO";
    command.instrument = "EUR.USD";
    command.order.action = "BUY";
    command.order.orderType = "LMT";
    command.order.totalQuantity = 1000.0;
    command.order.lmtPrice = 1.1;
    command.expiresAtMs = OmsJournal::NowEpochMs() + 60000;
    return command;
}

FlattenPositionCommand MakeFlatten(
    const std::string& callId, double position)
{
    FlattenPositionCommand command;
    command.context = MakePlace(callId).context;
    command.context.toolCallId = callId;
    command.context.venue = "IB";
    command.context.executionDomain = "PAPER";
    command.context.decisionLeaseFencingToken = 17;
    command.context.decisionLeaseGeneration = 3;
    command.contract = MakePlace(callId).contract;
    command.instrument = "EUR.USD";
    command.hasAuthoritativePreviewSnapshot = true;
    command.previewPositionQuantity = position;
    command.previewPositionConnectionEpoch = 21;
    command.previewPositionGeneration = 22;
    command.authoritativePreviewPlanBinding =
        "execution-owned-flatten-plan-binding";
    return command;
}

AuthoritativeFlattenPlan MakeFlattenPlan(
    const FlattenPositionCommand& command, double position)
{
    AuthoritativeFlattenPlan plan;
    plan.contract = command.contract;
    plan.instrument = command.instrument;
    plan.expectedPositionQuantity = position;
    plan.positionConnectionEpoch =
        command.previewPositionConnectionEpoch;
    plan.positionGeneration = command.previewPositionGeneration;
    if (position != 0.0)
    {
        plan.order.action = position > 0.0 ? "SELL" : "BUY";
        plan.order.orderType = "MKT";
        plan.order.totalQuantity = std::fabs(position);
        plan.order.lmtPrice = 0.0;
        plan.timeInForce = "DAY";
        plan.referencePrice = 1.10;
        plan.quoteSubscriptionId = "IB:21:4:1001";
        plan.quoteObservedAtMs = 100000;
        plan.quoteStaleAfterMs = 105000;
    }
    return plan;
}

} // namespace hepta_execution_coordinator_test
