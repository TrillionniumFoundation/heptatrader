#!/usr/bin/env python3
"""Application policy faults plus the actual native adapter's non-sending boundary."""
import argparse
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument("--adapter", required=True)
parser.add_argument("--native", required=True)
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("strategy_gateway_under_test", args.adapter)
g = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = g
spec.loader.exec_module(g)
SCOPE = "sha256:" + "a" * 64
ID = "application-test-command-001"


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.binding = SCOPE
        self.failure = None
        self.status = "ok"
        self.log = None
    def scope(self):
        return self.binding
    def call(self, operation, **kw):
        self.calls.append((operation, kw))
        if self.log:
            fd = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (operation + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        if self.failure == operation:
            raise RuntimeError("lost process response")
        return {"command_id": ID, "binding": self.binding, "ok": True,
                "result": {"tool": "trade.place_order" if operation == "submit" else "execution.get_command_status",
                           "status": self.status, "order_id": 1, "reason_code": "", "detail": "", "payload": None}}


class ApplicationPolicy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-app-policy-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = g.ApplicationStore(str(self.root / "application"))
        self.transport = FakeTransport()
        self.gateway = g.StrategyGateway(self.transport, self.store, clock_ms=lambda: 1)
        self.intent = g.LimitIntent("EUR.USD", "EUR", "CASH", "SIM", "USD", "BUY", "10", "1.1", "1.1001", 1900000000000)
        self.keydir = Path(self.store.path) / hashlib.sha256(b"key").hexdigest()
    def prepared(self):
        self.assertEqual(self.gateway.prepare("key", self.intent)["command_id"], ID)
    def mutations(self):
        return [op for op, _ in self.transport.calls if op == "submit"]
    def test_prepare_is_not_send_and_idempotent(self):
        self.prepared()
        before = (self.keydir / "intent.json").read_bytes()
        self.prepared()
        self.assertEqual([op for op, _ in self.transport.calls], ["prepare", "validate"])
        self.assertEqual((self.keydir / "intent.json").read_bytes(), before)
        self.assertFalse((self.keydir / "possibly-sent").exists())
    def test_normalization_and_wide_decimal_rejection(self):
        self.prepared()
        self.gateway.prepare("key", replace(self.intent, quantity="10.000", limit_price="1.10"))
        for value in ("0", "-1", "nan", "1e9999", "1e-9999", "1.00000000000000001", "true", " 1", "+1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(self.intent, quantity=value).fields()
        for value in (True, 0, 2**63):
            with self.assertRaises(ValueError):
                replace(self.intent, expires_at_ms=value).fields()
    def test_numeric_normalization_is_closed_and_preserves_value(self):
        for field in ("quantity", "limit_price", "reference_price"):
            for value in ("1e-126", "1e-127", "1.25e-127", "1e-300", "5e-324", "1e12", "0.1"):
                with self.subTest(field=field, value=value):
                    fields = replace(self.intent, **{field: value}).fields()
                    self.assertLessEqual(len(fields[field]), 128)
                    self.assertEqual(Decimal(fields[field]), Decimal(value))
                    self.assertEqual(g.LimitIntent(**fields).fields(), fields)
        # Existing valid HSA1 intent bytes must not acquire a new spelling.
        for value, canonical in (("1.10", "1.1"), ("10.000", "10"), ("1e-5", "0.00001"), ("1e12", "1000000000000")):
            self.assertEqual(replace(self.intent, quantity=value).fields()["quantity"], canonical)

    def test_small_exponent_native_validation_reaches_existing_sdk(self):
        token = self.root / "token"
        token.write_text("application-test-token-only")
        token.chmod(0o600)
        transport = g.NativeStrategyTransport(args.native, str(self.root / "missing.sock"), str(token))
        binding = transport.scope()
        empty_requests = self.root / "empty-requests"
        empty_requests.mkdir(mode=0o700)
        for field in ("quantity", "limit_price", "reference_price"):
            fields = replace(self.intent, **{field: "1e-300"}).fields()
            with self.subTest(field=field):
                with self.assertRaises(g.NativeClientError) as caught:
                    transport.call("validate", directory=str(empty_requests), binding=binding,
                                   fields=fields, command_id=ID)
                # The real adapter parsed the normalized intent and reached the
                # existing record loader. No socket, preview or mutation is used.
                self.assertTrue(caught.exception.response["reason"].startswith("RESEARCH_OUTBOX_"))
                self.assertEqual(caught.exception.response["operation"], "validate")
        self.assertEqual(list(empty_requests.iterdir()), [])

    def test_concurrent_store_initialization_waits_for_publication(self):
        # Pause a real first publisher after hard-link publication, before
        # unlinking its temporary name. A second constructor must acquire the
        # SAME flock before inspecting the transient two-link format marker.
        path = str(self.root / "concurrent-application")
        context = multiprocessing.get_context("fork")
        parent_publish, child_publish = context.Pipe()
        parent_open, child_open = context.Pipe()
        def publisher():
            real_link = g.os.link
            def paused_link(source, destination, *arguments, **keywords):
                real_link(source, destination, *arguments, **keywords)
                if destination == ".format":
                    child_publish.send("published")
                    if not child_publish.poll(5) or child_publish.recv() != "continue":
                        raise RuntimeError("publication barrier timed out")
            try:
                with patch.object(g.os, "link", paused_link):
                    g.ApplicationStore(path)
                child_publish.send("ok")
            except BaseException as error:
                child_publish.send("error:" + type(error).__name__ + ":" + str(error))
            finally:
                child_publish.close()
        def opener():
            real_lock = g._locked_file
            @contextmanager
            def observed_lock(dfd, name):
                if name == ".init.lock":
                    child_open.send("locking")
                with real_lock(dfd, name):
                    yield
            try:
                with patch.object(g, "_locked_file", observed_lock):
                    g.ApplicationStore(path)
                child_open.send("ok")
            except BaseException as error:
                child_open.send("error:" + type(error).__name__ + ":" + str(error))
            finally:
                child_open.close()
        first = context.Process(target=publisher)
        second = context.Process(target=opener)
        children = []
        try:
            first.start(); children.append(first)
            self.assertTrue(parent_publish.poll(5), "publisher did not reach link boundary")
            self.assertEqual(parent_publish.recv(), "published")
            self.assertEqual((Path(path) / ".format").stat().st_nlink, 2)
            second.start(); children.append(second)
            self.assertTrue(parent_open.poll(5), "second constructor did not reach init lock")
            self.assertEqual(parent_open.recv(), "locking")
            parent_publish.send("continue")
            self.assertTrue(parent_publish.poll(5), "first constructor did not finish")
            self.assertEqual(parent_publish.recv(), "ok")
            self.assertTrue(parent_open.poll(5), "second constructor did not finish")
            self.assertEqual(parent_open.recv(), "ok")
            for process in children:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            marker = Path(path) / ".format"
            self.assertEqual(marker.read_bytes(), g.FORMAT)
            self.assertEqual(marker.stat().st_nlink, 1)
            self.assertEqual({item.name for item in Path(path).iterdir()}, {".format", ".init.lock"})
        finally:
            for process in children:
                if process.is_alive():
                    process.kill()
                process.join(5)
            for connection in (parent_publish, child_publish, parent_open, child_open):
                connection.close()

    def test_orphan_format_hardlink_is_not_repaired(self):
        marker = Path(self.store.path) / ".format"
        alias = Path(self.store.path) / ".publish-orphan"
        os.link(marker, alias)
        with self.assertRaises(ValueError):
            g.ApplicationStore(self.store.path)
        self.assertEqual(marker.stat().st_nlink, 2)
        self.assertEqual(marker.read_bytes(), g.FORMAT)
        self.assertTrue(alias.exists())

    def test_existing_init_lock_does_not_admit_legacy_assets(self):
        path = self.root / "locked-legacy"
        path.mkdir(mode=0o700)
        for name, data in ((".init.lock", b""), ("legacy.json", b'{"legacy":true}')):
            item = path / name
            item.write_bytes(data)
            item.chmod(0o600)
        before = {item.name: item.read_bytes() for item in path.iterdir()}
        with self.assertRaises(ValueError):
            g.ApplicationStore(str(path))
        self.assertEqual({item.name: item.read_bytes() for item in path.iterdir()}, before)

    def test_old_oversized_intent_record_is_retained_not_converted(self):
        fields = self.intent.fields()
        fields["limit_price"] = format(Decimal("1e-300"), "f")
        record = {"schema": g.SCHEMA, "key": "key", "fields": fields, "binding": SCOPE}
        data = g._json(record)
        with self.store.locked("key", create=True) as (dfd, _):
            g._publish(dfd, "intent.json", data)
        with self.assertRaises(ValueError):
            self.gateway.prepare("key", replace(self.intent, limit_price="1e-300"))
        self.assertEqual((self.keydir / "intent.json").read_bytes(), data)
        self.assertFalse((self.keydir / "command.id").exists())
        self.assertEqual(self.transport.calls, [])

    def test_changed_intent_conflicts(self):
        self.prepared()
        for changes in ({"quantity": "11"}, {"side": "SELL"}, {"expires_at_ms": 1900000000001}, {"currency": "EUR"}):
            with self.assertRaises(ValueError):
                self.gateway.prepare("key", replace(self.intent, **changes))
        self.assertEqual(self.mutations(), [])
    def test_success_then_status_only(self):
        self.prepared()
        self.assertEqual(self.gateway.submit("key")["tool"], "trade.place_order")
        self.assertEqual(self.gateway.submit("key")["tool"], "execution.get_command_status")
        self.assertEqual(self.mutations(), ["submit"])
        self.assertEqual((self.keydir / "possibly-sent").read_text(), ID)
    def test_lost_reply_preserves_marker_and_never_resends(self):
        self.prepared()
        self.transport.failure = "submit"
        with self.assertRaises(RuntimeError):
            self.gateway.submit("key")
        self.transport.failure = None
        self.gateway.submit("key")
        self.assertEqual(self.mutations(), ["submit"])
    def test_every_result_status_is_not_retry_permission(self):
        for status in ("ok", "duplicate", "rejected", "uncertain", "error", "permission_denied"):
            key = "key-" + status
            self.gateway.prepare(key, self.intent)
            self.transport.status = status
            self.gateway.submit(key)
            self.assertEqual(self.gateway.submit(key)["tool"], "execution.get_command_status")
        self.assertEqual(len(self.mutations()), 6)
    def test_inspect_before_send_does_not_mark(self):
        self.prepared()
        self.gateway.inspect("key")
        self.assertFalse((self.keydir / "possibly-sent").exists())
        self.gateway.submit("key")
        self.assertEqual(self.mutations(), ["submit"])
    def test_preparation_loss_requires_explicit_original_id(self):
        self.transport.failure = "prepare"
        with self.assertRaises(RuntimeError):
            self.prepared()
        self.transport.failure = None
        with self.assertRaisesRegex(ValueError, "PREPARATION_INCOMPLETE"):
            self.prepared()
        self.assertEqual([op for op, _ in self.transport.calls], ["prepare"])
        self.gateway.adopt_preparation("key", ID)
        self.prepared()
        with self.assertRaises(ValueError):
            self.gateway.adopt_preparation("key", "different-command-001")
    def test_failed_prepare_durability_cannot_send(self):
        original = g._publish
        def fail(dfd, name, data):
            if name == "command.id":
                raise OSError("mapping fsync failed")
            original(dfd, name, data)
        with patch.object(g, "_publish", fail), self.assertRaises(OSError):
            self.prepared()
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_failed_marker_before_publication_cannot_send(self):
        self.prepared()
        original = g._publish
        def fail(dfd, name, data):
            if name == "possibly-sent":
                raise OSError("disk full")
            original(dfd, name, data)
        with patch.object(g, "_publish", fail), self.assertRaises(OSError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_failed_marker_after_publication_only_queries(self):
        self.prepared()
        original = g._publish
        def fail(dfd, name, data):
            original(dfd, name, data)
            if name == "possibly-sent":
                raise OSError("directory sync failed")
        with patch.object(g, "_publish", fail), self.assertRaises(OSError):
            self.gateway.submit("key")
        self.assertEqual(self.gateway.submit("key")["tool"], "execution.get_command_status")
        self.assertEqual(self.mutations(), [])
    def test_expiry_and_binding_block_without_marker(self):
        self.prepared()
        self.transport.binding = "sha256:" + "b" * 64
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.transport.binding = SCOPE
        self.gateway.clock_ms = lambda: self.intent.expires_at_ms
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.assertFalse((self.keydir / "possibly-sent").exists())
    def test_legacy_store_not_initialized_or_changed(self):
        path = self.root / "legacy"
        path.mkdir(mode=0o700)
        old = path / "old.json"
        old.write_bytes(b'{"schema":"hepta.research.outbox.v1"}')
        before = old.read_bytes()
        with self.assertRaises(ValueError):
            g.ApplicationStore(str(path))
        self.assertEqual({p.name for p in path.iterdir()}, {"old.json"})
        self.assertEqual(old.read_bytes(), before)
    def test_symlink_components_and_unsafe_modes(self):
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            g.ApplicationStore(str(link / "other"))
        os.chmod(self.store.path, 0o755)
        with self.assertRaises(ValueError):
            self.prepared()
    def test_corrupt_and_hardlinked_records(self):
        self.prepared()
        path = self.keydir / "command.id"
        os.link(path, self.root / "hardlink")
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        (self.root / "hardlink").unlink()
        path.write_bytes(b"short")
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_fifo_marker_fails_without_hanging(self):
        self.prepared()
        os.mkfifo(self.keydir / "possibly-sent", 0o600)
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_corrupt_intent_and_marker_do_not_send(self):
        self.prepared()
        original = (self.keydir / "intent.json").read_bytes()
        (self.keydir / "intent.json").write_bytes(b'{"schema":1,"schema":2}')
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        (self.keydir / "intent.json").write_bytes(original)
        with self.store.locked("key") as (dfd, _):
            g._publish(dfd, "possibly-sent", b"wrong-command-001")
        with self.assertRaises(ValueError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_actual_fsync_fault_before_send(self):
        self.prepared()
        real = g.os.fsync
        def fail(fd):
            if os.path.basename(os.readlink(f"/proc/self/fd/{fd}")).startswith(".publish-"):
                raise OSError("injected actual file fsync error")
            return real(fd)
        with patch.object(g.os, "fsync", fail), self.assertRaises(OSError):
            self.gateway.submit("key")
        self.assertEqual(self.mutations(), [])
    def test_concurrent_processes_place_once(self):
        self.prepared()
        self.transport.log = str(self.root / "calls")
        context = multiprocessing.get_context("fork")
        def invoke():
            self.gateway.submit("key")
        children = [context.Process(target=invoke) for _ in range(4)]
        for process in children:
            process.start()
        for process in children:
            process.join(5)
            if process.is_alive():
                process.kill(); process.join()
            self.assertEqual(process.exitcode, 0)
        calls = Path(self.transport.log).read_text().splitlines()
        self.assertEqual(calls.count("submit"), 1)
        self.assertEqual(calls.count("inspect"), 3)
    def test_sigkill_after_marker_before_send_is_query_only(self):
        self.prepared()
        context = multiprocessing.get_context("fork")
        def crash():
            original = self.transport.call
            def intercepted(operation, **kw):
                if operation == "submit":
                    os.kill(os.getpid(), signal.SIGKILL)
                return original(operation, **kw)
            self.transport.call = intercepted
            self.gateway.submit("key")
        process = context.Process(target=crash)
        process.start(); process.join(5)
        if process.is_alive():
            process.kill(); process.join()
        self.assertEqual(process.exitcode, -signal.SIGKILL)
        self.assertEqual(self.gateway.submit("key")["tool"], "execution.get_command_status")
        self.assertEqual(self.mutations(), [])
    def test_native_binding_and_strict_arguments_no_socket(self):
        token = self.root / "token"
        token.write_text("application-test-token-only")
        token.chmod(0o600)
        transport = g.NativeStrategyTransport(args.native, str(self.root / "missing.sock"), str(token))
        first = transport.scope()
        self.assertRegex(first, g.BINDING)
        token.write_text("application-test-token-rotated")
        self.assertNotEqual(transport.scope(), first)
        result = subprocess.run([args.native, "binding", "--socket", str(self.root / "missing.sock"),
            "--token-file", str(token), "--timeout-ms", "5000", "--unknown", "no"],
            capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertIs(json.loads(result.stdout)["ok"], False)
    def test_native_failures_do_not_inherit_credentials(self):
        token = self.root / "token"
        token.write_text("application-test-token-only")
        token.chmod(0o600)
        transport = g.NativeStrategyTransport(args.native, str(self.root / "missing.sock"), str(token))
        with patch.dict(os.environ, {"HEPTA_TOOL_SESSION_TOKEN": "not-a-real-credential", "PYTHONPATH": "/untrusted"}):
            self.assertEqual(transport.scope(), transport.scope())
        token.chmod(0o644)
        with self.assertRaises(g.NativeClientError):
            transport.scope()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApplicationPolicy)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.testsRun or not result.wasSuccessful() or result.skipped:
        raise SystemExit(1)
    print(f"APPLICATION_POLICY_PASS: {result.testsRun} tests, no skipped cases")
