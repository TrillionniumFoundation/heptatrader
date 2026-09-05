#include "ib_paper_execution_runtime_internal.h"

#include <cerrno>
#include <cmath>
#include <cstdlib>
#include <fcntl.h>
#include <iomanip>
#include <limits>
#include <locale>
#include <map>
#include <sstream>
#include <vector>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

using namespace ib_paper_execution_runtime_internal;

namespace
{
bool CanonicalFloating(const std::string& value)
{
    if (value.empty()) return false;
    std::size_t offset = value[0] == '-' ? 1u : 0u;
    if (offset == value.size()) return false;
    if (value[offset] == '0')
    {
        ++offset;
        if (offset < value.size() && value[offset] >= '0' &&
            value[offset] <= '9') return false;
    }
    else
    {
        if (value[offset] < '1' || value[offset] > '9') return false;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
    }
    if (offset < value.size() && value[offset] == '.')
    {
        ++offset;
        const std::size_t fractionStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == fractionStart) return false;
    }
    if (offset < value.size() &&
        (value[offset] == 'e' || value[offset] == 'E'))
    {
        ++offset;
        if (offset < value.size() &&
            (value[offset] == '+' || value[offset] == '-')) ++offset;
        const std::size_t exponentStart = offset;
        while (offset < value.size() && value[offset] >= '0' &&
               value[offset] <= '9') ++offset;
        if (offset == exponentStart) return false;
    }
    return offset == value.size();
}

bool ParseClassicDouble(const std::string& value, double& parsed)
{
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    input >> std::noskipws >> parsed;
    return input && input.eof() && std::isfinite(parsed);
}
}

bool IbPaperExecutionRuntimeComposition::PreparePrivateState(std::string& reason)
{
    struct stat directory;
    if (::lstat(m_config.stateDirectory.c_str(), &directory) != 0 ||
        !S_ISDIR(directory.st_mode) || directory.st_uid != ::geteuid() ||
        (directory.st_mode & 0777) != 0700)
    { reason = "IB_PAPER_STATE_DIRECTORY_UNSAFE"; return false; }
    const std::string lockPath = m_config.stateDirectory + "/ib-paper-runtime.lock";
    m_stateLockFd = ::open(lockPath.c_str(), O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (m_stateLockFd < 0 || ::fchmod(m_stateLockFd, 0600) != 0 ||
        ::flock(m_stateLockFd, LOCK_EX | LOCK_NB) != 0)
    {
        if (m_stateLockFd >= 0) ::close(m_stateLockFd);
        m_stateLockFd = -1;
        reason = "IB_PAPER_STATE_LOCK_UNAVAILABLE";
        return false;
    }
    return ValidateOrCreatePrivateFile(m_config.journalPath, reason);
}
bool IbPaperExecutionRuntimeComposition::ValidateFxCashBaselineRecords(
    const std::map<std::string, IbPaperFxCashBaseline>& records) const
{
    const auto validProof = [](const std::string& proof) {
        if (proof.size() != 71 || proof.compare(0, 7, "sha256:") != 0)
            return false;
        for (std::size_t i = 7; i < proof.size(); ++i)
            const unsigned char character =
                static_cast<unsigned char>(proof[i]);
            const bool digit = character >= static_cast<unsigned char>('0') &&
                character <= static_cast<unsigned char>('9');
            const bool lower = character >= static_cast<unsigned char>('a') &&
                character <= static_cast<unsigned char>('f');
            const bool upper = character >= static_cast<unsigned char>('A') &&
                character <= static_cast<unsigned char>('F');
            if (!digit && !lower && !upper)
                return false;
        return true;
    };
    if (records.size() != m_config.quoteContracts.size()) return false;
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_config.quoteContracts.begin();
         it != m_config.quoteContracts.end(); ++it)
    {
        const std::map<std::string, IbPaperFxCashBaseline>::const_iterator
            found = records.find(it->first);
        if (found == records.end()) return false;
        const IbPaperFxCashBaseline& baseline = found->second;
        if (baseline.account != m_config.profile.account ||
            baseline.instrument != it->first ||
            baseline.currency != it->second.symbol ||
            !std::isfinite(baseline.baselineCashBalance) ||
            !std::isfinite(baseline.observedCashBalance) ||
            !std::isfinite(baseline.campaignExecutionDelta) ||
            baseline.observedAtMs == 0 || !validProof(baseline.proof) ||
            !std::isfinite(baseline.baselineCashBalance +
                baseline.campaignExecutionDelta) ||
            std::fabs((baseline.baselineCashBalance +
                baseline.campaignExecutionDelta) -
                baseline.observedCashBalance) > 1e-6)
            return false;
    }
    return true;
}

