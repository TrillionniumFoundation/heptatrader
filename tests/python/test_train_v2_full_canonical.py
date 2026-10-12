"""Adversarial full-Cargo qualification parsing; all subprocesses mocked."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from train_v2.full_canonical_workspace import (
    LOCK, PIN, CRATES, QualificationError,
    parse_result, resolve_full_graph, verify_source, write_once,
)


class NativeResultsTests(unittest.TestCase):
    @staticmethod
    def output(crate,passed=None,ignored=None):
        want,ig,marker=CRATES[crate]
        n=want if passed is None else passed
        i=ig if ignored is None else ignored
        return (
            "Finished test profile\n     Running "+marker+" (target/debug/deps/test)\n"
            +f"test result: ok. {n} passed; 0 failed; {i} ignored; "
            +"0 measured; 0 filtered out; finished in 1.2s\n")

    def test_original_ledger_and_owner_counts_are_exact_and_ignored_retained(self):
        for crate in ("codex-hepta-learning-ledger",
                      "codex-hepta-learning-artifacts"):
            r=parse_result(self.output(crate),crate)
            self.assertEqual(r["passed"],CRATES[crate][0])
            self.assertEqual(r["ignored"],1)

    def test_actual_agentd_owner_target_requires_at_least_one_real_test(self):
        good=self.output("codex-hepta-agentd",passed=2)
        self.assertEqual(parse_result(good,"codex-hepta-agentd")["passed"],2)
        with self.assertRaisesRegex(QualificationError,"GATES"):
            parse_result(self.output("codex-hepta-agentd",passed=0),
                         "codex-hepta-agentd")

    def test_wrong_test_binary_identity_does_not_pass(self):
        with self.assertRaisesRegex(QualificationError,"RUNNER_IDENTITY"):
            parse_result("test result: ok. 118 passed; 0 failed; "
                         "1 ignored; 0 measured; 0 filtered out;",
                         "codex-hepta-learning-ledger")

    def test_no_report_on_failed_filtered_or_extra_ignored(self):
        original=self.output("codex-hepta-learning-ledger")
        for changed in (
            original.replace("0 failed;","1 failed;"),
            original.replace("0 filtered out;","1 filtered out;"),
            original.replace("1 ignored;","0 ignored;"),
            original.replace("118 passed;","117 passed;"),
        ):
            with self.subTest(changed=changed[-95:]):
                with self.assertRaises(QualificationError):
                    parse_result(changed,"codex-hepta-learning-ledger")

    def test_duplicate_test_summary_never_marked_pass(self):
        s=self.output("codex-hepta-learning-artifacts")
        with self.assertRaisesRegex(QualificationError,"UNIQUE"):
            parse_result(s+s,"codex-hepta-learning-artifacts")

    def test_full_workspace_metadata_requires_all_193_members(self):
        data={"workspace_members":list(range(193)),
              "packages":[{"name":n, "id":i} for i,n in enumerate(CRATES)]
                         +[{"name":f"other_{i}", "id":i+3} for i in range(193)]}
        mock=subprocess.CompletedProcess([],0,json.dumps(data).encode(),b"")
        with patch("train_v2.full_canonical_workspace.subprocess.run",
                   return_value=mock) as called:
            receipt=resolve_full_graph(Path("/unused/manifest.toml"))
        args=called.call_args.args[0]
        self.assertIn("--offline",args)
        self.assertIn("--locked",args)
        self.assertEqual(receipt["workspace_members"],193)
        self.assertTrue(receipt["offline_locked_full_dependency_graph"])
        bad=dict(data)
        bad["packages"]=list(data["packages"])
        bad["packages"][4]={"name":"unrelated","id":-5}
        with patch("train_v2.full_canonical_workspace.subprocess.run",
                   return_value=subprocess.CompletedProcess(
                      [],0,json.dumps(bad).encode(),b"")):
            with self.assertRaisesRegex(QualificationError,"MEMBER_MISSING"):
                resolve_full_graph(Path("/unused/manifest.toml"))
        data["workspace_members"].pop()
        with patch("train_v2.full_canonical_workspace.subprocess.run",
                   return_value=subprocess.CompletedProcess(
                      [],0,json.dumps(data).encode(),b"")):
            with self.assertRaisesRegex(QualificationError,"NOT_RESOLVED"):
                resolve_full_graph(Path("/unused/manifest.toml"))

    def test_wrong_cargo_lock_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/"codex-rs").mkdir()
            (root/"codex-rs/Cargo.lock").write_bytes(b"false lock")
            def fake_git(_,*cmd):
                if cmd[-1]=="HEAD":return PIN
                if cmd==("status","--porcelain=v1"):return ""
                return ""
            with patch("train_v2.full_canonical_workspace.git",
                       side_effect=fake_git):
                with self.assertRaisesRegex(QualificationError,"LOCK_CHANGED"):
                    verify_source(root)

    def test_immutable_receipt_is_private_fsynced_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            folder.chmod(0o700)
            sha=write_once(folder,"test.json",{"authority":False})
            self.assertEqual(len(sha),64)
            self.assertEqual(folder.joinpath("test.json").stat().st_mode & 0o777,
                             0o600)
            with self.assertRaisesRegex(QualificationError,"EXISTS"):
                write_once(folder,"test.json",{"authority":True})
            self.assertFalse(json.loads((folder/"test.json").read_text())
                             ["authority"])
