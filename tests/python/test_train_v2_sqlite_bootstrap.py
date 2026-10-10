"""Adversarial SQLite initialization races; no broker, model, or training IO."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from train_v2.orchestrator import _retry_sqlite_schema_busy, database
from train_v2.registry import _registry_connection


class SQLiteBootstrapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.root.chmod(0o700)

    @staticmethod
    def _operational(code):
        exc = sqlite3.OperationalError("database is locked")
        exc.sqlite_errorcode = code
        return exc

    def test_busy_error_is_retried_and_resolves(self):
        calls = []
        def attempt():
            calls.append(1)
            if len(calls) < 3:
                raise self._operational(sqlite3.SQLITE_BUSY)
            return "ok"
        self.assertEqual(_retry_sqlite_schema_busy(attempt, retry_seconds=2),
                         "ok")
        self.assertEqual(len(calls), 3)

    def test_locked_error_is_retried_and_resolves(self):
        calls = []
        def attempt():
            calls.append(1)
            if len(calls) == 1:
                raise self._operational(sqlite3.SQLITE_LOCKED)
            return "ok"
        self.assertEqual(_retry_sqlite_schema_busy(attempt), "ok")
        self.assertEqual(len(calls), 2)

    def test_unrelated_operational_error_is_never_retried(self):
        calls = []
        def attempt():
            calls.append(1)
            raise self._operational(sqlite3.SQLITE_READONLY)
        with self.assertRaises(sqlite3.OperationalError):
            _retry_sqlite_schema_busy(attempt)
        self.assertEqual(len(calls), 1)

    def test_busy_after_deadline_fails_not_silently_passes(self):
        def attempt():
            raise self._operational(sqlite3.SQLITE_BUSY)
        with self.assertRaises(sqlite3.OperationalError):
            _retry_sqlite_schema_busy(attempt, retry_seconds=0)

    def test_two_independent_database_connections_initialize_wal(self):
        path = self.root / "simultaneous.sqlite"
        def open_check_close(_):
            conn = database(path)
            try:
                return conn.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=8) as ex:
            journals = list(ex.map(open_check_close, range(8)))
        self.assertEqual(journals, ["wal"] * 8)

    def test_fresh_multi_process_wal_bootstrap_no_lock_failure(self):
        db = self.root / "multi.sqlite"
        program = (
            "from train_v2.orchestrator import database;"
            "import sys;"
            "d=database(sys.argv[1]);"
            "assert d.execute('PRAGMA journal_mode').fetchone()==('wal',);"
            "d.close();print('ok')")
        self.run_separate_processes(program, [str(db)], workers=8)
        with sqlite3.connect(db) as verify:
            self.assertEqual(verify.execute("PRAGMA journal_mode").fetchone(),
                             ("wal",))
            names = [row[0] for row in verify.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            self.assertTrue({"plans", "events", "samples", "review_batches"}
                            <= set(names))

    def test_fresh_multi_process_registry_wal_bootstrap(self):
        folder = self.root / "registry"
        program = (
            "from train_v2.registry import _registry_connection;"
            "import sys;"
            "r,d=_registry_connection(sys.argv[1],create=True);"
            "assert d.execute('PRAGMA journal_mode').fetchone()==('wal',);"
            "d.close();print('ok')")
        self.run_separate_processes(program, [str(folder)], workers=8)
        with sqlite3.connect(folder / "registrations.sqlite") as verify:
            self.assertEqual(verify.execute("PRAGMA journal_mode").fetchone(),
                             ("wal",))
            self.assertEqual(verify.execute(
                "SELECT name FROM sqlite_master WHERE name='registrations'"
            ).fetchone(), ("registrations",))

    def run_separate_processes(self, program, args, *, workers):
        env = dict(os.environ)
        children = [subprocess.Popen(
            [sys.executable, "-B", "-c", program, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env) for _ in range(workers)]
        try:
            result = [child.communicate(timeout=15) for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
        for child, (out, err) in zip(children, result):
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(out.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
