from __future__ import annotations
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_ib_candidate_artifact as artifact

class CampaignProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in artifact.TRUSTED_BUILDER_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture " + name)
            path.chmod(0o600)

    def build(self):
        return artifact.create_builder_provenance(self.root, "example/builder@sha256:"+"a"*64,
                                                 "sha256:"+"b"*64, "c"*64, "d"*64)

    def test_changing_controller_changes_bound_provenance(self):
        first = self.build()
        path = self.root / "scripts/run_ib_paper_campaign.py"
        path.write_text("changed controller behavior")
        with self.assertRaises(artifact.ArtifactError):
            artifact.validate_builder(first, trusted_root=self.root)
        second = self.build()
        self.assertNotEqual(first["bundle_sha256"], second["bundle_sha256"])

    def test_missing_controller_and_unbound_old_receipt_are_rejected(self):
        receipt = self.build()
        receipt["trusted_files"].pop("scripts/run_ib_paper_campaign.py")
        with self.assertRaises(artifact.ArtifactError): artifact.validate_builder(receipt)
        (self.root / "scripts/run_ib_paper_campaign.py").unlink()
        with self.assertRaises(artifact.ArtifactError): self.build()

if __name__ == "__main__": unittest.main()
