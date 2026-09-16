"""Compile and execute the disk-backed historical command identity contract.

This is a real C++/filesystem test, not a source-token assertion. Coordinator
integration remains separate: this test proves the persistent lookup primitive
fails closed on corruption, path substitution and hash-path collision.
"""
from __future__ import annotations

from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

CPP = r'''
#include "HeptaTrade/execution/historical_command_index.h"
#include <cassert>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

static std::string make_root() {
    char path[] = "/tmp/hepta-hci-XXXXXX";
    char* made = ::mkdtemp(path);
    assert(made != nullptr);
    std::string root(made);
    assert(::rmdir(root.c_str()) == 0);
    return root;
}

static HistoricalCommandIndexRecord record(const std::string& id,
                                           const std::string& hash,
                                           ExecutionCommandStatus status) {
    HistoricalCommandIndexRecord value;
    value.agentId = "agent-a";
    value.sessionId = "session-a";
    value.commandId = id;
    value.requestHash = hash;
    value.operation = "place";
    value.status = status;
    value.orderId = status == ExecutionCommandStatus::Accepted ? 77 : -1;
    value.reasonCode = status == ExecutionCommandStatus::Uncertain ?
        "RECOVERY_RECONCILE_REQUIRED" : "AUTHORITATIVE_CORRELATION_CONFIRMED";
    value.detail = "fixture detail";
    return value;
}

int main() {
    {
        const std::string root = make_root();
        HistoricalCommandIndex index(root);
        std::string reason;
        assert(index.Init(reason));
        HistoricalCommandIndexRecord found;
        assert(index.Lookup("agent-a", "session-a", "missing", found, reason) ==
               HistoricalCommandIndexLookup::Missing);
        auto value = record("old-command",
            "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ExecutionCommandStatus::Uncertain);
        assert(index.Put(value, reason));
        const std::string path = index.PathForIdentity(value.agentId, value.sessionId, value.commandId);
        assert(index.Lookup(value.agentId, value.sessionId, value.commandId, found, reason) ==
               HistoricalCommandIndexLookup::Found);
        assert(found.requestHash == value.requestHash &&
               found.status == ExecutionCommandStatus::Uncertain);
        value.status = ExecutionCommandStatus::Accepted;
        value.orderId = 77;
        value.reasonCode = "AUTHORITATIVE_CORRELATION_CONFIRMED";
        assert(index.Put(value, reason));
        assert(index.Lookup(value.agentId, value.sessionId, value.commandId, found, reason) ==
               HistoricalCommandIndexLookup::Found);
        assert(found.status == ExecutionCommandStatus::Accepted && found.orderId == 77);
        auto conflict = value;
        conflict.requestHash =
            "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        assert(!index.Put(conflict, reason));
        assert(reason == "HISTORICAL_INDEX_IDEMPOTENCY_CONFLICT");
        assert(::unlink(path.c_str()) == 0);
        assert(::rmdir(root.c_str()) == 0);
    }
    {
        const std::string root = make_root();
        HistoricalCommandIndex index(root);
        std::string reason;
        assert(index.Init(reason));
        auto first = record("first",
            "sha256:1111111111111111111111111111111111111111111111111111111111111111",
            ExecutionCommandStatus::Accepted);
        auto second = record("second",
            "sha256:2222222222222222222222222222222222222222222222222222222222222222",
            ExecutionCommandStatus::Rejected);
        assert(index.Put(first, reason));
        const std::string first_path = index.PathForIdentity(first.agentId, first.sessionId, first.commandId);
        const std::string second_path = index.PathForIdentity(second.agentId, second.sessionId, second.commandId);
        assert(::rename(first_path.c_str(), second_path.c_str()) == 0);
        HistoricalCommandIndexRecord found;
        assert(index.Lookup(second.agentId, second.sessionId, second.commandId, found, reason) ==
               HistoricalCommandIndexLookup::Collision);
        assert(reason == "HISTORICAL_INDEX_HASH_COLLISION");
        assert(::rename(second_path.c_str(), first_path.c_str()) == 0);
        const int fd = ::open(first_path.c_str(), O_WRONLY | O_TRUNC | O_CLOEXEC);
        assert(fd >= 0);
        const char bad[] = "HCI1\ncorrupt\n";
        assert(::write(fd, bad, sizeof(bad) - 1) == static_cast<ssize_t>(sizeof(bad) - 1));
        assert(::fsync(fd) == 0 && ::close(fd) == 0);
        assert(index.Lookup(first.agentId, first.sessionId, first.commandId, found, reason) ==
               HistoricalCommandIndexLookup::Corrupt);
        assert(!reason.empty());
        assert(::unlink(first_path.c_str()) == 0);
        assert(::rmdir(root.c_str()) == 0);
    }
    {
        const std::string target = make_root();
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
    return 0;
}
'''


class HistoricalCommandIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="hepta-historical-index-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source = root / "index_contract.cpp"
        cls.binary = root / "index_contract"
        source.write_text(CPP, encoding="utf-8")
        compiler = shlex.split(os.environ.get("CXX", "g++"))
        if not compiler:
            raise ValueError("CXX must name a compiler")
        subprocess.run(
            compiler + [
                "-std=c++11", "-Wall", "-Wextra", "-Werror", "-pthread",
                "-I", str(ROOT), str(source), "-lcrypto", "-o", str(cls.binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_full_key_atomic_index_contract(self) -> None:
        subprocess.run([str(self.binary)], check=True, capture_output=True, text=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
