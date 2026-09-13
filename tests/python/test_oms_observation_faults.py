"""Exercise a real journal fdatasync failure with a linker-only test seam."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = r'''
#include "oms_journal.h"
#include <cerrno>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <unistd.h>
extern "C" int __real_fdatasync(int);
static bool fail_sync = false;
extern "C" int __wrap_fdatasync(int fd) {
    if (fail_sync) { errno=EIO; return -1; }
    return __real_fdatasync(fd);
}
void require(bool value) { if (!value) throw std::runtime_error("sync failure contract"); }
int main(int argc, char** argv) {
    require(argc==2);
    setenv("HEPTA_OMS_ASYNC_FLUSH", "0", 1);
    setenv("HEPTA_OMS_SYNC_CRITICAL", "1", 1);
    OmsJournal journal; require(journal.Init(argv[1]));
    OmsJournalEvent event; event.eventType="order_intent"; event.reqId="failed-sync-fixture";
    auto before=journal.GetHealthSnapshot(); fail_sync=true;
    require(!journal.Append(event)); auto after=journal.GetHealthSnapshot();
    require(after.writePoisoned && !after.capacityKnown);
    require(after.durableSyncFailures==before.durableSyncFailures+1);
    require(after.dataSyncLatency.samples==before.dataSyncLatency.samples+1);
    require(after.appendLatency.samples==before.appendLatency.samples+1);
    require(after.dataSyncLatency.totalNs>=after.dataSyncLatency.maximumNs);
    fail_sync=false; require(!journal.Append(event));
    require(journal.Replay({})==-1); // metrics never clear a poison fence
}
'''

class OmsObservationFaultTests(unittest.TestCase):
    def test_failed_sync_is_measured_without_claiming_durability(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hepta-sync-fault-") as folder:
            directory = Path(folder)
            source = directory / "main.cpp"
            source.write_text(SOURCE, encoding="utf-8")
            binary = directory / "cases"
            compiler = shlex.split(os.environ.get("CXX", "g++"))
            subprocess.run(compiler + ["-std=c++11", "-O2", "-pthread", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "HeptaTrade"), str(source), str(ROOT / "HeptaTrade/oms_journal.cpp"),
                "-Wl,--wrap=fdatasync", "-o", str(binary)],
                check=True, capture_output=True, text=True, timeout=60)
            subprocess.run([str(binary), str(directory / "journal")],
                check=True, capture_output=True, text=True, timeout=30)

if __name__ == "__main__":
    unittest.main()
