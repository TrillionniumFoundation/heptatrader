"""Execute orchestration with inert subprocess seams; real fixtures run in CI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import accept_core_release as acceptance

SHA = "a" * 40


class CoreReleaseAcceptanceTests(unittest.TestCase):
    def fixture(self, root: Path, fail=None, tamper=False, untracked=False):
        calls = []
        candidate = None

        def run(argv, **kwargs):
            nonlocal candidate
            calls.append(argv)
            self.assertIs(kwargs["check"], True)
            stdout = ""
            if "rev-parse" in argv:
                stdout = (acceptance.REFERENCE_SHA if "-C" in argv else SHA) + "\n"
            elif argv[:2] == ["git", "status"] and untracked:
                stdout = "?? injected-untracked-file\n"
            elif "show" in argv:
                stdout = "1789279500\n"
            elif argv[:2] == ["git", "init"]:
                reference = Path(argv[2])
                reference.mkdir()
                (reference / "VERSION").write_text("0.3.0\n")
            elif argv[:2] == ["cmake", "-S"]:
                reference = Path(argv[argv.index("-S") + 1])
                build = Path(argv[argv.index("-B") + 1])
                reply = build / ".cmake/api/v1/reply"
                reply.mkdir(parents=True)
                (reply / "index-fixture.json").write_text(json.dumps({
                    "reply": {"codemodel-v2": {"jsonFile": "model.json"}}}))
                (reply / "model.json").write_text(json.dumps({
                    "paths": {"source": str(reference), "build": str(build)},
                    "configurations": [{"name": "Release", "targets": [
                        {"name": "fixture-app", "jsonFile": "app.json"}]}]}))
                (reply / "app.json").write_text(json.dumps({"name": "fixture-app",
                    "type": "EXECUTABLE", "install": {"destinations": [{"path": "bin"}]}}))
            elif argv[:3] == ["sudo", "mktemp", "-d"]:
                stdout = "/tmp/hepta-accept-source.ABCDef12\n"
            phase = None
            if "scripts/run_python_tests.py" in argv:
                phase = argv[argv.index("--lane") + 1]
            elif "scripts/run_release_simulator_smoke.py" in argv:
                phase = "smoke"
            elif "tests/systemd_simulator_smoke.py" in argv:
                phase = "systemd"
                if tamper:
                    candidate.write_bytes(b"substituted after testing")
            if fail is not None and phase == fail:
                raise subprocess.CalledProcessError(29, argv)
            if "scripts/build_release_package.py" in argv:
                target = Path(argv[argv.index("--output") + 1])
                source = argv[argv.index("--source-sha") + 1]
                target.write_bytes(source.encode())
                Path(str(target) + ".sha256").write_text(hashlib.sha256(target.read_bytes()).hexdigest() + "  package\n")
                if source == SHA:
                    candidate = target
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        return run, calls

    def test_one_digest_flows_through_both_installed_acceptances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, calls = self.fixture(root)
            receipt = acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(receipt["checks"], list(acceptance.CHECKS))
            candidate = str(root / "dist" / f"heptatrader-0.3.0-core-{SHA}.tar.gz")
            sha = hashlib.sha256(SHA.encode()).hexdigest()
            process = next(c for c in calls if "--lane" in c and c[-1] == "process")
            self.assertIn("HEPTA_PROCESS_CANDIDATE_ARTIFACT=" + candidate, process)
            self.assertIn("HEPTA_PROCESS_CANDIDATE_SHA256=" + sha, process)
            self.assertIn("HEPTA_ISOLATED_PROCESS_TESTS=1", process)
            systemd = next(c for c in calls if "tests/systemd_simulator_smoke.py" in c)
            self.assertEqual(systemd[systemd.index("--artifact") + 1], candidate)
            self.assertEqual(systemd[systemd.index("--expected-sha256") + 1], sha)
            self.assertIn("HEPTA_DISPOSABLE_SYSTEMD_TEST=1", systemd)
            # env options must precede assignments; otherwise --chdir becomes
            # a command name instead of setting the protected source cwd.
            self.assertTrue(process[3].startswith("--chdir="))
            self.assertEqual(receipt["package_sha256"], sha)
            self.assertFalse(receipt["paper_authorized"])
            status_calls = [c for c in calls if c[:2] == ["git", "status"]]
            self.assertEqual(len(status_calls), 2)
            self.assertIn(":(exclude,top)dist", status_calls[-1])
            with self.assertRaises(ValueError):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)

    def test_every_failed_phase_and_changed_package_prevent_pass_receipt(self):
        for phase in ("install", "core", "smoke", "process", "systemd", "tamper"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "VERSION").write_text("0.3.0\n")
                run, calls = self.fixture(root, fail=phase, tamper=phase == "tamper")
                with self.assertRaises((subprocess.CalledProcessError, ValueError)):
                    acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
                self.assertFalse((root / "dist/core-acceptance.json").exists())
                if phase in ("process", "systemd", "tamper"):
                    self.assertTrue(any(c[:3] == ["sudo", "rm", "-rf"] for c in calls))
                if phase == "process":
                    self.assertFalse(any("tests/systemd_simulator_smoke.py" in c for c in calls))

    def test_untracked_checkout_content_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.3.0\n")
            run, calls = self.fixture(root, untracked=True)
            with self.assertRaisesRegex(ValueError, "untracked files"):
                acceptance.accept(root / "build", root / "dist", SHA, root=root, run=run)
            self.assertTrue(any(c[:2] == ["git", "status"] for c in calls))
            self.assertFalse((root / "dist/core-acceptance.json").exists())


class ReferenceInstallTargetsTests(unittest.TestCase):
    def configure(self, root):
        source, build = root / "source", root / "build"
        source.mkdir()
        query = build / ".cmake/api/v1/query"
        query.mkdir(parents=True)
        (query / "codemodel-v2").touch()
        (source / "app.cpp").write_text("int main() { return 0; }\n")
        (source / "unused.cpp").write_text("#error unrelated historical target must not build\n")
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\nproject(InstallSlice LANGUAGES CXX)\n"
            "add_executable(installed-app app.cpp)\n"
            "add_executable(uninstalled-test unused.cpp)\n"
            "install(TARGETS installed-app RUNTIME DESTINATION bin)\n")
        subprocess.run(["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release"],
                       check=True, capture_output=True, timeout=60)
        return source, build

    def test_real_install_omits_unrelated_broken_test_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, build = self.configure(root)
            targets = acceptance.installed_executable_targets(source, build)
            self.assertEqual(targets, ["installed-app"])
            subprocess.run(["cmake", "--build", build, "--target", *targets],
                           check=True, capture_output=True, timeout=60)
            stage = root / "stage"
            subprocess.run(["cmake", "--install", build, "--prefix", stage],
                           check=True, capture_output=True, timeout=60)
            subprocess.run([stage / "bin/installed-app"], check=True, timeout=10)
            self.assertFalse((build / "uninstalled-test").exists())

    def test_new_installed_target_is_discovered_without_another_list(self):
        with tempfile.TemporaryDirectory() as directory:
            source, build = self.configure(Path(directory))
            with (source / "CMakeLists.txt").open("a") as stream:
                stream.write("add_executable(second-app app.cpp)\n"
                             "install(TARGETS second-app RUNTIME DESTINATION bin)\n")
            subprocess.run(["cmake", "-S", source, "-B", build],
                           check=True, capture_output=True, timeout=60)
            self.assertEqual(acceptance.installed_executable_targets(source, build),
                             ["installed-app", "second-app"])

    def test_missing_foreign_and_empty_models_cannot_skip_reference_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                acceptance.installed_executable_targets(root, root / "absent")
            source, build = self.configure(root)
            with self.assertRaisesRegex(ValueError, "source/build mismatch"):
                acceptance.installed_executable_targets(root / "foreign", build)
            reply = build / ".cmake/api/v1/reply"
            index = json.loads(next(reply.glob("index-*.json")).read_text())
            model_path = reply / index["reply"]["codemodel-v2"]["jsonFile"]
            model = json.loads(model_path.read_text())
            model["configurations"][0]["targets"] = []
            model_path.write_text(json.dumps(model))
            with self.assertRaisesRegex(ValueError, "no installed executable"):
                acceptance.installed_executable_targets(source, build)


if __name__ == "__main__":
    unittest.main()
