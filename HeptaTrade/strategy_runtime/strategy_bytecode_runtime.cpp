#include "strategy_bytecode_runtime.h"
#include <algorithm>
#include <chrono>
#include <limits>
#include <type_traits>
#include <utility>

#if defined(__linux__) && defined(__x86_64__)
#include "strategy_bytecode_engine.h"
#include "strategy_bytecode_sandbox.h"
#include <poll.h>
#include <pthread.h>
#include <sys/wait.h>
#endif

namespace
{
StrategyBytecodeResult Reject(const char* code)
{ StrategyBytecodeResult r; r.reasonCode = code; return r; }
#if defined(__linux__) && defined(__x86_64__)
using namespace hepta_bytecode_detail;
bool Same(const StrategyArtifactDescriptor& a, const StrategyArtifactDescriptor& b)
{
    return a.moduleId == b.moduleId && a.version == b.version && a.artifactDigest == b.artifactDigest &&
        a.configDigest == b.configDigest && a.modelDigest == b.modelDigest &&
        a.budget.maxMemoryBytes == b.budget.maxMemoryBytes && a.budget.maxThreads == b.budget.maxThreads &&
        a.budget.maxCheckpointBytes == b.budget.maxCheckpointBytes && a.budget.maxFileDescriptors == b.budget.maxFileDescriptors;
}
class Fd {
public:
    int value = -1;
    Fd() = default; Fd(const Fd&) = delete; Fd& operator=(const Fd&) = delete;
    ~Fd() { if (value >= 0) ::close(value); }
    void Close() noexcept { const int old = value; value = -1; if (old >= 0) ::close(old); }
};
class Mask {
    sigset_t previous{};
    bool active = false;
public:
    bool Block() noexcept { sigset_t all; ::sigfillset(&all); active = ::pthread_sigmask(SIG_BLOCK, &all, &previous) == 0; return active; }
    bool Restore() noexcept { if (!active) return true; const bool ok = ::pthread_sigmask(SIG_SETMASK, &previous, nullptr) == 0; if (ok) active = false; return ok; }
    ~Mask() { Restore(); }
};
class Child {
public:
    pid_t pid = -1; Fd pidfd;
    void Terminate() noexcept {
        if (pid < 0) return;
        if (pidfd.value >= 0) ::syscall(SYS_pidfd_send_signal, pidfd.value, SIGKILL, nullptr, 0u);
        else ::kill(pid, SIGKILL); // Only before pidfd acquisition; supervisor owns all child reaping.
        int status; pid_t result;
        do { result = ::waitpid(pid, &status, 0); } while (result < 0 && errno == EINTR);
        pid = -1;
    }
    ~Child() { Terminate(); }
};
using Clock = std::chrono::steady_clock;
std::uint64_t ElapsedMs(Clock::time_point start)
{
    const auto us = std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - start).count();
    return us > 0 ? static_cast<std::uint64_t>(us) / 1000 + (us % 1000 != 0) : 0;
}
#endif
}

