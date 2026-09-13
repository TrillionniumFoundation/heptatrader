from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

class ProtocolReferenceTests(unittest.TestCase):
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
