#pragma once

#include "../HeptaTrade/execution/historical_command_index.h"

#include <cassert>
#include <cstdlib>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

namespace {

std::string TempHistoricalCommandIndexDir()
{
    char path[] = "/tmp/hepta-historical-index-XXXXXX";
    char* created = ::mkdtemp(path);
    assert(created != nullptr);
    const std::string root(created);
    assert(::rmdir(root.c_str()) == 0); // Init owns creation and fsync semantics.
    return root;
}

void RemoveHistoricalCommandIndexFixture(const std::string& root,
                                         const std::vector<std::string>& paths)
{
    for (const auto& path : paths) ::unlink(path.c_str());
    ::rmdir(root.c_str());
}

HistoricalCommandIndexRecord HistoricalRecord(const std::string& command,
                                               const std::string& hash,
                                               ExecutionCommandStatus status)
{
    HistoricalCommandIndexRecord record;
    record.agentId = "agent-a";
    record.sessionId = "session-a";
    record.commandId = command;
    record.requestHash = hash;
    record.operation = "place";
    record.status = status;
    record.orderId = status == ExecutionCommandStatus::Accepted ? 77 : -1;
    record.reasonCode = status == ExecutionCommandStatus::Uncertain ?
        "RECOVERY_RECONCILE_REQUIRED" : "AUTHORITATIVE_CORRELATION_CONFIRMED";
    record.detail = "fixture detail";
    return record;
}

void TestHistoricalCommandIndexDurabilityAndFullIdentity()
{
    const std::string root = TempHistoricalCommandIndexDir();
    HistoricalCommandIndex index(root);
    std::string reason;
    assert(index.Init(reason));

    HistoricalCommandIndexRecord found;
    assert(index.Lookup("agent-a", "session-a", "missing", found, reason) ==
        HistoricalCommandIndexLookup::Missing);

    HistoricalCommandIndexRecord record = HistoricalRecord(
        "old-command", "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ExecutionCommandStatus::Uncertain);
    assert(index.Put(record, reason));
    const std::string path = index.PathForIdentity(
        record.agentId, record.sessionId, record.commandId);
    assert(index.Lookup(record.agentId, record.sessionId, record.commandId, found, reason) ==
        HistoricalCommandIndexLookup::Found);
    assert(found.requestHash == record.requestHash);
    assert(found.status == ExecutionCommandStatus::Uncertain);
    assert(found.reasonCode == "RECOVERY_RECONCILE_REQUIRED");

    // Same exact durable identity may advance from uncertain to a later
    // authoritative terminal outcome. Different payload hash never overwrites.
    record.status = ExecutionCommandStatus::Accepted;
    record.orderId = 77;
    record.reasonCode = "AUTHORITATIVE_CORRELATION_CONFIRMED";
    assert(index.Put(record, reason));
    assert(index.Lookup(record.agentId, record.sessionId, record.commandId, found, reason) ==
        HistoricalCommandIndexLookup::Found);
    assert(found.status == ExecutionCommandStatus::Accepted && found.orderId == 77);
    HistoricalCommandIndexRecord conflict = record;
    conflict.requestHash = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    assert(!index.Put(conflict, reason));
    assert(reason == "HISTORICAL_INDEX_IDEMPOTENCY_CONFLICT");

    RemoveHistoricalCommandIndexFixture(root, {path});
}

void TestHistoricalCommandIndexCorruptionAndCollisionFailClosed()
{
    const std::string root = TempHistoricalCommandIndexDir();
    HistoricalCommandIndex index(root);
    std::string reason;
    assert(index.Init(reason));

    HistoricalCommandIndexRecord first = HistoricalRecord(
        "first", "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        ExecutionCommandStatus::Accepted);
    HistoricalCommandIndexRecord second = HistoricalRecord(
        "second", "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        ExecutionCommandStatus::Rejected);
    assert(index.Put(first, reason));
    const std::string firstPath = index.PathForIdentity(first.agentId, first.sessionId, first.commandId);
    const std::string secondPath = index.PathForIdentity(second.agentId, second.sessionId, second.commandId);

    // Moving a valid record to another key's digest path simulates a digest
    // collision/path substitution. Lookup compares the complete stored key.
    assert(::rename(firstPath.c_str(), secondPath.c_str()) == 0);
    HistoricalCommandIndexRecord found;
    assert(index.Lookup(second.agentId, second.sessionId, second.commandId, found, reason) ==
        HistoricalCommandIndexLookup::Collision);
    assert(reason == "HISTORICAL_INDEX_HASH_COLLISION");
    assert(::rename(secondPath.c_str(), firstPath.c_str()) == 0);

    // Corrupt bytes are not interpreted as a missing old command.
    const int fd = ::open(firstPath.c_str(), O_WRONLY | O_TRUNC | O_CLOEXEC);
    assert(fd >= 0);
    const char bad[] = "HCI1\ncorrupt\n";
    assert(::write(fd, bad, sizeof(bad) - 1) == static_cast<ssize_t>(sizeof(bad) - 1));
    assert(::fsync(fd) == 0);
    assert(::close(fd) == 0);
    assert(index.Lookup(first.agentId, first.sessionId, first.commandId, found, reason) ==
        HistoricalCommandIndexLookup::Corrupt);
    assert(!reason.empty());

    RemoveHistoricalCommandIndexFixture(root, {firstPath, secondPath});
}

void TestHistoricalCommandIndexRejectsUnsafeNamespace()
{
    const std::string target = TempHistoricalCommandIndexDir();
    assert(::mkdir(target.c_str(), 0700) == 0);
    const std::string link = target + ".link";
    assert(::symlink(target.c_str(), link.c_str()) == 0);
    HistoricalCommandIndex unsafe(link);
    std::string reason;
    assert(!unsafe.Init(reason));
    assert(reason == "HISTORICAL_INDEX_ROOT_UNSAFE");
    assert(::unlink(link.c_str()) == 0);
    assert(::rmdir(target.c_str()) == 0);
}

void TestHistoricalCommandIndexCases()
{
    TestHistoricalCommandIndexDurabilityAndFullIdentity();
    TestHistoricalCommandIndexCorruptionAndCollisionFailClosed();
    TestHistoricalCommandIndexRejectsUnsafeNamespace();
}

} // namespace
