"""Exercise installation in a temporary home without invoking systemd or GPU."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "neuron_installer", ROOT / "research/neuron_learning/install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class NeuronInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / ".config/systemd/user").mkdir(parents=True)
        self.config = {
            "output_dir": str(self.home / ".local/state/heptatrader/neuron-learning/student/EUR.USD"),
            "period_sha256": "a" * 64,
            "native_source_sha": "b" * 40,
        }
        for key in ("native_binary", "gpu_library", "accounting_module"):
            artifact = self.home / key
            artifact.write_text(key)
            self.config[key] = str(artifact)
        self.config_file = self.home / "source-config.json"

    def install(self, config, update=False):
        self.config_file.write_text(json.dumps(config))
        # Config validation has its own tests. This fixture cannot load a GPU
        # or execute a native binary, including during module import.
        runner = types.SimpleNamespace(config_load=lambda _: copy.deepcopy(config))
        argv = ["install.py", "--config", str(self.config_file)]
        if update:
            argv.append("--update-owned-sidecars")
        with patch.dict(sys.modules, {"runner": runner}), \
                patch.object(sys, "path", list(sys.path)), \
                patch.object(sys, "argv", argv), \
                patch.object(installer.Path, "home", return_value=self.home), \
                patch.object(installer.os, "geteuid", return_value=1000), \
                patch.object(installer.os, "umask"), \
                patch.object(installer.subprocess, "run") as service, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.last_service = service
            installer.main()
        return json.loads(output.getvalue()), service

    def test_configuration_update_gets_distinct_immutable_install(self):
        first, _ = self.install(self.config)
        changed = {**self.config, "period_sha256": "c" * 64}
        second, _ = self.install(changed, update=True)
        self.assertNotEqual(first["release_id"], second["release_id"])
        self.assertEqual(json.loads(Path(first["config"]).read_text())["period_sha256"], "a" * 64)
        self.assertEqual(json.loads(Path(second["config"]).read_text())["period_sha256"], "c" * 64)
        self.assertTrue(Path(first["release"]).is_dir())

    def test_reordered_config_is_idempotent(self):
        first, _ = self.install(self.config)
        second, service = self.install(dict(reversed(list(self.config.items()))))
        self.assertEqual(first["release_id"], second["release_id"])
        service.assert_called_once_with(["systemctl", "--user", "daemon-reload"], check=True)

    def test_reinstall_from_installed_config_is_idempotent(self):
        first, _ = self.install(self.config)
        second, _ = self.install(json.loads(Path(first["config"]).read_text()))
        self.assertEqual(first["release_id"], second["release_id"])

    def test_unowned_second_unit_does_not_stop_or_modify_first(self):
        first, _ = self.install(self.config)
        learning = Path(first["units"]["heptatrader-neuron-learning.service"]["path"])
        evaluator = Path(first["units"]["heptatrader-neuron-shadow-eval.service"]["path"])
        before = learning.read_bytes()
        evaluator.write_text("[Service]\nExecStart=/usr/bin/true\n")
        with self.assertRaisesRegex(RuntimeError, "EXISTING_UNIT_CHANGE_REQUIRES_REVIEW"):
            self.install({**self.config, "period_sha256": "d" * 64}, update=True)
        self.assertEqual(learning.read_bytes(), before)
        self.last_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
