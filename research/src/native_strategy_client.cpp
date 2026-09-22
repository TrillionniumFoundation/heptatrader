#include "hepta/research/native_strategy_client.h"
#include <tools/trading_tool_wire_contract.h>
#include <cmath>
#include <stdexcept>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

namespace hepta { namespace research {
namespace {
void CheckId(const std::string& id) {
    if (!TradingToolWireContract::IsCanonicalCommandId(id))
        throw std::invalid_argument("RESEARCH_TOOL_CALL_ID_INVALID");
}
void Validate(const TradingToolCall& call) {
    std::string reason, detail;
    if (!TradingToolWireContract::ValidateCallSemantics(call, reason, detail))
        throw std::invalid_argument(reason + ": " + detail);
}
}
PreparedOrder::PreparedOrder(const std::string& instrument, const InstrumentRef& contract,
                             const std::string& side, double quantity, double limit,
                             double reference, std::int64_t expiry) {
    // This version explicitly supports only the existing LMT/DAY profile.
    // SHFE close-today/yesterday and legacy auto-open/close are not inferred.
    if (expiry <= 0 || !std::isfinite(quantity) || quantity <= 0 ||
        !std::isfinite(limit) || limit <= 0 || !std::isfinite(reference) || reference <= 0 ||
        (side != "BUY" && side != "SELL"))
        throw std::invalid_argument("RESEARCH_ORDER_PROPOSAL_INVALID");
    // HTT1 carries only these four contract fields. Do not silently discard
    // an option expiry, strike, multiplier or other unsupported identity.
    if (contract.symbol.empty() || contract.currency.empty() || contract.secType.empty() ||
        contract.exchange.empty() || !contract.primaryExchange.empty() ||
        !contract.lastTradeDateOrContractMonth.empty() || !contract.right.empty() ||
        contract.strike != 0 || !contract.multiplier.empty() ||
        !contract.tradingClass.empty() || !contract.localSymbol.empty())
        throw std::invalid_argument("RESEARCH_CONTRACT_NOT_REPRESENTABLE_BY_HTT1");
    call_.name = "risk.preview_order"; call_.instrument = instrument;
    call_.ibContract = contract; call_.ibOrder.action = side;
    call_.ibOrder.orderType = "LMT"; call_.ibOrder.totalQuantity = quantity;
    call_.ibOrder.lmtPrice = limit; call_.timeInForce = "DAY";
    call_.referencePrice = reference; call_.expiresAtMs = expiry;
    Validate(call_);
}
TradingToolHostRequest PreparedOrder::PreviewRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
TradingToolHostRequest PreparedOrder::SubmissionRequest(const std::string& id, const std::string& permit) const {
    CheckId(id);
    if (permit.empty() || permit.size() > 4096)
        throw std::invalid_argument("RESEARCH_PREVIEW_PERMIT_REQUIRED");
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    request.call.name = "trade.place_order"; request.call.previewPermit = permit;
    Validate(request.call);
    return request;
}
PreparedCancellation::PreparedCancellation(long serverOrderId) {
    call_.name = "trade.cancel_order"; call_.orderId = serverOrderId;
    Validate(call_);
}
TradingToolHostRequest PreparedCancellation::SubmissionRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
PreparedFlatten::PreparedFlatten(const std::string& instrument) {
    call_.name = "risk.preview_flatten"; call_.instrument = instrument;
    Validate(call_);
}
TradingToolHostRequest PreparedFlatten::PreviewRequest(const std::string& id) const {
    CheckId(id);
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    return request;
}
TradingToolHostRequest PreparedFlatten::SubmissionRequest(const std::string& id,
                                                          const std::string& permit) const {
    CheckId(id);
    if (permit.empty() || permit.size() > 4096)
        throw std::invalid_argument("RESEARCH_PREVIEW_PERMIT_REQUIRED");
    TradingToolHostRequest request;
    request.toolCallId = id; request.call = call_;
    request.call.name = "trade.flatten_position"; request.call.previewPermit = permit;
    Validate(request.call);
    return request;
}
bool NativeStrategyClient::Forward(const TradingToolHostRequest& request, NativeToolClientResult& result,
                                   std::string& reason) const {
    // Never allow a previous success envelope to survive a failed transport.
    result = NativeToolClientResult(); reason.clear();
    const bool transported = client_.Call(request, result, reason);
    if (!transported) result = NativeToolClientResult();
    return transported;
}
// Build an owned request before Forward clears outputs. IDs and permits may
// refer to fields in the previous result or to the diagnostic string.
bool NativeStrategyClient::Preview(const PreparedOrder& order, const std::string& id,
                                   NativeToolClientResult& result, std::string& reason) const {
    try { return Forward(order.PreviewRequest(id), result, reason); }
    catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::PreviewAuthorized(
    const PreparedOrder& order, const std::string& previewCallId,
    TypedPreviewAuthorization& authorization, NativeToolClientResult& result,
    std::string& reason) const {
    const std::string id = previewCallId;
    authorization = TypedPreviewAuthorization();
    if (!Preview(order, id, result, reason)) return false;
    return TypedToolProtocol::DecodePreviewAuthorization(
        result.responseJson, "risk.preview_order", authorization, reason);
}
bool NativeStrategyClient::PreviewAuthorized(
    const PreparedFlatten& flatten, const std::string& previewCallId,
    TypedPreviewAuthorization& authorization, NativeToolClientResult& result,
    std::string& reason) const {
    const std::string id = previewCallId;
    authorization = TypedPreviewAuthorization();
    if (!PreviewFlatten(flatten, id, result, reason)) return false;
    return TypedToolProtocol::DecodePreviewAuthorization(
        result.responseJson, "risk.preview_flatten", authorization, reason);
}
bool NativeStrategyClient::Submit(const PreparedOrder& order, const std::string& id,
                                  const std::string& permit, NativeToolClientResult& result,
                                  std::string& reason) const {
    try { return Forward(order.SubmissionRequest(id, permit), result, reason); }
    catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Cancel(const PreparedCancellation& cancellation, const std::string& id,
                                  NativeToolClientResult& result, std::string& reason) const {
    try { return Forward(cancellation.SubmissionRequest(id), result, reason); }
    catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::PreviewFlatten(const PreparedFlatten& flatten, const std::string& id,
                                          NativeToolClientResult& result, std::string& reason) const {
    try { return Forward(flatten.PreviewRequest(id), result, reason); }
    catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Flatten(const PreparedFlatten& flatten, const std::string& id,
                                   const std::string& permit, NativeToolClientResult& result,
                                   std::string& reason) const {
    try { return Forward(flatten.SubmissionRequest(id, permit), result, reason); }
    catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Status(const std::string& id, const std::string& queryId,
                                  NativeToolClientResult& result, std::string& reason) const {
    try {
        CheckId(id); CheckId(queryId);
        TradingToolHostRequest request;
        request.toolCallId = queryId; request.call.name = "execution.get_command_status";
        request.call.targetCommandId = id; Validate(request.call);
        return Forward(request, result, reason);
    } catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}

namespace {
const char* const kOutboxToken = "hepta-strategy-outbox-v1-not-a-session-token";
const std::size_t kRecordLimit = 65536 + 149;
std::atomic<unsigned long> outboxTemporaryCounter(0);
class OutboxFd {
public:
    explicit OutboxFd(int fd = -1) : fd_(fd) {}
    ~OutboxFd() { if (fd_ >= 0) ::close(fd_); }
    OutboxFd(const OutboxFd&) = delete;
    OutboxFd& operator=(const OutboxFd&) = delete;
    int Get() const { return fd_; }
    int Release() { const int fd = fd_; fd_ = -1; return fd; }
    void Reset(int fd) { if (fd_ >= 0) ::close(fd_); fd_ = fd; }
private:
    int fd_;
};
bool OutboxFail(std::string& reason, const char* code) { reason = code; return false; }
bool SyncOutbox(int fd) {
    int result;
    do { result = ::fsync(fd); } while (result < 0 && errno == EINTR);
    return result == 0;
}
int OpenOutbox(const std::string& path) {
    // Walk every path component relative to a pinned descriptor. O_NOFOLLOW on
    // the final filename alone would still follow an intermediate symlink.
    if (path.empty() || path[0] != '/' || path.size() > 4096 ||
        path.back() == '/' || path.find('\0') != std::string::npos) return -1;
    OutboxFd current(::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (current.Get() < 0) return -1;
    std::size_t begin = 1;
    while (begin < path.size()) {
        const auto slash = path.find('/', begin);
        const bool last = slash == std::string::npos;
        const auto part = path.substr(begin, last ? std::string::npos : slash - begin);
        if (part.empty() || part == "." || part == "..") return -1;
        OutboxFd next(::openat(current.Get(), part.c_str(),
            O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
        struct stat info;
        if (next.Get() < 0 || ::fstat(next.Get(), &info) != 0 || !S_ISDIR(info.st_mode)) return -1;
        if (last) {
            if (info.st_uid != ::geteuid() || (info.st_mode & 07777) != 0700) return -1;
            return next.Release();
        }
        // Trust only root/self ancestors. A root-owned sticky directory such
        // as /tmp is permitted; other group/world-writable ancestors are not.
        if ((info.st_uid != 0 && info.st_uid != ::geteuid()) ||
            ((info.st_mode & 0022) != 0 && !(info.st_uid == 0 && (info.st_mode & S_ISVTX)))) return -1;
        current.Reset(next.Release());
        begin = slash + 1;
    }
    return -1;
}
bool LockOutbox(int fd) {
    int result;
    do { result = ::flock(fd, LOCK_EX); } while (result < 0 && errno == EINTR);
    return result == 0;
}
bool SameRecord(const struct stat& a, const struct stat& b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
        a.st_uid == b.st_uid && a.st_gid == b.st_gid && a.st_mode == b.st_mode &&
        a.st_nlink == 1 && b.st_nlink == 1 &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}
bool ReadOutbox(int directory, const std::string& name, std::string& bytes) {
    bytes.clear();
    // NONBLOCK prevents a malicious FIFO/device entry from blocking open/read.
    OutboxFd file(::openat(directory, name.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK));
    struct stat before, after, linked;
    if (file.Get() < 0 || ::fstat(file.Get(), &before) != 0 || !S_ISREG(before.st_mode) ||
        before.st_uid != ::geteuid() || before.st_nlink != 1 ||
        (before.st_mode & 07777) != 0600 || before.st_size <= 149 ||
        before.st_size > static_cast<off_t>(kRecordLimit)) return false;
    std::string content(static_cast<std::size_t>(before.st_size), '\0');
    std::size_t offset = 0;
    while (offset < content.size()) {
        const auto count = ::read(file.Get(), &content[offset], content.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return false;
        offset += static_cast<std::size_t>(count);
    }
    char extra;
    ssize_t count;
    do { count = ::read(file.Get(), &extra, 1); } while (count < 0 && errno == EINTR);
    if (count != 0 || !SyncOutbox(file.Get()) || ::fstat(file.Get(), &after) != 0 ||
        ::fstatat(directory, name.c_str(), &linked, AT_SYMLINK_NOFOLLOW) != 0 ||
        !SameRecord(before, after) || !SameRecord(after, linked)) return false;
    bytes.swap(content);
    return true;
}
bool SameOutboxPath(int fd, const std::string& directory) {
    OutboxFd named(OpenOutbox(directory));
    struct stat a, b;
    return named.Get() >= 0 && ::fstat(fd, &a) == 0 && ::fstat(named.Get(), &b) == 0 &&
        a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_mode == b.st_mode && a.st_uid == b.st_uid;
}
bool OutboxMutation(const TradingToolHostRequest& request) {
    const auto& name = request.call.name;
    return (name == "trade.place_order" || name == "trade.flatten_position" || name == "trade.cancel_order") &&
        request.cancelToolCallId.empty() && request.queueDeadlineAtMs == 0;
}
bool DecodeOutbox(const std::string& bytes, const std::string& id,
                  TradingToolHostRequest& request, std::string& binding, std::string& reason) {
    request = TradingToolHostRequest(); binding.clear();
    if (bytes.size() <= 149 || bytes.size() > kRecordLimit || bytes.compare(0, 5, "HSR1\n") != 0 ||
        bytes[76] != '\n' || bytes[148] != '\n') return OutboxFail(reason, "RESEARCH_OUTBOX_FORMAT_INVALID");
    const std::string observed = bytes.substr(5, 71), wire = bytes.substr(149);
    if (bytes.substr(77, 71) != NativeToolDiscoveryContract::ContentDigest("HSR1\n" + observed + "\n" + wire))
        return OutboxFail(reason, "RESEARCH_OUTBOX_DIGEST_MISMATCH");
    TradingToolHostRequest candidate;
    if (!TypedToolProtocol::DecodeRequest(wire, candidate, reason)) return false;
    if (candidate.sessionToken != kOutboxToken || candidate.toolCallId != id || !OutboxMutation(candidate))
        return OutboxFail(reason, "RESEARCH_OUTBOX_REQUEST_BINDING_INVALID");
    std::string encoded;
    if (!TypedToolProtocol::EncodeRequest(candidate, encoded, reason) || encoded != wire)
        return OutboxFail(reason, "RESEARCH_OUTBOX_NONCANONICAL");
    candidate.sessionToken.clear();
    request = candidate; binding = observed; reason.clear(); return true;
}
bool LoadOutbox(const std::string& directory, const std::string& id,
                TradingToolHostRequest& request, std::string& binding, std::string& reason) {
    request = TradingToolHostRequest(); binding.clear(); reason.clear();
    if (!TradingToolWireContract::IsCanonicalCommandId(id)) return OutboxFail(reason, "RESEARCH_OUTBOX_ID_INVALID");
    OutboxFd dir(OpenOutbox(directory));
    if (dir.Get() < 0) return OutboxFail(reason, "RESEARCH_OUTBOX_DIRECTORY_UNSAFE");
    if (!LockOutbox(dir.Get())) return OutboxFail(reason, "RESEARCH_OUTBOX_LOCK_FAILED");
    std::string bytes;
    if (!ReadOutbox(dir.Get(), id + ".hsr", bytes)) return OutboxFail(reason, "RESEARCH_OUTBOX_RECORD_UNSAFE");
    if (!SyncOutbox(dir.Get()) || !SameOutboxPath(dir.Get(), directory))
        return OutboxFail(reason, "RESEARCH_OUTBOX_SYNC_OR_PATH_FAILED");
    return DecodeOutbox(bytes, id, request, binding, reason);
}
class OutboxTemporary {
public:
    OutboxTemporary(int directory, const std::string& name) : directory_(directory), name_(name) {}
    ~OutboxTemporary() { if (!name_.empty()) ::unlinkat(directory_, name_.c_str(), 0); }
    void Published() { name_.clear(); }
private:
    int directory_;
    std::string name_;
};
bool WriteOutbox(int directory, const std::string& name, const std::string& bytes, std::string& reason) {
    struct stat existing;
    if (::fstatat(directory, name.c_str(), &existing, AT_SYMLINK_NOFOLLOW) == 0) {
        std::string previous;
        if (!ReadOutbox(directory, name, previous) || previous != bytes)
            return OutboxFail(reason, "RESEARCH_OUTBOX_CONFLICT_OR_UNSAFE");
        return true;
    }
    if (errno != ENOENT) return OutboxFail(reason, "RESEARCH_OUTBOX_LOOKUP_FAILED");
    int raw = -1;
    std::string temporary;
    for (unsigned int attempt = 0; attempt < 64; ++attempt) {
        temporary = ".pending-" + name + "-" + std::to_string(::getpid()) + "-" +
            std::to_string(outboxTemporaryCounter.fetch_add(1));
        raw = ::openat(directory, temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (raw >= 0 || errno != EEXIST) break;
    }
    if (raw < 0) return OutboxFail(reason, "RESEARCH_OUTBOX_CREATE_FAILED");
    OutboxFd file(raw);
    OutboxTemporary cleanup(directory, temporary);
    if (::fchmod(file.Get(), 0600) != 0) return OutboxFail(reason, "RESEARCH_OUTBOX_MODE_FAILED");
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto count = ::write(file.Get(), bytes.data() + offset, bytes.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return OutboxFail(reason, "RESEARCH_OUTBOX_WRITE_FAILED");
        offset += static_cast<std::size_t>(count);
    }
    if (!SyncOutbox(file.Get())) return OutboxFail(reason, "RESEARCH_OUTBOX_SYNC_FAILED");
    // Linux no-replace rename publishes one complete single-link inode. Unlike
    // link/unlink publication, a crash cannot strand a two-link final record.
    // Unsupported kernel/filesystem semantics fail closed; never overwrite.
    if (::renameat2(directory, temporary.c_str(), directory, name.c_str(), RENAME_NOREPLACE) != 0)
        return OutboxFail(reason, "RESEARCH_OUTBOX_PUBLISH_FAILED");
    cleanup.Published();
    // Verify the published name, including single-link ownership and bytes.
    std::string published;
    if (!ReadOutbox(directory, name, published) || published != bytes)
        return OutboxFail(reason, "RESEARCH_OUTBOX_VERIFY_FAILED");
    return true;
}
} // namespace

bool NativeStrategyClient::PersistRequest(const std::string& directory, TradingToolHostRequest request,
                                          std::string& reason, const std::string& expectedBinding) const {
    const std::string capturedDirectory = directory;
    reason.clear();
    std::string binding, wire;
    if (!client_.RecoveryBinding(binding, reason)) return false;
    if (!expectedBinding.empty() && binding != expectedBinding)
        return OutboxFail(reason, "NATIVE_RECOVERY_BINDING_MISMATCH");
    if (!request.sessionToken.empty() || !OutboxMutation(request))
        return OutboxFail(reason, "RESEARCH_OUTBOX_REQUEST_INVALID");
    request.sessionToken = kOutboxToken;
    if (!TypedToolProtocol::EncodeRequest(request, wire, reason)) return false;
    const std::string bytes = "HSR1\n" + binding + "\n" +
        NativeToolDiscoveryContract::ContentDigest("HSR1\n" + binding + "\n" + wire) + "\n" + wire;
    OutboxFd dir(OpenOutbox(capturedDirectory));
    if (dir.Get() < 0) return OutboxFail(reason, "RESEARCH_OUTBOX_DIRECTORY_UNSAFE");
    if (!LockOutbox(dir.Get())) return OutboxFail(reason, "RESEARCH_OUTBOX_LOCK_FAILED");
    if (!WriteOutbox(dir.Get(), request.toolCallId + ".hsr", bytes, reason)) return false;
    if (!SyncOutbox(dir.Get()) || !SameOutboxPath(dir.Get(), capturedDirectory))
        return OutboxFail(reason, "RESEARCH_OUTBOX_SYNC_OR_PATH_FAILED");
    reason.clear(); return true;
}
bool NativeStrategyClient::Persist(const std::string& directory, const PreparedOrder& order,
    const std::string& id, const std::string& permit, std::string& reason) const {
    try { return PersistRequest(directory, order.SubmissionRequest(id, permit), reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Persist(const std::string& directory, const PreparedCancellation& cancellation,
    const std::string& id, std::string& reason) const {
    try { return PersistRequest(directory, cancellation.SubmissionRequest(id), reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::Persist(const std::string& directory, const PreparedFlatten& flatten,
    const std::string& id, const std::string& permit, std::string& reason) const {
    try { return PersistRequest(directory, flatten.SubmissionRequest(id, permit), reason); }
    catch (const std::invalid_argument& e) { reason = e.what(); return false; }
}
bool NativeStrategyClient::LoadStored(const std::string& directory, const std::string& id,
    TradingToolHostRequest& request, std::string& reason) const {
    const std::string capturedDirectory = directory, capturedId = id;
    request = TradingToolHostRequest();
    TradingToolHostRequest candidate;
    std::string binding, current;
    if (!LoadOutbox(capturedDirectory, capturedId, candidate, binding, reason) || !client_.RecoveryBinding(current, reason)) return false;
    if (current != binding) return OutboxFail(reason, "NATIVE_RECOVERY_BINDING_MISMATCH");
    request = candidate; reason.clear(); return true;
}
bool NativeStrategyClient::SubmitStored(const std::string& directory, const std::string& id,
    NativeToolClientResult& result, std::string& reason) const {
    const std::string capturedDirectory = directory, capturedId = id;
    result = NativeToolClientResult(); reason.clear();
    TradingToolHostRequest request;
    std::string binding;
    if (!LoadOutbox(capturedDirectory, capturedId, request, binding, reason)) return false;
    return client_.CallBound(request, binding, result, reason);
}

// The prepared-command facade uses the same codec, immutable record and bound
// transport as the original API. No second serializer/outbox/state authority.
bool NativeStrategyClient::PrepareRequest(TradingToolHostRequest request,
    const std::string& mutationTool, PreparedStrategyCommand& prepared,
    NativeToolClientResult& result, std::string& reason) const {
    prepared = PreparedStrategyCommand(); result = NativeToolClientResult(); reason.clear();
    std::string binding;
    if (!client_.RecoveryBinding(binding, reason)) return false;
    // Capture ONE credential for discovery and the preview call. Merely testing
    // its value before/after an unbound call would permit an A/B/A rotation race.
    if (!client_.CallBound(request, binding, result, reason)) return false;
    TypedPreviewAuthorization authorization;
    if (!TypedToolProtocol::DecodePreviewAuthorization(
            result.responseJson, request.call.name, authorization, reason)) return false;
    request.toolCallId = authorization.commandId;
    request.call.name = mutationTool;
    request.call.previewPermit = authorization.previewPermit;
    Validate(request.call);
    prepared.request_ = request;
    prepared.binding_ = binding;
    reason.clear(); return true;
}
bool NativeStrategyClient::Prepare(const PreparedOrder& order, const std::string& id,
    PreparedStrategyCommand& prepared, NativeToolClientResult& result, std::string& reason) const {
    try { return PrepareRequest(order.PreviewRequest(id), "trade.place_order", prepared, result, reason); }
    catch (const std::invalid_argument& e) {
        prepared = PreparedStrategyCommand(); result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Prepare(const PreparedFlatten& flatten, const std::string& id,
    PreparedStrategyCommand& prepared, NativeToolClientResult& result, std::string& reason) const {
    try { return PrepareRequest(flatten.PreviewRequest(id), "trade.flatten_position", prepared, result, reason); }
    catch (const std::invalid_argument& e) {
        prepared = PreparedStrategyCommand(); result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Prepare(const PreparedCancellation& cancellation, const std::string& id,
    PreparedStrategyCommand& prepared, std::string& reason) const {
    try {
        // Capture borrowed IDs before clearing a previously prepared object.
        const auto request = cancellation.SubmissionRequest(id);
        prepared = PreparedStrategyCommand(); reason.clear();
        std::string binding;
        if (!client_.RecoveryBinding(binding, reason)) return false;
        prepared.request_ = request; prepared.binding_ = binding;
        return true;
    } catch (const std::invalid_argument& e) {
        prepared = PreparedStrategyCommand(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::Persist(const std::string& directory,
    PreparedStrategyCommand& prepared, std::string& reason) const {
    const std::string capturedDirectory = directory;
    prepared.durable_ = false; prepared.directory_.clear(); reason.clear();
    if (!prepared.Ready() || prepared.binding_.empty())
        return OutboxFail(reason, "RESEARCH_OUTBOX_UNPREPARED");
    if (!PersistRequest(capturedDirectory, prepared.request_, reason, prepared.binding_)) return false;
    prepared.directory_ = capturedDirectory; prepared.durable_ = true;
    return true;
}
bool NativeStrategyClient::Restore(const std::string& directory, const std::string& id,
    PreparedStrategyCommand& prepared, std::string& reason) const {
    const std::string capturedDirectory = directory, capturedId = id;
    prepared = PreparedStrategyCommand(); reason.clear();
    TradingToolHostRequest request;
    std::string binding, current;
    if (!LoadOutbox(capturedDirectory, capturedId, request, binding, reason) ||
        !client_.RecoveryBinding(current, reason)) return false;
    if (current != binding) return OutboxFail(reason, "NATIVE_RECOVERY_BINDING_MISMATCH");
    prepared.request_ = request; prepared.binding_ = binding;
    prepared.directory_ = capturedDirectory; prepared.durable_ = true;
    return true;
}
bool NativeStrategyClient::LoadPrepared(const PreparedStrategyCommand& prepared,
    TradingToolHostRequest& request, std::string& binding, std::string& reason) const {
    request = TradingToolHostRequest(); binding.clear(); reason.clear();
    if (!prepared.Ready() || !prepared.Durable() || prepared.directory_.empty())
        return OutboxFail(reason, "RESEARCH_OUTBOX_NOT_DURABLE");
    if (!LoadOutbox(prepared.directory_, prepared.CommandId(), request, binding, reason)) return false;
    if (binding != prepared.binding_) return OutboxFail(reason, "NATIVE_RECOVERY_BINDING_MISMATCH");
    // A previously durable in-memory object must not silently submit changed
    // bytes under the same filename. Read once and compare for either action.
    auto expected = prepared.request_;
    auto actual = request;
    expected.sessionToken = actual.sessionToken = kOutboxToken;
    std::string expectedWire, actualWire;
    if (!TypedToolProtocol::EncodeRequest(expected, expectedWire, reason) ||
        !TypedToolProtocol::EncodeRequest(actual, actualWire, reason)) return false;
    if (expectedWire != actualWire) return OutboxFail(reason, "RESEARCH_OUTBOX_PREPARED_MISMATCH");
    return true;
}

bool NativeStrategyClient::Submit(const PreparedStrategyCommand& prepared,
    NativeToolClientResult& result, std::string& reason) const {
    result = NativeToolClientResult(); reason.clear();
    TradingToolHostRequest request;
    std::string binding;
    if (!LoadPrepared(prepared, request, binding, reason)) return false;
    return client_.CallBound(request, binding, result, reason);
}
bool NativeStrategyClient::InspectBound(const std::string& id, const std::string& queryId,
    const std::string& binding, NativeToolClientResult& result, std::string& reason) const {
    try {
        CheckId(id); CheckId(queryId);
        if (id == queryId) return OutboxFail(reason, "RESEARCH_INSPECTION_QUERY_ID_REUSED");
        TradingToolHostRequest query;
        query.toolCallId = queryId; query.call.name = "execution.get_command_status";
        query.call.targetCommandId = id; Validate(query.call);
        // A token-file credential is captured once by CallBound for discovery
        // and forwarding. Do not fall back to the generic unbound Status API.
        return client_.CallBound(query, binding, result, reason);
    } catch (const std::invalid_argument& e) {
        result = NativeToolClientResult(); reason = e.what(); return false;
    }
}
bool NativeStrategyClient::InspectStored(const std::string& directory, const std::string& id,
    const std::string& queryId, NativeToolClientResult& result, std::string& reason) const {
    const std::string capturedDirectory = directory, capturedId = id, capturedQueryId = queryId;
    result = NativeToolClientResult(); reason.clear();
    TradingToolHostRequest original;
    std::string binding;
    if (!LoadOutbox(capturedDirectory, capturedId, original, binding, reason)) return false;
    return InspectBound(original.toolCallId, capturedQueryId, binding, result, reason);
}
bool NativeStrategyClient::Inspect(const PreparedStrategyCommand& prepared, const std::string& queryId,
    NativeToolClientResult& result, std::string& reason) const {
    const std::string capturedQueryId = queryId;
    result = NativeToolClientResult(); reason.clear();
    TradingToolHostRequest original;
    std::string binding;
    if (!LoadPrepared(prepared, original, binding, reason)) return false;
    return InspectBound(original.toolCallId, capturedQueryId, binding, result, reason);
}

}} // namespace hepta::research
