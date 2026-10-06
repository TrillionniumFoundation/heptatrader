from pathlib import Path
import sys, tempfile, unittest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_documentation as links

class DocumentationLinksTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'docs').mkdir()
            (root/'docs/guide.md').write_text('# Guide\n')
            (root/'README.md').write_text(text)
            return links.validate(root)
    def test_repo_links(self): self.assertEqual(links.validate(ROOT), [])
    def test_real_relative_links(self): self.assertEqual(self.check('[guide](docs/guide.md#section)'), [])
    def test_broken_link_fails(self): self.assertTrue(self.check('[missing](docs/absent.md)'))
    def test_outside_link_fails(self): self.assertTrue(self.check('[outside](../private)'))
    def test_external_and_anchors_need_no_network(self):
        self.assertEqual(self.check('[external](https://example.invalid/) [mail](mailto:a@b) [anchor](#x)'), [])
    def test_no_generated_headers_or_length_quotas(self): self.assertEqual(self.check('# Notes\nBrief notes.'), [])
    def test_code_example_links_are_not_navigation(self):
        self.assertEqual(self.check('```text\n[example](not-a-real-file)\n```\n'), [])
