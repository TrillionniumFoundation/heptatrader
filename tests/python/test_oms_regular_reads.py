from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_oms_checkpoint as checkpoint
import hepta_oms_lifecycle as lifecycle


FIFO_CASES = (
    "verify-current-v1", "verify-current-v2", "lookup-current",
    "hash", "metadata", "bytes", "command-index", "runtime-index",
    "hot-replay", "lines", "segment", "copy-file", "copy-segment",
    "lookup-index-race", "export-segment-race", "export-journal-race",
)


def fifo_child(case: str, root: Path) -> None:
    os.chmod(root, 0o700)
    if case.endswith("-race"):
        return fifo_race_child(case, root)
    source = root / "input"
    output = root / "output"
    destination_fd = -1
    if case in {"verify-current-v1", "verify-current-v2", "lookup-current"}:
        source = root / "CURRENT"
    elif case == "hot-replay":
        source = root / "hot-replay.jsonl"
    elif case == "segment":
        source = root / "segment-000001.jsonl"
    os.mkfifo(source, 0o600)
    operations = {
        "verify-current-v1": lambda: checkpoint.verify_generation(root),
        "verify-current-v2": lambda: lifecycle.verify_generation(root),
        "lookup-current": lambda: checkpoint.lookup_command(root, "a", "s", "c", "h"),
        "hash": lambda: checkpoint._sha256_file(source),
        "metadata": lambda: checkpoint._load_json_private(source),
        "bytes": lambda: checkpoint._read_private_bytes(source),
        "command-index": lambda: checkpoint._verify_index(source, 0),
        "runtime-index": lambda: checkpoint._verify_runtime_index(source, 0),
        "hot-replay": lambda: lifecycle._read_hot(root, 1024, 10, 512),
        "lines": lambda: list(lifecycle._iter_private_lines(source)),
        "segment": lambda: lifecycle._read_generation_segment(root, 1024, 10, 512),
        "copy-file": lambda: lifecycle._copy_private_file(source, output),
    }
    if case == "copy-segment":
        destination_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        operations[case] = lambda: lifecycle._copy_generation_segment(source, destination_fd, 0)
    # Only this private test interpreter is subject to the watchdog. The
    # production functions must reject before opening a FIFO can wait.
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        try:
            operations[case]()
        except checkpoint.GenerationError as error:
            print(str(error))
        else:
            raise AssertionError("non-regular input accepted")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if destination_fd >= 0:
            os.close(destination_fd)


def fifo_race_child(case: str, root: Path) -> None:
    # Use the original SIM-only generation fixture and all real verification.
    sys.path.insert(0, str(Path(__file__).parent))
    from test_oms_checkpoint import event
    journal = root / "journal"
    journal.write_text("".join(json.dumps(value) + "\n" for value in (
        event("order_intent", "c", "h"), event("place_send_attempt", "c", "h"))))
    journal.chmod(0o600)
    store = root / "generations"
    manifest = checkpoint.build_generation(journal, store, stopped=True)
    generation = store / manifest["generation"]
    actual_open = os.open
    verified = checkpoint.verify_generation if case == "lookup-index-race" else lifecycle.verify_generation
    calls = 0
    armed = False

    def after_verification(*args, **kwargs):
        nonlocal calls, armed, swapped
        result = verified(*args, **kwargs)
        calls += 1
        # Export verifies once up front, then again just before each segment.
        armed = calls >= (1 if case == "lookup-index-race" else 2)
        if case == "lookup-index-race":
            target.unlink(); os.mkfifo(target, 0o600); swapped = True
        return result

    target = (generation / "command-index.tsv" if case == "lookup-index-race" else
              generation / "segment-000001.jsonl" if case == "export-segment-race" else journal)
    swapped = False

    def swapped_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == target and not swapped and (armed or case == "export-journal-race"):
            target.unlink(); os.mkfifo(target, 0o600); swapped = True
        return actual_open(path, flags, *args, **kwargs)

    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        owner = checkpoint if case == "lookup-index-race" else lifecycle
        with patch.object(owner, "verify_generation", side_effect=after_verification), \
                patch.object(os, "open", side_effect=swapped_open):
            try:
                if case == "lookup-index-race":
                    checkpoint.lookup_command(store, "agent-a", "session-a", "c", "h")
                else:
                    lifecycle.export_legacy(journal, store, root / "export")
            except checkpoint.GenerationError as error:
                assert swapped
                print(str(error))
            else:
                raise AssertionError("post-verification FIFO replacement accepted")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


class OmsRegularReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-oms-regular-read-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)

    def write(self, name: str, value: bytes) -> Path:
        path = self.root / name
        path.write_bytes(value)
        path.chmod(0o600)
        return path

    @unittest.skipUnless(sys.platform == "linux", "Linux FIFO watchdog proof")
    def test_public_and_shared_readers_reject_real_fifos_before_waiting(self) -> None:
        for case in FIFO_CASES:
            with self.subTest(case=case):
                directory = self.root / case
                directory.mkdir(mode=0o700)
                run = subprocess.run(
                    [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                     "--fifo-child", case, str(directory)],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(run.returncode, 0, (run.returncode, run.stdout, run.stderr))
                self.assertIn("UNSAFE", run.stdout)

    def test_regular_metadata_hash_and_indexes_keep_existing_results(self) -> None:
        path = self.write("metadata", b'{"old":"result"}\n')
        self.assertEqual(checkpoint._load_json_private(path), {"old": "result"})
        self.assertEqual(checkpoint._sha256_file(path),
                         (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()))
        self.assertEqual(checkpoint._read_private_bytes(path), path.read_bytes())
        empty = self.write("empty", b"")
        self.assertEqual(checkpoint._verify_index(empty, 0), [])
        self.assertIsNone(checkpoint._verify_runtime_index(empty, 0))
        lines = self.write("lines", b"one\ntwo\n")
        self.assertEqual(list(lifecycle._iter_private_lines(lines)), [b"one\n", b"two\n"])

    def test_full_index_reader_does_not_add_metadata_size_limit(self) -> None:
        # Valid legacy indexes may exceed the metadata reader's16MiB default.
        # The full-file private read must preserve that existing scale.
        raw = b"x" * (16 * 1024 * 1024 + 1)
        path = self.write("large-index", raw)
        self.assertEqual(checkpoint._read_private_bytes(path, maximum=None), raw)
        with self.assertRaisesRegex(checkpoint.GenerationError, "UNSAFE_METADATA"):
            checkpoint._read_private_bytes(path)

    def test_full_index_read_accepts_short_regular_reads_and_rejects_growth(self) -> None:
        raw = b"original index bytes\n" * 10
        path = self.write("short-read-index", raw)
        actual_read = os.read
        with patch.object(checkpoint.os, "read", side_effect=lambda fd, size: actual_read(fd, min(size, 7))):
            self.assertEqual(checkpoint._read_private_bytes(path, maximum=None), raw)
        actual_stat = os.fstat
        changed = False

        def grow_after_observation(fd):
            nonlocal changed
            observed = actual_stat(fd)
            if not changed:
                changed = True
                with path.open("ab") as stream:
                    stream.write(b"not in the observation")
            return observed

        with patch.object(checkpoint.os, "fstat", side_effect=grow_after_observation):
            with self.assertRaisesRegex(checkpoint.GenerationError, "METADATA_CHANGED"):
                checkpoint._read_private_bytes(path, maximum=None)

    def test_streaming_line_bound_rejects_without_reading_the_remaining_file(self) -> None:
        path = self.write("oversize-index", b"x" * (4 * 1024 * 1024) + b"\n")
        actual_read = os.read
        observed = []

        def bounded_probe(fd: int, size: int) -> bytes:
            block = actual_read(fd, size)
            observed.append(len(block))
            return block

        with patch.object(lifecycle.os, "read", side_effect=bounded_probe):
            with self.assertRaisesRegex(checkpoint.GenerationError, "INDEX_LINE_LIMIT"):
                list(lifecycle._iter_private_lines(path))
        self.assertEqual(len(observed), 1)
        self.assertLessEqual(sum(observed), 1024 * 1024)

    def test_streaming_line_bound_preserves_exact_boundary_and_torn_rejection(self) -> None:
        limit = checkpoint.MAX_INDEX_LINE
        path = self.write("boundary-index", b"a" * (limit - 1) + b"\nsmall\n")
        self.assertEqual(list(lifecycle._iter_private_lines(path)),
                         [b"a" * (limit - 1) + b"\n", b"small\n"])
        path.write_bytes(b"x" * limit + b"\n")
        with self.assertRaisesRegex(checkpoint.GenerationError, "INDEX_LINE_LIMIT"):
            list(lifecycle._iter_private_lines(path))
        path.write_bytes(b"short-torn")
        with self.assertRaisesRegex(checkpoint.GenerationError, "INDEX_TORN_RECORD"):
            list(lifecycle._iter_private_lines(path))

    def test_private_metadata_rules_and_descriptor_cleanup_are_retained(self) -> None:
        path = self.write("unsafe", b"{}")
        path.chmod(0o644)
        count = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(12):
            for reader in (checkpoint._load_json_private, checkpoint._read_private_bytes,
                           checkpoint._sha256_file):
                with self.assertRaisesRegex(checkpoint.GenerationError, "UNSAFE"):
                    reader(path)
        self.assertEqual(len(list(Path("/proc/self/fd").iterdir())), count)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fifo-child":
        fifo_child(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
