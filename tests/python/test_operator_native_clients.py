"""Operator client routing on private fixtures; no native socket or manager calls."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_operator_clients as clients
import hepta_native_reader as reader


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class OperatorClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-operator-clients-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = self.root / "release"; self.release.mkdir(mode=0o700)
        self.home = self.root / "home"; self.home.mkdir()
        self.files = ["heptatrader-user", "hepta_maintenance_latch.py", "hepta_operator_clients.py",
                      "hepta_native_reader.py", "hepta_verified_loader.py", "hepta_inactive_owner.py", "hepta-user-mcp"]
        for name in self.files:
            shutil.copyfile(ROOT / "scripts" / name, self.release / name)
            (self.release / name).chmod(0o500)
        self.guard = self.root / "original-startup-guard.py"
        self.guard.write_text("# Original configured startup helper, retained unchanged.\n")
        self.guard.chmod(0o500)
        self.bindings = {}
        for name in reader.NATIVE_CLIENT_NAMES:
            binary = self.root / name
            binary.write_text("#!/usr/bin/python3\n" +
                "import json,sys\nargs=sys.argv[1:]\n" +
                "print(json.dumps({'status':'ok','tool':args[-1] if args else '',"
                "'payload':{'arguments':args,'client':" + repr(name) + "}}))\n")
            binary.chmod(0o500)
            self.bindings[name] = {"path": str(binary), "sha256": sha(binary)}
        self.manifest = {"source_commit": "a" * 40,
                         "files": {name: sha(self.release / name) for name in self.files},
                         "native_clients": self.bindings,
                         "native_startup_guard": {"path": str(self.guard), "sha256": sha(self.guard)}}
        self.write_manifest()
        spec = importlib.util.spec_from_file_location("fixture_operator_latch", self.release / "hepta_maintenance_latch.py")
        self.latch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.latch)

    def write_manifest(self, value=None):
        path = self.release / "MANIFEST.json"
        if path.exists():
            path.chmod(0o600)
        path.write_text(json.dumps(self.manifest if value is None else value))
        path.chmod(0o400)

    def select(self):
        changes = {
            "heptatrader-user": ("operator_release_required=false", "operator_release_required=true"),
            "hepta-user-mcp": ("operator_mcp_required=false", "operator_mcp_required=true"),
            "hepta_operator_clients.py": ("REQUIRE_PINNED_CLIENTS = False", "REQUIRE_PINNED_CLIENTS = True"),
            "hepta_maintenance_latch.py": ("OPERATOR_CLIENTS_REQUIRED = False", "OPERATOR_CLIENTS_REQUIRED = True")}
        old_helper_sha = sha(self.release / "hepta_operator_clients.py")
        for name, (old, new) in changes.items():
            path = self.release / name
            text = path.read_text(); self.assertEqual(text.count(old), 1)
            path.chmod(0o700); path.write_text(text.replace(old, new)); path.chmod(0o500)
        path = self.release / "hepta_maintenance_latch.py"
        path.chmod(0o700); path.write_text(path.read_text().replace(old_helper_sha,
            sha(self.release / "hepta_operator_clients.py"))); path.chmod(0o500)
        self.manifest["files"] = {name: sha(self.release / name) for name in self.files}
        self.write_manifest()
        spec = importlib.util.spec_from_file_location("selected_operator_latch", path)
        self.latch = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.latch)

    def fake_legacy_clients(self):
        legacy = self.home / ".local/share/heptatrader/current/bin"; legacy.mkdir(parents=True)
        for name in ("heptactl", "hepta-sessionctl"):
            path = legacy / name
            path.write_text("#!/bin/sh\necho legacy-client-was-used\n"); path.chmod(0o500)

    def fake_codex(self):
        path = self.home / ".local/share/heptatrader/tools/node_modules/.bin/codex"
        path.parent.mkdir(parents=True)
        path.write_text("#!/usr/bin/python3\nimport json,sys; print(json.dumps(sys.argv[1:]))\n")
        path.chmod(0o500)

    def invoke(self, *arguments, **extra_env):
        return subprocess.run(["/bin/bash", str(self.release / "heptatrader-user"), *arguments],
            capture_output=True, text=True, timeout=5,
            env={**os.environ, "HOME": str(self.home), "XDG_RUNTIME_DIR": str(self.root / "run"), **extra_env})

    def test_native_and_session_use_pinned_images_with_original_arguments(self):
        cases = [(["native", "--call-id", "original-command", "command.status", "command_id=original-command"],
                  "heptactl", ["--socket", str(self.root / "run/heptatrader/tools.sock"), "--token-file",
                   str(self.home / ".config/heptatrader/paper.token")]),
                 (["session", "command-status", "--command", "/original/outbox/request.hsr1"],
                  "hepta-sessionctl", ["--socket", str(self.root / "run/heptatrader/supervisor.sock")])]
        for arguments, name, prefix in cases:
            with self.subTest(route=name):
                result = self.invoke(*arguments, HEPTA_NATIVE_CLIENTS="/untrusted/map",
                                     HEPTA_OPERATOR_MANIFEST="/untrusted/manifest")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)["payload"]
                self.assertEqual(payload, {"client": name, "arguments": prefix + arguments[1:]})
                self.assertNotIn("/proc/self/fd", json.dumps(payload))

    def test_internal_payload_uses_same_binding_and_existing_owner_paths(self):
        for owner in ("paper", "crypto"):
            with self.subTest(owner=owner):
                value = self.latch._native_payload(self.root / "runtime/control", owner, "orders.list")
                args = value["arguments"]
                self.assertEqual(value["client"], "heptactl")
                self.assertEqual(args[:6], ["--socket", str(self.root / "runtime/tools.sock"), "--token-file",
                    str(Path(pwd.getpwuid(os.geteuid()).pw_dir) / (".config/heptatrader/" + owner + ".token")),
                    "--io-timeout-ms", "4000"])
                self.assertEqual(args[6], "--call-id")
                self.assertTrue(args[7].startswith("hold-resume-"))
                self.assertEqual(args[8:], ["call", "orders.list"])

    def test_owner_audit_keeps_its_top_level_mode_and_exact_owner_arguments(self):
        self.select()
        arguments = ["owner-audit", "--token-file", "/original/private/owner.token"]
        result = self.invoke("session", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"],
                         {"client": "hepta-sessionctl", "arguments": arguments})
        # The generic unconfigured wrapper preserves the same native mode too.
        wrapper = self.release / "heptatrader-user"
        wrapper.chmod(0o700); wrapper.write_bytes((ROOT / "scripts/heptatrader-user").read_bytes())
        wrapper.chmod(0o500); (self.release / "MANIFEST.json").unlink()
        legacy = self.home / ".local/share/heptatrader/current/bin"; legacy.mkdir(parents=True)
        shutil.copyfile(self.bindings["hepta-sessionctl"]["path"], legacy / "hepta-sessionctl")
        (legacy / "hepta-sessionctl").chmod(0o500)
        result = self.invoke("session", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"]["arguments"], arguments)

    def test_inactive_owner_audit_is_not_given_supervisor_arguments(self):
        self.select()
        arguments = ["inactive-owner-audit", "--owner", "crypto", "--phase", "held",
                     "--execution-pid", "123", "--execution-start-ticks", "456"]
        result = self.invoke("session", *arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"],
                         {"client": "hepta-sessionctl", "arguments": arguments})

    def test_optional_inactive_runtime_binding_requires_explicit_source_pins(self):
        runtime = {"schema": clients.INACTIVE_RUNTIME_SCHEMA,
            "execution_binary": dict(self.bindings["heptactl"]),
            "build_receipt": {"path": str(self.guard), "sha256": sha(self.guard)},
            "source_commit": "a" * 40,
            "source_leaf_sha256": {name: "b" * 64 for name in clients.INACTIVE_RUNTIME_SOURCES}}
        self.manifest["inactive_owner_runtime"] = runtime; self.write_manifest()
        self.assertEqual(clients.load_config(self.release)["inactive_owner_runtime"], runtime)
        for change in ("missing_source", "bad_source_hash", "unpinned_module", "incomplete_binding"):
            candidate = copy.deepcopy(self.manifest)
            if change == "missing_source": candidate["inactive_owner_runtime"]["source_leaf_sha256"].pop(next(iter(clients.INACTIVE_RUNTIME_SOURCES)))
            elif change == "bad_source_hash": candidate["inactive_owner_runtime"]["source_leaf_sha256"]["wrong"] = "not-a-digest"
            elif change == "unpinned_module": candidate["files"].pop("hepta_inactive_owner.py")
            else: candidate["inactive_owner_runtime"].pop("build_receipt")
            self.write_manifest(candidate)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "INACTIVE_OWNER_RUNTIME"):
                clients.load_config(self.release)

    def systemd(self, argv):
        unit = argv[3]
        values = {"ActiveState": "active", "SubState": "running", "MainPID": "1234", "InvocationID": unit}
        if unit == "heptatrader-execution.service":
            values.update(Type="simple", KillMode="control-group",
                ExecStartPre="{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B " + str(self.guard) +
                    " native-startup-hold ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; "
                    "pid=0 ; code=(null) ; status=0/0 }")
        return "\n".join(key + "=" + value for key, value in values.items())

    def test_original_configured_startup_guard_path_and_bytes_are_required(self):
        with patch.object(self.latch, "_command", side_effect=self.systemd):
            self.assertEqual(set(self.latch._service_snapshot()),
                {"heptatrader-execution.service", "heptatrader-gateway.service"})
        def new_copy(argv):
            return self.systemd(argv).replace(str(self.guard), str(self.release / "hepta_maintenance_latch.py"))
        with patch.object(self.latch, "_command", side_effect=new_copy):
            with self.assertRaisesRegex(self.latch.HoldError, "STARTUP_GUARD_NOT_COORDINATED"):
                self.latch._service_snapshot()
        self.guard.chmod(0o700); self.guard.write_text("# replaced original helper\n"); self.guard.chmod(0o500)
        with patch.object(self.latch, "_command", side_effect=self.systemd):
            with self.assertRaisesRegex(ValueError, "BINARY_CHANGED"):
                self.latch._service_snapshot()

    def test_extra_guard_argument_or_command_and_policy_changes_remain_rejected(self):
        for old, new in [(" native-startup-hold ;", " native-startup-hold extra ;"),
                         ("ignore_errors=no", "ignore_errors=yes"),
                         ("KillMode=control-group", "KillMode=process"),
                         ("Type=simple", "Type=notify"),
                         ("status=0/0 }", "status=0/0 } { path=/bin/true ; }")]:
            with self.subTest(change=new), patch.object(self.latch, "_command",
                    side_effect=lambda argv: self.systemd(argv).replace(old, new)):
                with self.assertRaisesRegex(self.latch.HoldError, "STARTUP_GUARD_NOT_COORDINATED"):
                    self.latch._service_snapshot()

    def test_guard_change_during_manager_read_is_detected(self):
        def changed(argv):
            raw = self.systemd(argv)
            if argv[3] == "heptatrader-gateway.service":
                self.guard.chmod(0o700); self.guard.write_text("# changed during proof\n"); self.guard.chmod(0o500)
            return raw
        with patch.object(self.latch, "_command", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "BINARY_CHANGED"):
                self.latch._service_snapshot()

    def test_partial_binding_bad_source_and_bad_selected_image_never_fall_back(self):
        original = copy.deepcopy(self.manifest)
        variants = []
        partial = copy.deepcopy(original); del partial["native_clients"]["hepta-sessionctl"]; variants.append(partial)
        no_guard = copy.deepcopy(original); del no_guard["native_startup_guard"]; variants.append(no_guard)
        bad_source = copy.deepcopy(original); bad_source["files"]["hepta_native_reader.py"] = "0" * 64; variants.append(bad_source)
        bad_image = copy.deepcopy(original); bad_image["native_clients"]["heptactl"]["sha256"] = "0" * 64; variants.append(bad_image)
        for manifest in variants:
            with self.subTest(manifest=manifest):
                self.write_manifest(manifest)
                result = self.invoke("native", "call", "orders.list")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(result.stdout)

    def test_helper_source_is_pinned_before_latch_import(self):
        helper = self.release / "hepta_operator_clients.py"
        helper.chmod(0o700); helper.write_text("raise AssertionError('unverified helper executed')\n"); helper.chmod(0o500)
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            self.latch._operator_clients()

    def test_manifest_symlink_and_duplicate_declarations_are_rejected(self):
        manifest = self.release / "MANIFEST.json"
        raw = manifest.read_bytes(); manifest.unlink()
        other = self.root / "other.json"; other.write_bytes(raw); manifest.symlink_to(other)
        self.assertNotEqual(self.invoke("native", "call", "orders.list").returncode, 0)
        manifest.unlink()
        manifest.write_text('{"native_clients":{},"native_clients":{}}')
        self.assertNotEqual(self.invoke("native", "call", "orders.list").returncode, 0)

    def test_unconfigured_wrapper_keeps_legacy_path(self):
        (self.release / "MANIFEST.json").unlink()
        legacy = self.home / ".local/share/heptatrader/current/bin"; legacy.mkdir(parents=True)
        for name in ("heptactl", "hepta-sessionctl"):
            shutil.copyfile(self.bindings[name]["path"], legacy / name)
            (legacy / name).chmod(0o500)
        result = self.invoke("native", "call", "orders.list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["payload"]["client"], "heptactl")

    def test_legacy_manifest_without_declaration_keeps_existing_behavior(self):
        self.write_manifest({"source_commit": "b" * 40, "files": {}})
        self.fake_legacy_clients()
        result = self.invoke("native", "call", "orders.list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "legacy-client-was-used")
        self.assertIsNone(self.latch._operator_clients())

    def test_selected_missing_manifest_or_declaration_refuses_legacy(self):
        self.select(); self.fake_legacy_clients()
        path = self.release / "MANIFEST.json"; path.unlink()
        for route in ("native", "session"):
            result = self.invoke(route, "fixture-command")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("legacy-client", result.stdout)
        with self.assertRaisesRegex(self.latch.HoldError, "DECLARATION_REQUIRED"):
            self.latch._operator_clients()
        self.write_manifest({"files": {}})
        self.assertNotEqual(self.invoke("native", "fixture-command").returncode, 0)
        with self.assertRaisesRegex(ValueError, "DECLARATION_REQUIRED"):
            self.latch._operator_clients()

    def test_selected_missing_helper_cannot_use_legacy(self):
        self.select(); self.fake_legacy_clients()
        (self.release / "hepta_operator_clients.py").unlink()
        result = self.invoke("native", "fixture-command")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("legacy-client", result.stdout)
        with self.assertRaises(FileNotFoundError):
            self.latch._operator_clients()

    def test_manifest_fifo_cannot_hang_native_or_latch_detection(self):
        path = self.release / "MANIFEST.json"; path.unlink(); os.mkfifo(path, 0o600)
        self.assertNotEqual(self.invoke("native", "fixture-command").returncode, 0)
        code = ("import importlib.util; s=importlib.util.spec_from_file_location('l'," +
                repr(str(self.release / "hepta_maintenance_latch.py")) +
                "); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m._operator_clients()")
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
            text=True, timeout=2, env={**os.environ, "PYTHONPATH": str(self.release)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OPERATOR_RELEASE_FILE_UNSAFE", result.stderr)

    def test_selected_mcp_routes_use_local_files_and_missing_files_refuse(self):
        self.select(); self.fake_codex()
        result = self.invoke("ask", "fixture only; fake Codex records argv")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertIn('mcp_servers.heptatrader.command="' + str(self.release / "hepta-user-mcp") + '"', args)
        adapter = self.release / "hepta_mcp_server.py"
        adapter.write_text("import json,os;print(json.dumps({'path':__file__,'socket':os.environ['HEPTA_TOOL_SOCKET']}))\n")
        adapter.chmod(0o500)
        env = {**os.environ, "HOME": str(self.home), "XDG_RUNTIME_DIR": str(self.root / "run")}
        result = subprocess.run(["/bin/bash", str(self.release / "hepta-user-mcp")], capture_output=True,
                                text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"path": str(adapter),
            "socket": str(self.root / "run/heptatrader/tools.sock")})
        adapter.unlink()
        result = subprocess.run(["/bin/bash", str(self.release / "hepta-user-mcp")], capture_output=True,
                                text=True, timeout=5, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("adapter is missing", result.stderr)
        (self.release / "hepta-user-mcp").unlink()
        result = self.invoke("ask", "fixture only; fake Codex must not start")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.stdout)
        self.assertIn("launcher is missing", result.stderr)

    def test_exec_uses_verified_inode_after_path_replacement(self):
        binding = self.bindings["heptactl"]
        original_execve = os.execve
        def inspect_exec(executable, arguments, environment):
            replacement = self.root / "replacement"
            replacement.write_text("#!/usr/bin/python3\nraise SystemExit(99)\n"); replacement.chmod(0o500)
            replacement.replace(binding["path"])
            fd = int(executable.rsplit("/", 1)[1])
            result = subprocess.run(arguments, executable=executable, pass_fds=(fd,),
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["payload"]["arguments"], ["original-command-id"])
            self.assertEqual(arguments[0], binding["path"])
            raise SystemExit(0)
        with patch.object(clients.os, "execve", side_effect=inspect_exec):
            with self.assertRaises(SystemExit):
                clients.exec_client("heptactl", ["original-command-id"], self.release)
        self.assertIs(os.execve, original_execve)
        with self.assertRaisesRegex(ValueError, "BINARY_CHANGED"):
            clients.exec_client("heptactl", [], self.release)


if __name__ == "__main__":
    unittest.main()
