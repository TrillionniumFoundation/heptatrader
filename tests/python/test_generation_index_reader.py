"""Compile the generation index sequential reader and prove linear selected-range I/O."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

CPP = r"""
#include "HeptaTrade/execution/generation_index_reader.h"
#include <fcntl.h>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char** argv)
{
    if (argc != 3) return 2;
    const off_t start = static_cast<off_t>(std::stoll(argv[2]));
    const int fd = ::open(argv[1], O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 3;
    struct stat metadata{};
    if (::fstat(fd, &metadata) != 0) return 4;
    GenerationSequentialLineReader reader(fd, metadata.st_size, start, 65536U);
    std::uint64_t lines = 0;
    off_t expected = start;
    std::string line;
    while (expected < metadata.st_size)
    {
        off_t rowStart = 0, rowEnd = 0;
        if (!reader.Read(rowStart, rowEnd, line) ||
            rowStart != expected || rowEnd <= rowStart || line.empty())
            return 5;
        expected = rowEnd;
        ++lines;
    }
    std::cout << "{\"lines\":" << lines
              << ",\"read_calls\":" << reader.ReadCalls()
              << ",\"read_bytes\":" << reader.ReadBytes()
              << ",\"selected_bytes\":"
              << static_cast<std::uint64_t>(metadata.st_size - start)
              << "}\n";
    return ::close(fd) == 0 ? 0 : 6;
}
"""


class GenerationIndexReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="hepta-generation-reader-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        source = root / "reader.cpp"
        cls.binary = root / "reader"
        source.write_text(CPP)
        subprocess.run(
            ["g++", "-std=c++11", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT), str(source), "-o", str(cls.binary)],
            check=True, capture_output=True, text=True, timeout=60)

    def _run(self, payload: bytes, start: int = 0, check: bool = True):
        path = Path(self.tmp.name) / "index.tsv"
        path.write_bytes(payload)
        return subprocess.run(
            [str(self.binary), str(path), str(start)],
            check=check, capture_output=True, text=True, timeout=10)

    @staticmethod
    def _fixture(rows: int, width: int = 512) -> bytes:
        output = bytearray()
        for index in range(rows):
            prefix = f"{index:08d}\t".encode()
            row = prefix + b"x" * (width - len(prefix) - 1) + b"\n"
            if len(row) != width:
                raise AssertionError("fixture row width drift")
            output.extend(row)
        return bytes(output)

    def test_full_and_suffix_scan_read_each_selected_byte_once(self):
        payload = self._fixture(20000)
        full = json.loads(self._run(payload).stdout)
        self.assertEqual(full["lines"], 20000)
        self.assertEqual(full["read_bytes"], len(payload))
        self.assertEqual(full["selected_bytes"], len(payload))
        self.assertLessEqual(full["read_calls"], (len(payload) + 65535) // 65536)

        start = 1234 * 512
        suffix = json.loads(self._run(payload, start).stdout)
        self.assertEqual(suffix["lines"], 20000 - 1234)
        self.assertEqual(suffix["read_bytes"], len(payload) - start)
        self.assertEqual(suffix["selected_bytes"], len(payload) - start)

        evidence_root = os.environ.get("HEPTA_CORE_EVIDENCE_DIR")
        if evidence_root:
            source_sha = os.environ.get("HEPTA_CORE_EVIDENCE_SOURCE_SHA", "")
            evidence_directory = Path(evidence_root)
            if (not evidence_directory.is_dir() or evidence_directory.is_symlink()
                    or len(source_sha) != 40
                    or any(c not in "0123456789abcdef" for c in source_sha)):
                raise AssertionError("core evidence identity or directory is invalid")
            observation = {
                "schema": "heptatrader.generation-index-read-cost.v1",
                "result": "PASS",
                "source_sha": source_sha,
                "fixture_rows": 20000,
                "row_bytes": 512,
                "logical_bytes": len(payload),
                "suffix_start_bytes": start,
                "full": full,
                "suffix": suffix,
                "broker_io": False,
                "authorization_effect": "NONE",
            }
            evidence_path = evidence_directory / "generation-index-read-cost.json"
            with evidence_path.open("x") as stream:
                json.dump(observation, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

    def test_oversized_or_unterminated_row_fails_closed(self):
        oversized = b"x" * 65537 + b"\n"
        self.assertNotEqual(self._run(oversized, check=False).returncode, 0)
        self.assertNotEqual(self._run(b"unterminated", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()