StrategyBytecodeResult StrategyBytecodeRuntime::Run(StrategyRuntimeControl& controller,
    const std::string& moduleId, std::uint64_t expectedGeneration,
    const VerifiedStrategyArtifact& artifact, const StrategyArtifactVerifier& verifier,
    const StrategyBytecodeInvocation& invocation, const VerifiedStrategyCheckpoint& checkpoint,
    const StrategyBytecodeLimits& limits, const std::atomic<bool>* cancelled)
{
#if !defined(__linux__) || !defined(__x86_64__)
    (void)controller; (void)moduleId; (void)expectedGeneration; (void)artifact; (void)verifier;
    (void)invocation; (void)checkpoint; (void)limits; (void)cancelled;
    return Reject("STRATEGY_VM_PLATFORM_UNSUPPORTED");
#else
    std::unique_lock<std::mutex> single(m_mutex, std::try_to_lock);
    if (!single.owns_lock()) return Reject("STRATEGY_VM_BUSY");
    const auto start = Clock::now();
    if (cancelled && cancelled->load()) return Reject("STRATEGY_VM_CANCELLED");
    if (limits.maximumSteps == 0 || limits.maximumSteps > 1000000 || limits.wallTimeMs == 0 ||
        limits.wallTimeMs > 5000 || limits.childAddressSpaceBytes < (1u << 20) ||
        limits.childAddressSpaceBytes > (1u << 30)) return Reject("STRATEGY_VM_LIMIT_INVALID");
    if (!verifier.Authorizes(artifact, invocation.observedAtMs)) return Reject("STRATEGY_VM_AUTHORIZATION_INVALID");
    const auto& descriptor = artifact.Descriptor();
    StrategyRuntimeSnapshot before;
    if (!controller.Get(moduleId, before) || before.generation != expectedGeneration ||
        before.phase != StrategyRuntimePhase::Running || !Same(before.descriptor, descriptor) ||
        invocation.observedAtMs < before.updatedAtMs) return Reject("STRATEGY_VM_CONTROLLER_INVALID");
    if (descriptor.budget.maxMemoryBytes < (1u << 20) || descriptor.budget.maxFileDescriptors < 4 ||
        descriptor.budget.maxCheckpointBytes < sizeof(StateMagic) - 1 + StateSize * 8)
        return Reject("STRATEGY_VM_DESCRIPTOR_BUDGET_INVALID");
    if (invocation.inputs.size() > MaximumInputs || invocation.snapshotDigest.size() != 71 ||
        invocation.expiresAtMs > artifact.ExpiresAtMs() || invocation.expiresAtMs <= invocation.observedAtMs)
        return Reject("STRATEGY_VM_INPUT_INVALID");
    for (const auto* text : {&invocation.proposalId, &invocation.candidateId, &invocation.capitalPool,
                            &invocation.accountBook, &invocation.instrument})
        if (text->empty() || text->size() > 128) return Reject("STRATEGY_VM_INPUT_INVALID");
    Frame frame; frame.inputCount = invocation.inputs.size();
    for (std::size_t i = 0; i < invocation.inputs.size(); ++i) {
        if (!InRange(invocation.inputs[i])) return Reject("STRATEGY_VM_INPUT_INVALID");
        frame.inputs[i] = invocation.inputs[i];
    }
    if (before.checkpointSequence != 0) {
        if (!checkpoint.IsValid() || !Same(descriptor, checkpoint.Descriptor()) ||
            checkpoint.Sequence() != before.checkpointSequence || checkpoint.PayloadDigest() != before.checkpointDigest ||
            checkpoint.Payload().size() != before.checkpointBytes || checkpoint.SavedAtMs() > invocation.observedAtMs ||
            !DecodeState(checkpoint.Payload(), frame)) return Reject("STRATEGY_VM_CHECKPOINT_INVALID");
    } else if (checkpoint.IsValid()) return Reject("STRATEGY_VM_CHECKPOINT_INVALID");
    Program program;
    if (!Decode(artifact.ArtifactBytes(), invocation.inputs.size(), program)) return Reject("STRATEGY_VM_PROGRAM_INVALID");
    StrategyProposal proposal;
    proposal.proposalId = invocation.proposalId; proposal.moduleId = descriptor.moduleId;
    proposal.moduleVersion = descriptor.version; proposal.sequence = invocation.sequence;
    proposal.capitalPool = invocation.capitalPool; proposal.accountBook = invocation.accountBook;
    proposal.snapshotDigest = invocation.snapshotDigest; proposal.validFromMs = invocation.observedAtMs;
    proposal.expiresAtMs = invocation.expiresAtMs; proposal.horizonMs = invocation.horizonMs;
    StrategyProposalCandidate candidate; candidate.candidateId = invocation.candidateId;
    candidate.targets.push_back({invocation.instrument, 0}); proposal.candidates.push_back(candidate);
    if (!StrategyProposalContract::ValidateAndSeal(proposal, invocation.observedAtMs).accepted)
        return Reject("STRATEGY_VM_CONTEXT_INVALID");
    // The horizon is anchored at invocation, not renewed by a later aggregator.
    proposal.expiresAtMs = invocation.observedAtMs + invocation.horizonMs;
    struct sigaction disposition{};
    if (::sigaction(SIGCHLD, nullptr, &disposition) != 0 || disposition.sa_handler != SIG_DFL ||
        (disposition.sa_flags & SA_NOCLDWAIT) != 0) return Reject("STRATEGY_VM_REAPER_INVALID");
    int fds[2];
    if (::pipe2(fds, O_CLOEXEC | O_NONBLOCK) != 0) return Reject("STRATEGY_VM_PIPE_FAILED");
    Fd reader, writer; reader.value = fds[0]; writer.value = fds[1];
    if (ElapsedMs(start) >= limits.wallTimeMs) return Reject("STRATEGY_VM_TIMEOUT");
    const auto memory = std::min(limits.childAddressSpaceBytes, descriptor.budget.maxMemoryBytes);
    const auto parent = ::getpid();
    Mask mask;
    if (!mask.Block()) return Reject("STRATEGY_VM_SIGNAL_SETUP_FAILED");
    Child child;
    child.pid = ::fork();
    if (child.pid == 0) {
        // All signals remain blocked; no inherited user/sanitizer handlers run.
        // Everything below is fixed storage, pure arithmetic or direct syscalls.
        if (!InstallGuards(writer.value, parent, memory, (limits.wallTimeMs + 999) / 1000,
                           static_cast<unsigned>(sizeof(Wire)))) ::_exit(125);
        const Wire wire = Evaluate(program, frame, limits.maximumSteps);
        ssize_t written;
        do { written = ::write(3, &wire, sizeof(wire)); } while (written < 0 && errno == EINTR);
        ::syscall(SYS_exit_group, written == static_cast<ssize_t>(sizeof(wire)) ? 0 : 126);
        ::_exit(127); // Unreachable unless the exit syscall itself fails.
    }
    if (!mask.Restore()) return Reject("STRATEGY_VM_SIGNAL_SETUP_FAILED");
    if (child.pid < 0) return Reject("STRATEGY_VM_FORK_FAILED");
    child.pidfd.value = static_cast<int>(::syscall(SYS_pidfd_open, child.pid, 0u));
    if (child.pidfd.value < 0) return Reject("STRATEGY_VM_PIDFD_FAILED");
    writer.Close();
    Wire wire; std::size_t received = 0; bool eof = false; int status = 0;
    for (;;) {
        if (cancelled && cancelled->load()) return Reject("STRATEGY_VM_CANCELLED");
        if (ElapsedMs(start) >= limits.wallTimeMs) return Reject("STRATEGY_VM_TIMEOUT");
        if (!eof) {
            char extra;
            void* buffer = received < sizeof(wire) ? static_cast<void*>(reinterpret_cast<char*>(&wire) + received) : &extra;
            const auto n = ::read(reader.value, buffer, received < sizeof(wire) ? sizeof(wire) - received : 1);
            if (n > 0) {
                if (received == sizeof(wire)) return Reject("STRATEGY_VM_PROTOCOL_INVALID");
                received += static_cast<std::size_t>(n);
            } else if (n == 0) eof = true;
            else if (errno != EINTR && errno != EAGAIN) return Reject("STRATEGY_VM_READ_FAILED");
        }
        const auto waited = ::waitpid(child.pid, &status, WNOHANG);
        if (waited < 0 && errno == ECHILD) { child.pid = -1; return Reject("STRATEGY_VM_REAPER_LOST"); }
        if (waited < 0 && errno != EINTR) return Reject("STRATEGY_VM_WAIT_FAILED");
        if (waited == child.pid) {
            child.pid = -1;
            if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) return Reject("STRATEGY_VM_CHILD_FAILED");
            // Drain a result that became readable between read and waitpid.
            while (!eof) {
                if (ElapsedMs(start) >= limits.wallTimeMs) return Reject("STRATEGY_VM_TIMEOUT");
                char extra; void* buffer = received < sizeof(wire) ? static_cast<void*>(reinterpret_cast<char*>(&wire) + received) : &extra;
                const auto n = ::read(reader.value, buffer, received < sizeof(wire) ? sizeof(wire) - received : 1);
                if (n > 0) { if (received == sizeof(wire)) return Reject("STRATEGY_VM_PROTOCOL_INVALID"); received += static_cast<std::size_t>(n); }
                else if (n == 0) eof = true;
                else if (errno != EINTR) return Reject("STRATEGY_VM_READ_FAILED");
            }
            break;
        }
        struct pollfd pollfd{reader.value, POLLIN | POLLHUP, 0};
        if (::poll(&pollfd, 1, 1) < 0 && errno != EINTR) return Reject("STRATEGY_VM_POLL_FAILED");
    }
    if (received != sizeof(wire) || wire.magic != WireMagic || wire.steps == 0 || wire.steps > limits.maximumSteps)
        return Reject("STRATEGY_VM_PROTOCOL_INVALID");
    if (wire.fault != Success) {
        const char* code = wire.fault == Fuel ? "STRATEGY_VM_FUEL_EXHAUSTED" : wire.fault == Stack ?
            "STRATEGY_VM_STACK_INVALID" : wire.fault == Numeric ? "STRATEGY_VM_NUMERIC_INVALID" : "STRATEGY_VM_NO_EMIT";
        auto result = Reject(code); result.steps = wire.steps; return result;
    }
    if (!InRange(wire.utility) || !InRange(wire.target)) return Reject("STRATEGY_VM_PROTOCOL_INVALID");
    for (const auto value : wire.state) if (!InRange(value)) return Reject("STRATEGY_VM_PROTOCOL_INVALID");
    const auto elapsed = ElapsedMs(start);
    if (elapsed >= limits.wallTimeMs || elapsed > std::numeric_limits<std::uint64_t>::max() - invocation.observedAtMs)
        return Reject("STRATEGY_VM_TIMEOUT");
    const auto finishedAt = invocation.observedAtMs + elapsed;
    if (finishedAt >= invocation.expiresAtMs || elapsed >= invocation.horizonMs ||
        !verifier.Authorizes(artifact, finishedAt)) return Reject("STRATEGY_VM_RESULT_EXPIRED");
    if (cancelled && cancelled->load()) return Reject("STRATEGY_VM_CANCELLED");
    StrategyRuntimeSnapshot after;
    if (!controller.Get(moduleId, after) || after.generation != expectedGeneration ||
        after.phase != StrategyRuntimePhase::Running || !Same(after.descriptor, descriptor))
        return Reject("STRATEGY_VM_GENERATION_CHANGED");
    proposal.candidates[0].utility = wire.utility; proposal.candidates[0].targets[0].targetPosition = wire.target;
    auto sealed = StrategyProposalContract::ValidateAndSeal(proposal, finishedAt);
    if (!sealed.accepted) return Reject("STRATEGY_VM_OUTPUT_INVALID");
    StrategyBytecodeResult result;
    result.proposal = std::move(sealed.proposal); result.checkpointPayload = EncodeState(wire);
    result.steps = wire.steps; result.reasonCode = "STRATEGY_VM_COMPLETED"; result.accepted = true;
    return result;
#endif
}
