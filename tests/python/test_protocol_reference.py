from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
import importlib.util

ROOT = Path(__file__).resolve().parents[2]

class ProtocolReferenceTests(unittest.TestCase):
    def renderer(self):
        spec = importlib.util.spec_from_file_location("hepta_protocol_renderer", ROOT / "scripts/render_protocol_reference.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_single_tool_field_source_and_generated_profile(self):
        renderer = self.renderer()
        renderer.sync_tool_fields(ROOT)
        rows = renderer.parse_tool_fields((ROOT / renderer.TOOL_FIELDS).read_text())
        self.assertEqual([number for _, number, _, enabled in rows if enabled], list(range(1, 27)))
        self.assertEqual([number for _, number, _, enabled in rows if not enabled], list(range(27, 35)))
        self.assertEqual(renderer.render(ROOT), (ROOT / "docs/technical/wire-field-reference.md").read_text())

    def test_duplicate_or_malformed_tags_are_rejected(self):
        renderer = self.renderer()
        good = 'HEPTA_TOOL_FIELD(Token, 1, "token", 1)'
        for bad in (good + "\n" + good, good + '\nHEPTA_TOOL_FIELD(Other, 1, "other", 0)',
                    'HEPTA_TOOL_FIELD(Token, 256, "token", 1)',
                    'HEPTA_TOOL_FIELD(Token, 1, "token", 2)'):
            with self.subTest(value=bad), self.assertRaises(ValueError): renderer.parse_tool_fields(bad)

    def test_mcp_drift_fails_and_regenerates(self):
        renderer = self.renderer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (renderer.TOOL_FIELDS, renderer.MCP_SOURCE):
                target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((ROOT / name).read_text())
            target = root / renderer.MCP_SOURCE
            target.write_text(target.read_text().replace('"session_token": 1,', '"session_token": 99,'))
            with self.assertRaises(ValueError): renderer.sync_tool_fields(root)
            renderer.sync_tool_fields(root, write=True)
            renderer.sync_tool_fields(root)

    def test_documented_vectors_against_real_cpp_codecs(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "native compiler is required; do not silently skip wire evidence")
        with tempfile.TemporaryDirectory(prefix="hepta-wire-vectors-") as directory:
            binary = str(Path(directory) / "vectors")
            subprocess.run([compiler, "-std=c++11", "-O0", str(ROOT/"tests/protocol_reference_vectors.cpp"),
                            str(ROOT/"HeptaTrade/execution/execution_service_protocol.cpp"),
                            str(ROOT/"HeptaTrade/execution/execution_event_feed.cpp"),
                            str(ROOT/"HeptaTrade/tool_host/session_supervisor_protocol.cpp"), "-o", binary],
                           check=True, capture_output=True, timeout=60)
            result = subprocess.run([binary], check=True, capture_output=True, timeout=5)
            self.assertIn(b"protocol golden vectors PASS", result.stdout)

if __name__ == "__main__": unittest.main()
