import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("weight_launch", Path(__file__).resolve().parents[2] / "scripts/run_verified_weight_research.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "worker.py").write_text("pass\n")
        self.data = {"schema": "heptatrader.weight-research-release.v1", "files": {"worker.py": hashlib.sha256(b"pass\n").hexdigest()}}
    def seal(self):
        p = self.root / "release-manifest.json"
        p.write_text(json.dumps(self.data))
        return p, hashlib.sha256(p.read_bytes()).hexdigest()
    def test_valid_release(self):
        root, _ = M.verify_release(*self.seal())
        self.assertEqual(root, self.root)
    def test_tampered_source_fails(self):
        args = self.seal()
        (self.root / "worker.py").write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "FILE_CHANGED"):
            M.verify_release(*args)
    def test_extra_python_fails(self):
        args = self.seal()
        (self.root / "extra.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "PYTHON_INVENTORY"):
            M.verify_release(*args)
    def test_parent_escape_fails(self):
        self.data["files"]["../escape.py"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "RELEASE_PATH"):
            M.verify_release(*self.seal())

if __name__ == "__main__":
    unittest.main()
