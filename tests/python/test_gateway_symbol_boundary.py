"""Exercise the binary boundary rather than asserting CMake source spellings."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GatewaySymbolBoundaryTests(unittest.TestCase):
    def check_binary(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cpp, binary = root / "fixture.cpp", root / "fixture"
            cpp.write_text(source)
            compiled = subprocess.run(["c++", "-O0", str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            return subprocess.run(["cmake", f"-DHEPTA_GATEWAY_BINARY={binary}",
                                   f"-DHEPTA_NM_EXECUTABLE={shutil.which('nm')}",
                                   "-DHEPTA_GATEWAY_REPORT_RELEASE_BUDGET=ON", "-P",
                                   str(ROOT / "cmake/verify_gateway_forbidden_symbols.cmake")],
                                  capture_output=True, text=True, timeout=10)

    def test_innocent_symbol_growth_is_advisory_not_a_build_failure(self):
        source = "\n".join(f"int ordinary_{i}() {{ return {i}; }}" for i in range(1250))
        result = self.check_binary(source + "\nint main() { return ordinary_0(); }\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Advisory Gateway symbol growth", result.stderr)

    def test_privileged_execution_symbol_remains_a_hard_error(self):
        result = self.check_binary("class ExecutionCoordinator { public: void Send(); };\n"
                                   "void ExecutionCoordinator::Send() {}\n"
                                   "int main() { ExecutionCoordinator c; c.Send(); }\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("privileged Execution Service symbols", result.stderr)
