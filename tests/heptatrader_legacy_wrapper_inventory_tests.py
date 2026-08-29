#!/usr/bin/env python3

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "scripts"))

import inventory_heptatrader_legacy_wrappers as inventory  # noqa: E402


class LegacyWrapperInventoryTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / "ops").mkdir()
        (root / "scripts").mkdir()
        (root / "systemd").mkdir()
        registry = root / "ops/hepta-ops-v1.json"
        registry.write_text(json.dumps({
            "schema": "hepta.ops-registry.v1",
            "version": 1,
            "jobs": {
                "fixture.run": {
                    "lifecycle": "canonical",
                    "executable": "scripts/fixture.py",
                    "arguments": [],
                    "allow_user_arguments": True,
                    "network_allowed": False,
                    "paper_authorized": False,
                    "live_authorized": False,
                    "compatibility_wrappers": [],
                },
            },
        }, sort_keys=True) + "\n", encoding="utf-8")
        registry.chmod(0o600)
        (root / "run_fixture.sh").write_text(
            "#!/bin/sh\nexec python3 scripts/fixture.py\n",
            encoding="utf-8")
        (root / "status_unmapped.sh").write_text(
            "#!/bin/sh\nexec python3 scripts/other.py\n",
            encoding="utf-8")
        (root / "systemd/fixture.service").write_text(
            "[Service]\nExecStart=/repo/run_fixture.sh\n",
            encoding="utf-8")
        subprocess.run(
            ["git", "add", "ops/hepta-ops-v1.json", "run_fixture.sh",
             "systemd/fixture.service"],
            cwd=root, check=True)
        return registry

    def test_inventory_maps_jobs_and_blocks_referenced_wrappers(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-wrapper-inventory-") as temporary:
            root = Path(temporary)
            registry = self._fixture(root)
            report = inventory.inventory(root, registry)
            self.assertEqual(
                report["schema"],
                "hepta.legacy-wrapper-retirement-inventory.v2")
            self.assertEqual(report["version"], 2)
            self.assertTrue(report["passed"])
            self.assertFalse(report["migration_complete"])
            self.assertFalse(report["deletion_authorized"])
            self.assertEqual(report["wrapper_count"], 2)
            records = {record["path"]: record for record in report["records"]}
            self.assertEqual(
                records["run_fixture.sh"]["mapped_jobs"], ["fixture.run"])
            self.assertEqual(
                records["run_fixture.sh"]["retirement_status"],
                "blocked-referenced")
            self.assertEqual(
                records["status_unmapped.sh"]["retirement_status"],
                "blocked-unmapped")
            self.assertEqual(report["tracked_wrapper_count"], 1)
            self.assertEqual(report["untracked_wrapper_count"], 1)
            self.assertEqual(report["mapped_wrapper_count"], 1)
            self.assertEqual(report["referenced_wrapper_count"], 1)
            self.assertFalse(report["host_runtime"]["collected"])
            self.assertFalse(report["host_runtime"]["complete"])
            self.assertFalse(report["scan_complete"])
            self.assertTrue(report["host_runtime"]["read_only"])
            self.assertEqual(
                report["host_runtime"]["schema"],
                "hepta.host-script-reference-inventory.v1")
            self.assertFalse(
                report["host_runtime"]["redaction"]
                ["command_arguments_recorded"])
            self.assertFalse(
                report["host_runtime"]["redaction"]
                ["environment_recorded"])

    def test_private_output_and_require_complete_exit(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-wrapper-output-") as temporary:
            root = Path(temporary)
            self._fixture(root)
            output = root / "evidence/inventory.json"
            process = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY /
                        "scripts/inventory_heptatrader_legacy_wrappers.py"),
                    "--root", str(root),
                    "--output", str(output),
                    "--require-complete",
                ],
                check=False,
            )
            self.assertEqual(process.returncode, 1)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(report["migration_complete"])

    def test_all_git_paths_and_dangling_references_block_completion(self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-wrapper-dangling-") as temporary:
            root = Path(temporary)
            registry = self._fixture(root)
            workflow = root / ".github/workflows/use.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "run: ./status_unmapped.sh\n"
                "next: ./run_missing.sh\n",
                encoding="utf-8")
            subprocess.run(
                ["git", "add", ".github/workflows/use.yml"],
                cwd=root, check=True)
            report = inventory.inventory(root, registry)
            records = {record["path"]: record for record in report["records"]}
            self.assertIn(
                ".github/workflows/use.yml:1",
                records["status_unmapped.sh"]["references"])
            self.assertIn("run_missing.sh", report["dangling_references"])
            self.assertFalse(report["migration_complete"])

    def test_host_systemd_inventory_expands_instances_and_records_state(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-wrapper-systemd-") as temporary:
            root = Path(temporary).resolve()
            (root / "scripts").mkdir()
            (root / "scripts/ibgateway_supervise.sh").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8")
            (root / "run_ib_scalping_main_alpha_paper.sh").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8")
            (root / "status_guard.sh").write_text(
                "#!/bin/sh\nexit 0\n", encoding="utf-8")
            external = root / "research-worktree"
            (external / "scripts").mkdir(parents=True)
            (external / "scripts/oos_probe.py").write_text(
                "raise SystemExit(0)\n", encoding="utf-8")
            units = root / "host-systemd"
            units.mkdir()
            (units / "ibgateway.service").write_text(
                "[Service]\n"
                f"ExecCondition=/bin/bash {root}/status_guard.sh\n"
                f"ExecStart={root}/scripts/ibgateway_supervise.sh\n",
                encoding="utf-8")
            (units / "hepta-ib-scalping@.service").write_text(
                "[Service]\n"
                f"ExecStart=/bin/bash {root}/"
                "run_ib_scalping_main_%i_paper.sh\n",
                encoding="utf-8")
            (units / "research.service").write_text(
                "[Service]\n"
                f"WorkingDirectory={external}\n"
                "ExecStart=/usr/bin/python3 scripts/oos_probe.py\n",
                encoding="utf-8")

            def run(arguments: list[str]
                    ) -> subprocess.CompletedProcess[str]:
                output = ""
                if "list-unit-files" in arguments and "--user" not in arguments:
                    output = (
                        "ibgateway.service enabled enabled\n"
                        "hepta-ib-scalping@.service disabled enabled\n"
                        "hepta-ib-scalping@alpha.service enabled enabled\n"
                        "research.service disabled enabled\n"
                    )
                elif "list-units" in arguments and "--user" not in arguments:
                    output = (
                        "ibgateway.service loaded active running fixture\n"
                        "hepta-ib-scalping@alpha.service loaded inactive "
                        "dead fixture\n"
                    )
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=output, stderr="")

            with (
                    mock.patch.object(
                        inventory, "SYSTEMD_RUNTIME_ROOTS",
                        (("system", units),)),
                    mock.patch.object(
                        inventory, "_run_read_only", side_effect=run)):
                errors: set[str] = set()
                records, scanned = inventory._systemd_inventory(
                    root,
                    {
                        "run_ib_scalping_main_alpha_paper.sh",
                        "status_guard.sh",
                    },
                    {"scripts/ibgateway_supervise.sh"},
                    {
                        "run_ib_scalping_main_alpha_paper.sh",
                        "status_guard.sh",
                    },
                    errors,
                )
            self.assertFalse(errors)
            self.assertEqual(scanned, [f"system:{units}"])
            direct = next(
                record for record in records
                if record["script_path"] ==
                "scripts/ibgateway_supervise.sh")
            self.assertEqual(direct["script_kind"], "repository-script")
            self.assertTrue(direct["enabled"])
            self.assertTrue(direct["active"])
            condition = next(
                record for record in records
                if record["script_path"] == "status_guard.sh")
            self.assertEqual(condition["directive"], "ExecCondition")
            template = next(
                record for record in records
                if record["script_path"] ==
                "run_ib_scalping_main_%i_paper.sh")
            self.assertEqual(
                template["script_kind"], "template-root-wrapper")
            self.assertIsNone(template["instance"])
            instance = next(
                record for record in records
                if record["script_path"] ==
                "run_ib_scalping_main_alpha_paper.sh")
            self.assertEqual(instance["instance"], "alpha")
            self.assertEqual(instance["unit_file_state"], "enabled")
            self.assertEqual(instance["active_state"], "inactive")
            research = next(
                record for record in records
                if record["script_path"] == "scripts/oos_probe.py")
            self.assertEqual(
                research["repository_scope"], "external-worktree")
            self.assertEqual(research["execution_root"], external.as_posix())
            self.assertTrue(research["exists"])
            serialized = json.dumps(records, sort_keys=True)
            self.assertNotIn("ExecStart=", serialized)

    def test_cron_and_process_inventory_record_only_matched_scripts(
            self) -> None:
        with tempfile.TemporaryDirectory(
                prefix="hepta-wrapper-runtime-") as temporary:
            root = Path(temporary).resolve()
            wrapper = root / "run_fixture.sh"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            system_cron = root / "system-crontab"
            system_cron.write_text(
                "SECRET_FILE=/tmp/not-a-command.sh\n"
                f"* * * * * fixture {wrapper} --token cron-secret\n",
                encoding="utf-8")
            proc = root / "proc"
            (proc / "123").mkdir(parents=True)
            (proc / "123/cwd").symlink_to(
                root, target_is_directory=True)
            (proc / "123/cmdline").write_bytes(
                b"/bin/bash\0run_fixture.sh"
                b"\0--token\0process-secret\0")

            def run(arguments: list[str]
                    ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(arguments, ["crontab", "-l"])
                return subprocess.CompletedProcess(
                    arguments, 0,
                    stdout=(
                        f"@reboot {wrapper} --token user-secret\n"),
                    stderr="")

            errors: set[str] = set()
            with (
                    mock.patch.object(
                        inventory, "SYSTEM_CRON_FILES", (system_cron,)),
                    mock.patch.object(
                        inventory, "SYSTEM_CRON_DIRECTORIES", ()),
                    mock.patch.object(
                        inventory, "_run_read_only", side_effect=run)):
                cron, sources = inventory._cron_inventory(
                    root, {wrapper.name}, {wrapper.name}, set(), errors)
            processes = inventory._process_inventory(
                root, {wrapper.name}, {wrapper.name}, set(), errors,
                proc=proc)
            self.assertFalse(errors)
            self.assertEqual(
                sources, [f"system:{system_cron}", "user:current"])
            self.assertEqual(len(cron), 2)
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0]["source_id"], "pid:123")
            serialized = json.dumps(
                {"cron": cron, "processes": processes}, sort_keys=True)
            self.assertNotIn("cron-secret", serialized)
            self.assertNotIn("user-secret", serialized)
            self.assertNotIn("process-secret", serialized)
            self.assertNotIn("SECRET_FILE", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
