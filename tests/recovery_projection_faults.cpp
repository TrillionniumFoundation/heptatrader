// Test-only allocator fault sweep. No runtime fault switch or Broker transport.
#include "../HeptaTrade/execution/execution_coordinator.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <new>
#include <string>
#include <vector>

namespace faults {
thread_local bool counting = false;
thread_local long failAt = -1;
thread_local std::size_t calls = 0;
thread_local bool fired = false;
void Arm(long index) { calls = 0; fired = false; failAt = index; counting = true; }
void Disarm() { counting = false; failAt = -1; }
void* Allocate(std::size_t bytes) {
    if (counting) {
        const auto current = calls++;
        if (failAt >= 0 && current == static_cast<std::size_t>(failAt)) {
            failAt = -1; fired = true; throw std::bad_alloc();
        }
    }
    if (void* p = std::malloc(bytes ? bytes : 1)) return p;
    throw std::bad_alloc();
}
}
void* operator new(std::size_t n) { return faults::Allocate(n); }
void* operator new[](std::size_t n) { return faults::Allocate(n); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
#if __cplusplus >= 201402L
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
#endif

namespace {
void Check(bool ok, const char* message) {
    if (!ok) { std::fprintf(stderr, "recovery check failed: %s\n", message); std::abort(); }
}
std::string Bytes(const std::string& path) {
    std::ifstream in(path.c_str(), std::ios::binary);
    Check(bool(in), "fixture read");
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}
PlaceOrderCommand Command(int id) {
    PlaceOrderCommand c;
    c.context.agentId = "recovery-agent-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    c.context.sessionId = "recovery-session-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    c.context.toolCallId = "recovery-command-cccccccccccccccccccccccccccc-" + std::to_string(id);
    c.context.account = "recovery-fixture-account";
    c.context.strategy = "recovery-fixture-strategy";
    c.contract.symbol = "EUR"; c.contract.secType = "CASH";
    c.contract.currency = "USD"; c.contract.exchange = "IDEALPRO";
    c.instrument = "EUR.USD";
    c.order.action = "BUY"; c.order.orderType = "LMT";
    c.order.totalQuantity = 1; c.order.lmtPrice = 1.1;
    c.expiresAtMs = 0;
    return c;
}
void CheckProjection(ExecutionCoordinator& coordinator, bool present) {
    for (int i = 0; i < 2; ++i) {
        const auto c = Command(i);
        ExecutionCommandResult result;
        Check(coordinator.GetCommandStatus(c.context.agentId, c.context.sessionId,
              c.context.toolCallId, result) == present, "command projection is atomic");
        ExecutionOrderOwner owner;
        Check(coordinator.GetOrderOwner(41 + i, owner) == present, "owner projection is atomic");
        if (present) {
            Check(result.status == ExecutionCommandStatus::Accepted && result.orderId == 41 + i,
                  "accepted identity preserved");
            Check(owner.agentId == c.context.agentId && owner.account == c.context.account,
                  "owner binding preserved");
        }
    }
    std::vector<std::int64_t> times;
    coordinator.GetPlaceSendAttemptTimes("recovery-fixture-account", "", 0, times);
    Check(times.size() == (present ? 2U : 0U), "rate index is atomic");
    Check(coordinator.IsSessionOwnerFenced("other-owner", "other-session") == present,
          "owner fence projection is atomic");
}
void Sweep(const std::string& path) {
    OmsJournal journal;
    Check(journal.Init(path), "journal initialization");
    int sends = 0;
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [&](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Submitted(41 + sends++);
        });
    {
        ExecutionCoordinator writer(journal, callbacks);
        for (int i = 0; i < 2; ++i)
            Check(writer.PlaceOrder(Command(i)).status == ExecutionCommandStatus::Accepted,
                  "seed actual coordinator commands");
        writer.FenceSessionOwner("other-owner", "other-session");
    }
    const auto original = Bytes(path);
    std::size_t baseline = 0, failures = 0, projectionFailures = 0, successes = 0;
    {
        ExecutionCoordinator reader(journal, callbacks);
        std::string reason;
        faults::Arm(-1);
        const bool ok = reader.RecoverFromJournal(reason);
        baseline = faults::calls;
        faults::Disarm();
        Check(ok, "positive recovery control");
        CheckProjection(reader, true);
    }
    Check(baseline > 0 && baseline < 10000, "bounded allocation sweep");
    for (std::size_t n = 0; n < baseline + 16; ++n) {
        ExecutionCoordinator reader(journal, callbacks);
        std::string reason;
        // Existing state must not survive a failed rehydration as stale truth.
        Check(reader.RecoverFromJournal(reason), "prepare repeated recovery");
        faults::Arm(static_cast<long>(n));
        bool ok = false;
        try { ok = reader.RecoverFromJournal(reason); }
        catch (...) { faults::Disarm(); Check(false, "one-shot allocation fault escaped"); }
        faults::Disarm();
        if (!ok) {
            ++failures;
            Check(faults::fired, "negative recovery was actually injected");
            Check(reader.IsMutationBlocked(), "failed projection stays fenced");
            CheckProjection(reader, false);
            if (reason == "OMS_RECOVERY_PROJECTION_FAILED") ++projectionFailures;
            Check(reader.PlaceOrder(Command(0)).status != ExecutionCommandStatus::Accepted,
                  "failure cannot resend an admitted command");
        } else {
            ++successes;
            CheckProjection(reader, true);
        }
        Check(reader.RecoverFromJournal(reason), "retry recovery after fault removal");
        CheckProjection(reader, true);
        Check(reader.PlaceOrder(Command(0)).status == ExecutionCommandStatus::Duplicate,
              "original command remains duplicate after recovery");
        Check(sends == 2, "fault/restart must not resubmit");
        Check(Bytes(path) == original, "recovery never rewrites journal bytes");
    }
    Check(failures > 0 && projectionFailures > 0 && successes > 0,
          "validation/projection failures and successful controls exercised");
    // Late corrupt data must prevent even the valid prefix becoming visible.
    {
        std::ofstream out(path.c_str(), std::ios::app | std::ios::binary);
        out << "{invalid-json}\n";
        Check(bool(out), "append corruption fixture");
    }
    const auto corrupt = Bytes(path);
    ExecutionCoordinator bad(journal, callbacks);
    std::string reason;
    Check(!bad.RecoverFromJournal(reason) && bad.IsMutationBlocked(), "late corruption fences");
    CheckProjection(bad, false);
    Check(Bytes(path) == corrupt && sends == 2, "corrupt evidence retained without resend");
    std::printf("allocation_sweep=%zu failed=%zu projection_failed=%zu success=%zu\n",
                baseline + 16, failures, projectionFailures, successes);
}
void Uncertain(const std::string& path) {
    OmsJournal journal;
    Check(journal.Init(path), "uncertain journal initialization");
    ExecutionCoordinatorCallbacks callbacks;
    callbacks.placement = VenuePlacement::Immediate(
        [](const PlaceOrderCommand&, const std::string&) {
            return VenuePlaceResult::Uncertain("fixture uncertain outcome");
        });
    ExecutionCoordinator writer(journal, callbacks);
    Check(writer.PlaceOrder(Command(0)).status == ExecutionCommandStatus::Uncertain,
          "seed actual uncertain outcome");
    ExecutionCoordinator reader(journal, callbacks);
    std::string reason;
    Check(!reader.RecoverFromJournal(reason) && reader.IsMutationBlocked(),
          "semantic uncertainty is not successful recovery");
    ExecutionCommandResult result;
    const auto c = Command(0);
    Check(reader.GetCommandStatus(c.context.agentId, c.context.sessionId,
          c.context.toolCallId, result), "uncertain identity retained for reconciliation");
    Check(result.status == ExecutionCommandStatus::Uncertain,
          "exception cleanup must not erase valid uncertain state");
}
}
int main(int argc, char** argv) {
    Check(argc == 2, "private fixture directory argument");
    Check(::setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1) == 0, "synchronous fixture");
    Sweep(std::string(argv[1]) + "/sweep.jsonl");
    Uncertain(std::string(argv[1]) + "/uncertain.jsonl");
    std::puts("recovery_projection_faults: PASS");
}
