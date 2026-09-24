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
                                   f"-DHEPTA_NM_EXECUTABLE={shutil.which('nm')}", "-P",
                                   str(ROOT / "cmake/verify_gateway_forbidden_symbols.cmake")],
                                  capture_output=True, text=True, timeout=10)

    def test_innocent_symbol_growth_is_observed_without_a_fake_budget(self):
        source = "\n".join(f"int ordinary_{i}() {{ return {i}; }}" for i in range(1250))
        result = self.check_binary(source + "\nint main() { return ordinary_0(); }\n")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Gateway privileged-symbol boundary PASS", output)
        self.assertRegex(output, r"defined_symbols=\d+ observed")
        self.assertNotIn("Advisory Gateway symbol growth", output)

    def test_privileged_execution_symbol_remains_a_hard_error(self):
        result = self.check_binary("class ExecutionCoordinator { public: void Send(); };\n"
                                   "void ExecutionCoordinator::Send() {}\n"
                                   "int main() { ExecutionCoordinator c; c.Send(); }\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("privileged Execution Service symbols", result.stderr)