bool IbPaperExecutionRuntimeComposition::LoadFxCashBaselines(
    std::string& reason)
{
    if (!m_config.fxCashBaselines.empty()) {
        if (!ValidateFxCashBaselineRecords(m_config.fxCashBaselines)) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        reason.clear();
        return true;
    }

    const std::string& path = m_config.fxCashBaselineCredentialPath;
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        reason = errno == ENOENT ? "IB_FX_CASH_BASELINE_MISSING" :
            "IB_FX_CASH_BASELINE_UNSAFE";
        return false;
    }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0) {
        ::close(fd);
        reason = "IB_FX_CASH_BASELINE_UNSAFE";
        return false;
    }
    const bool privateSourceMode =
        (metadata.st_mode & 07777) == 0400 &&
        metadata.st_uid == ::geteuid();
    const bool systemdCredentialMode =
        (metadata.st_mode & 07777) == 0440 &&
        metadata.st_uid == 0 && metadata.st_gid == 0;
    if (!S_ISREG(metadata.st_mode) ||
        (!privateSourceMode && !systemdCredentialMode) ||
        metadata.st_nlink != 1 ||
        metadata.st_size <= 0 || metadata.st_size > 16384) {
        ::close(fd);
        reason = "IB_FX_CASH_BASELINE_UNSAFE";
        return false;
    }
    std::string contents(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < contents.size()) {
        const ssize_t count =
            ::read(fd, &contents[offset], contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) {
            ::close(fd);
            reason = "IB_FX_CASH_BASELINE_READ_FAILED";
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    if (::close(fd) != 0) {
        reason = "IB_FX_CASH_BASELINE_READ_FAILED";
        return false;
    }

    std::istringstream input(contents);
    std::string line;
    if (!std::getline(input, line) || line != "HFX1") {
        reason = "IB_FX_CASH_BASELINE_INVALID";
        return false;
    }
    std::map<std::string, IbPaperFxCashBaseline> parsed;
    while (std::getline(input, line)) {
        if (line.empty()) continue;
        std::vector<std::string> fields;
        std::size_t begin = 0;
        while (begin <= line.size()) {
            const std::size_t separator = line.find('|', begin);
            fields.push_back(line.substr(begin,
                separator == std::string::npos ? separator :
                    separator - begin));
            if (separator == std::string::npos) break;
            begin = separator + 1;
        }
        if (fields.size() != 8 || fields[0] != m_config.profile.account ||
            fields[1].empty() || fields[2].empty()) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        const std::size_t proofSeparator = line.rfind('|');
        if (proofSeparator == std::string::npos ||
            Sha256Text(line.substr(0, proofSeparator)) != fields[7]) {
            reason = "IB_FX_CASH_BASELINE_PROOF_MISMATCH";
            return false;
        }
        if (!CanonicalFloating(fields[3])) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        double baseline = 0.0;
        if (!ParseClassicDouble(fields[3], baseline)) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        if (!CanonicalFloating(fields[4])) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        double observed = 0.0;
        if (!ParseClassicDouble(fields[4], observed)) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        if (!CanonicalFloating(fields[5])) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        double delta = 0.0;
        if (!ParseClassicDouble(fields[5], delta)) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        std::uint64_t observedAtMs = 0;
        if (!ParsePositiveUnsigned(fields[6], observedAtMs)) {
            reason = "IB_FX_CASH_BASELINE_INVALID";
            return false;
        }
        IbPaperFxCashBaseline record;
        record.account = fields[0];
        record.instrument = fields[1];
        record.currency = fields[2];
        record.baselineCashBalance = baseline;
        record.observedCashBalance = observed;
        record.campaignExecutionDelta = delta;
        record.observedAtMs = observedAtMs;
        record.proof = fields[7];
        if (!parsed.insert(std::make_pair(record.instrument, record)).second) {
            reason = "IB_FX_CASH_BASELINE_AMBIGUOUS";
            return false;
        }
    }
    if (!ValidateFxCashBaselineRecords(parsed)) {
        reason = "IB_FX_CASH_BASELINE_INVALID";
        return false;
    }
    m_config.fxCashBaselines = parsed;
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::LoadFxCashRestartCheckpoint(
    std::string& reason)
{
    // The root/operator supplied HFX1 credential remains the immutable
    // campaign baseline.  This execution-owned checkpoint may advance only
    // the one-shot startup observation after a broker-proven campaign fill.
    const std::string path = m_config.stateDirectory + "/" +
        kFxCashRestartCheckpointFile;
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0)
    {
        if (errno == ENOENT)
        {
            reason.clear();
            return true;
        }
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_UNSAFE";
        return false;
    }
    struct stat metadata;
    if (::fstat(fd, &metadata) != 0 ||
        !S_ISREG(metadata.st_mode) || metadata.st_uid != ::geteuid() ||
        (metadata.st_mode & 07777) != 0600 || metadata.st_nlink != 1 ||
        metadata.st_size <= 0 || metadata.st_size > 16384)
    {
        ::close(fd);
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_UNSAFE";
        return false;
    }
    std::string contents(static_cast<std::size_t>(metadata.st_size), '\0');
    std::size_t offset = 0;
    while (offset < contents.size())
    {
        const ssize_t count =
            ::read(fd, &contents[offset], contents.size() - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0)
        {
            ::close(fd);
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_READ_FAILED";
            return false;
        }
        offset += static_cast<std::size_t>(count);
    }
    if (::close(fd) != 0)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_READ_FAILED";
        return false;
    }

    std::istringstream input(contents);
    std::string line;
    if (!std::getline(input, line) || line != "HFXR1")
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
        return false;
    }
    std::map<std::string, IbPaperFxCashBaseline> advanced;
    while (std::getline(input, line))
    {
        if (line.empty()) continue;
        std::vector<std::string> fields;
        std::size_t begin = 0;
        while (begin <= line.size())
        {
            const std::size_t separator = line.find('|', begin);
            fields.push_back(line.substr(begin,
                separator == std::string::npos ? separator :
                    separator - begin));
            if (separator == std::string::npos) break;
            begin = separator + 1;
        }
        if (fields.size() != 9 ||
            Sha256Text(line.substr(0, line.rfind('|'))) != fields[8])
        {
            reason = fields.size() == 9 ?
                "IB_FX_CASH_RESTART_CHECKPOINT_PROOF_MISMATCH" :
                "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        const std::map<std::string, IbPaperFxCashBaseline>::const_iterator
            original = m_config.fxCashBaselines.find(fields[1]);
        const std::map<std::string, InstrumentRef>::const_iterator contract =
            m_config.quoteContracts.find(fields[1]);
        if (original == m_config.fxCashBaselines.end() ||
            contract == m_config.quoteContracts.end() ||
            contract->second.secType != "CASH" ||
            fields[0] != original->second.account ||
            fields[1] != original->second.instrument ||
            fields[2] != original->second.currency)
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        if (!CanonicalFloating(fields[3])) {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        double baseline = 0.0;
        if (!ParseClassicDouble(fields[3], baseline))
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        if (!CanonicalFloating(fields[4])) {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        double observed = 0.0;
        if (!ParseClassicDouble(fields[4], observed))
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        if (!CanonicalFloating(fields[5])) {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        double delta = 0.0;
        if (!ParseClassicDouble(fields[5], delta))
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        std::uint64_t observedAtMs = 0;
        if (!ParsePositiveUnsigned(fields[6], observedAtMs))
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
            return false;
        }
        if (observedAtMs < original->second.observedAtMs)
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_STALE";
            return false;
        }
        if (fields[7] != original->second.proof ||
            std::fabs(baseline - original->second.baselineCashBalance) > 1e-6 ||
            !std::isfinite(baseline + delta) ||
            std::fabs((baseline + delta) - observed) > 1e-6)
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_ANCHOR_MISMATCH";
            return false;
        }
        IbPaperFxCashBaseline checkpoint = original->second;
        checkpoint.observedCashBalance = observed;
        checkpoint.campaignExecutionDelta = delta;
        checkpoint.observedAtMs = observedAtMs;
        if (!advanced.insert(std::make_pair(
                checkpoint.instrument, checkpoint)).second)
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_AMBIGUOUS";
            return false;
        }
    }
    std::size_t expectedRecords = 0;
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_config.quoteContracts.begin();
         it != m_config.quoteContracts.end(); ++it)
    {
        if (it->second.secType == "CASH") ++expectedRecords;
    }
    if (advanced.size() != expectedRecords)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
        return false;
    }
    for (std::map<std::string, IbPaperFxCashBaseline>::const_iterator it =
             advanced.begin(); it != advanced.end(); ++it)
        m_config.fxCashBaselines[it->first] = it->second;
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::PersistFxCashRestartCheckpoint(
    std::string& reason)
{
    if (!m_adapter)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_SNAPSHOT_INCOMPLETE";
        return false;
    }
    const IBAuthoritativeRiskSnapshot risk =
        m_adapter->GetAuthoritativeRiskSnapshot();
    const std::map<std::string, IBAuthoritativeFxCashExposure> exposures =
        m_adapter->GetAuthoritativeFxCashExposures();
    if (!risk.accountComplete || !risk.positionsComplete ||
        !risk.fxCashComplete || risk.connectionEpoch == 0 ||
        risk.accountGeneration == 0 || risk.positionsGeneration == 0 ||
        risk.fxCashGeneration == 0)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_SNAPSHOT_INCOMPLETE";
        return false;
    }

    const std::uint64_t observedAtMs = NowEpochMs();
    std::map<std::string, IbPaperFxCashBaseline> advanced;
    std::ostringstream output;
    output.imbue(std::locale::classic());
    output << "HFXR1\n" << std::setprecision(
        std::numeric_limits<double>::max_digits10);
    for (std::map<std::string, InstrumentRef>::const_iterator it =
             m_config.quoteContracts.begin();
         it != m_config.quoteContracts.end(); ++it)
    {
        if (it->second.secType != "CASH") continue;
        const std::map<std::string, IbPaperFxCashBaseline>::const_iterator
            original = m_config.fxCashBaselines.find(it->first);
        const std::map<std::string, IBAuthoritativeFxCashExposure>::const_iterator
            exposure = exposures.find(it->first);
        double position = 0.0;
        std::string positionReason;
        if (original == m_config.fxCashBaselines.end() ||
            exposure == exposures.end() ||
            exposure->second.instrument != it->first ||
            exposure->second.baseCurrency != original->second.currency ||
            !std::isfinite(exposure->second.baselineCashBalance) ||
            !std::isfinite(exposure->second.currentCashBalance) ||
            !std::isfinite(exposure->second.campaignOwnedQuantity) ||
            std::fabs(exposure->second.baselineCashBalance -
                original->second.baselineCashBalance) > 1e-6 ||
            std::fabs((exposure->second.currentCashBalance -
                original->second.baselineCashBalance) -
                exposure->second.campaignOwnedQuantity) > 1e-6 ||
            !m_adapter->ResolveAuthoritativePositionQuantity(
                it->first, it->second, position, positionReason) ||
            std::fabs(position -
                exposure->second.campaignOwnedQuantity) > 1e-6)
        {
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_SNAPSHOT_INCOHERENT";
            return false;
        }
        IbPaperFxCashBaseline checkpoint = original->second;
        checkpoint.observedCashBalance =
            exposure->second.currentCashBalance;
        checkpoint.campaignExecutionDelta =
            exposure->second.campaignOwnedQuantity;
        checkpoint.observedAtMs = std::max(
            observedAtMs, original->second.observedAtMs);
        std::ostringstream signedFields;
        signedFields.imbue(std::locale::classic());
        signedFields << std::setprecision(
            std::numeric_limits<double>::max_digits10)
            << checkpoint.account << '|' << checkpoint.instrument << '|'
            << checkpoint.currency << '|' << checkpoint.baselineCashBalance
            << '|' << checkpoint.observedCashBalance << '|'
            << checkpoint.campaignExecutionDelta << '|'
            << checkpoint.observedAtMs << '|' << checkpoint.proof;
        const std::string signedRecord = signedFields.str();
        output << signedRecord << '|' << Sha256Text(signedRecord) << '\n';
        advanced[it->first] = checkpoint;
    }
    if (advanced.empty() || output.str().size() > 16384)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_INVALID";
        return false;
    }

    const int directoryFd = ::open(m_config.stateDirectory.c_str(),
        O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    struct stat directoryMetadata;
    if (directoryFd < 0 || ::fstat(directoryFd, &directoryMetadata) != 0 ||
        !S_ISDIR(directoryMetadata.st_mode) ||
        directoryMetadata.st_uid != ::geteuid() ||
        (directoryMetadata.st_mode & 0777) != 0700)
    {
        if (directoryFd >= 0) ::close(directoryFd);
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_UNSAFE";
        return false;
    }
    struct stat existing;
    if (::fstatat(directoryFd, kFxCashRestartCheckpointFile, &existing,
                  AT_SYMLINK_NOFOLLOW) == 0)
    {
        if (!S_ISREG(existing.st_mode) || existing.st_uid != ::geteuid() ||
            (existing.st_mode & 07777) != 0600 || existing.st_nlink != 1)
        {
            ::close(directoryFd);
            reason = "IB_FX_CASH_RESTART_CHECKPOINT_UNSAFE";
            return false;
        }
    }
    else if (errno != ENOENT)
    {
        ::close(directoryFd);
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_UNSAFE";
        return false;
    }

    std::string temporaryName;
    int checkpointFd = -1;
    for (int attempt = 0; attempt < 16 && checkpointFd < 0; ++attempt)
    {
        temporaryName = std::string(".ib-fx-cash-restart-attestation.tmp.") +
            std::to_string(static_cast<unsigned long>(::getpid())) + "." +
            std::to_string(observedAtMs) + "." + std::to_string(attempt);
        checkpointFd = ::openat(directoryFd, temporaryName.c_str(),
            O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        if (checkpointFd < 0 && errno != EEXIST) break;
    }
    if (checkpointFd < 0)
    {
        ::close(directoryFd);
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_WRITE_FAILED";
        return false;
    }
    const std::string serialized = output.str();
    std::size_t written = 0;
    bool writeOk = ::fchmod(checkpointFd, 0600) == 0;
    while (writeOk && written < serialized.size())
    {
        const ssize_t count = ::write(checkpointFd,
            serialized.data() + written, serialized.size() - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) writeOk = false;
        else written += static_cast<std::size_t>(count);
    }
    struct stat writtenMetadata;
    writeOk = writeOk && ::fsync(checkpointFd) == 0 &&
        ::fstat(checkpointFd, &writtenMetadata) == 0 &&
        S_ISREG(writtenMetadata.st_mode) &&
        writtenMetadata.st_uid == ::geteuid() &&
        (writtenMetadata.st_mode & 07777) == 0600 &&
        writtenMetadata.st_nlink == 1;
    if (::close(checkpointFd) != 0) writeOk = false;
    bool renamed = false;
    if (writeOk)
    {
        renamed = ::renameat(directoryFd, temporaryName.c_str(),
            directoryFd, kFxCashRestartCheckpointFile) == 0;
        writeOk = renamed && ::fsync(directoryFd) == 0;
    }
    if (!renamed)
        ::unlinkat(directoryFd, temporaryName.c_str(), 0);
    if (::close(directoryFd) != 0) writeOk = false;
    if (!writeOk)
    {
        reason = "IB_FX_CASH_RESTART_CHECKPOINT_WRITE_FAILED";
        return false;
    }
    for (std::map<std::string, IbPaperFxCashBaseline>::const_iterator it =
             advanced.begin(); it != advanced.end(); ++it)
        m_config.fxCashBaselines[it->first] = it->second;
    reason.clear();
    return true;
}

bool IbPaperExecutionRuntimeComposition::LoadFenceCredential(std::string& reason)
{
    std::string contents;
    if (!ReadSmallPrivateFile(m_config.fenceCredentialPath, contents, reason)) return false;
    std::istringstream input(contents);
    std::string header, tokenLine, generationLine, extra;
    if (!std::getline(input, header) || header != "HFC1" ||
        !std::getline(input, tokenLine) || !std::getline(input, generationLine) ||
        std::getline(input, extra) || tokenLine.compare(0, 14, "fencing_token=") != 0 ||
        generationLine.compare(0, 11, "generation=") != 0 ||
        !ParsePositiveUnsigned(tokenLine.substr(14), m_fencingToken) ||
        !ParsePositiveUnsigned(generationLine.substr(11), m_fencingGeneration))
    { reason = "IB_PAPER_FENCE_CREDENTIAL_INVALID"; return false; }
    return true;
}
