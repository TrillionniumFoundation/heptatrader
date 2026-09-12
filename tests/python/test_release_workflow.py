from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_tagged_release_is_version_bound_and_immutable_evidence(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('tags: ["v*.*.*"]', workflow)
        self.assertIn('test "v${version}" = "${GITHUB_REF_NAME}"', workflow)
        self.assertIn('heptatrader.tagged-release-manifest.v1', workflow)
        self.assertIn("release-manifest.json.sha256", workflow)
        self.assertIn("retention-days: 90", workflow)
        self.assertIn("persist-credentials: false", workflow)


if __name__ == "__main__":
    unittest.main()
