"""Train V2 pinned native Cargo qualification: strictly no production I/O."""
import hashlib
import pathlib
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from train_v2.native_qualification import (
    PIN, QualificationError, CRATES, THIRD_PARTY,
    blob_sha, toml_value, validate_lock, native_test, prepare,
    verify_native_snapshot,
)

class NativeQualificationUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
        self.original=self.root/"original"
        self.original.mkdir()
        (self.original/"codex-rs").mkdir()
        self.build=self.root/"isolated-qualification"

    def lock_files(self, *, locked_version="1.0.0", locked_hash="a"*64):
        full='''version = 3
[[package]]
name = "valid-package"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "'''+("a"*64)+'''"
[[package]]
name = "codex-hepta-learning-ledger"
version = "0.0.0"
'''
        subset='''version = 3
[[package]]
name = "valid-package"
version = "'''+locked_version+'''"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "'''+locked_hash+'''"
'''
        (self.original/"codex-rs/Cargo.lock").write_text(full)
        self.build.mkdir(exist_ok=True)
        (self.build/"src").mkdir(exist_ok=True)
        (self.build/"src/Cargo.lock").write_text(subset)

    def test_exact_git_blob_preimage(self):
        expected=hashlib.sha1(b"blob 3"+bytes([0])+b"abc").hexdigest()
        self.assertEqual(blob_sha(b"abc"),expected)
        self.assertNotEqual(blob_sha(b"abc"),blob_sha(b"abcd"))

    def test_workspace_dep_serializer_is_valid_toml(self):
        val={"version":"1.2.3","features":["derive","rc"]}
        text='[workspace.dependencies]\nserde = '+toml_value(val)
        self.assertEqual(tomllib.loads(text)["workspace"]["dependencies"]["serde"],val)

    def test_upstream_original_lock_is_the_allowed_version_set(self):
        self.lock_files()
        self.assertEqual(validate_lock(self.build,self.original),1)

    def test_checksum_divergence_fails_closed(self):
        self.lock_files(locked_hash="b"*64)
        with self.assertRaisesRegex(QualificationError,"DEPENDENCY_OUTSIDE"):
            validate_lock(self.build,self.original)

    def test_version_upgrade_fails_closed(self):
        self.lock_files(locked_version="1.0.1")
        with self.assertRaisesRegex(QualificationError,"DEPENDENCY_OUTSIDE"):
            validate_lock(self.build,self.original)

    def test_build_dir_must_be_fresh(self):
        self.build.mkdir()
        sentinel=self.build/"do-not-delete.txt"
        sentinel.write_text("untouched")
        with self.assertRaisesRegex(QualificationError,"FRESH_QUALIFICATION_OUTPUT"):
            prepare(self.original,self.build,{})
        self.assertEqual(sentinel.read_text(),"untouched")

    def test_source_symlink_refused(self):
        other=self.root/"other"
        other.mkdir()
        link=self.root/"link"
        link.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(QualificationError,"UNSAFE_NATIVE_SOURCE"):
            verify_native_snapshot(link)

    def test_wrong_git_commit_refused(self):
        with patch("train_v2.native_qualification.run",
                   return_value=SimpleNamespace(stdout="badsha\n")):
            with self.assertRaisesRegex(QualificationError,"WRONG_CANONICAL_SOURCE_COMMIT"):
                verify_native_snapshot(self.original)

    def test_dirty_tracked_native_source_refused(self):
        results=[SimpleNamespace(stdout=PIN+"\n"),
                 SimpleNamespace(stdout=" M codex-rs/hepta-learning-ledger/src/lib.rs\n")]
        with patch("train_v2.native_qualification.run",side_effect=results):
            with self.assertRaisesRegex(QualificationError,"TRACKED_NATIVE_SOURCE_CHANGED"):
                verify_native_snapshot(self.original)

    def test_native_tests_require_real_locked_and_offline_commands(self):
        self.build.mkdir()
        (self.build/"src").mkdir()
        (self.build/"evidence").mkdir()
        returns=[
            SimpleNamespace(stdout="test result: ok. 92 passed; 0 failed; 1 ignored;\n",stderr=""),
            SimpleNamespace(stdout=(
                "test result: ok. 92 passed; 0 failed; 1 ignored;\n"
                "test result: ok. 118 passed; 0 failed; 1 ignored;\n"),stderr=""),
        ]
        with patch("train_v2.native_qualification.run",side_effect=returns) as calls:
            with patch("train_v2.native_qualification.validate_lock",return_value=48):
                counts,packages,sha=native_test(self.build,80,self.original)
        self.assertEqual(sorted(counts),[(92,0,1),(118,0,1)])
        self.assertEqual(packages,48)
        self.assertIn("--offline",calls.call_args_list[0].args[0])
        self.assertIn("--offline",calls.call_args_list[1].args[0])
        self.assertIn("--locked",calls.call_args_list[1].args[0])
        self.assertNotIn("--locked",calls.call_args_list[0].args[0])
        self.assertTrue(len(sha)==64)

    def test_fake_or_partial_native_test_success_is_rejected(self):
        self.build.mkdir()
        (self.build/"src").mkdir()
        (self.build/"evidence").mkdir()
        returns=[
            SimpleNamespace(stdout="test result: ok. 92 passed; 0 failed; 1 ignored;\n",stderr=""),
            SimpleNamespace(stdout="test result: ok. 210 passed; 0 failed; 2 ignored;\n",stderr=""),
        ]
        with patch("train_v2.native_qualification.run",side_effect=returns):
            with patch("train_v2.native_qualification.validate_lock",return_value=48):
                with self.assertRaisesRegex(QualificationError,"ACTUAL_NATIVE_LEDGER_OWNER_TEST_COUNTS_CHANGED"):
                    native_test(self.build,80,self.original)

    def test_production_authority_is_not_a_test_dependency(self):
        self.assertEqual(len(CRATES),6)
        self.assertEqual(len(THIRD_PARTY),5)
        self.assertNotIn("hepta-agentd",CRATES)
