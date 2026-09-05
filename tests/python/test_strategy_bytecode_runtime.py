from __future__ import annotations

from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAGIC = b"HEPTA_STRATEGY_BYTECODE_V1\n"
MAXIMUM = 9_000_000_000_000_000
SCALE = 1_000_000


def reference(code: list[tuple[int, int]], inputs: list[int], fuel: int) -> str:
    """Independent variable-length-stack interpreter using Python integers."""
    for op, arg in code:
        if not 1 <= op <= 15:
            return "invalid"
        if op == 1 and not -MAXIMUM <= arg <= MAXIMUM:
            return "invalid"
        if op == 2 and not 0 <= arg < len(inputs):
            return "invalid"
        if op in (3, 4) and not 0 <= arg < 16:
            return "invalid"
        if op in (11, 12) and not 0 <= arg < len(code):
            return "invalid"
        if op in (5, 6, 7, 8, 9, 10, 13, 14, 15) and arg:
            return "invalid"
    if not 1 <= len(code) <= 4096:
        return "invalid"
    state = [0] * 16
    stack: list[int] = []
    pc = steps = 0
    status, utility, target = 4, 0, 0
    while pc < len(code):
        if steps == fuel:
            status = 1
            break
        op, arg = code[pc]
        pc += 1
        steps += 1
        required = 2 if op in (5, 6, 7, 8, 9, 10, 15) else 1 if op in (4, 12, 13, 14) else 0
        if len(stack) < required or (op == 15 and len(stack) != 2):
            status = 2
            break
        if op in (1, 2, 3):
            value = arg if op == 1 else inputs[arg] if op == 2 else state[arg]
        elif op == 4:
            state[arg] = stack.pop()
            continue
        elif op == 11:
            pc = arg
            continue
        elif op == 12:
            if stack.pop() == 0:
                pc = arg
            continue
        elif op == 13:
            value = stack[-1]
        elif op == 14:
            stack.pop()
            continue
        elif op == 15:
            status, utility, target = 0, stack[0], stack[1]
            break
        else:
            right, left = stack.pop(), stack.pop()
            if op == 8 and right == 0:
                status = 3
                break
            if op in (7, 8):
                numerator = left * right if op == 7 else left * SCALE
                denominator = SCALE if op == 7 else right
                value = abs(numerator) // abs(denominator)
                if (numerator < 0) != (denominator < 0):
                    value = -value
            else:
                value = {5: lambda: left + right, 6: lambda: left - right,
                         9: lambda: SCALE if left < right else 0,
                         10: lambda: SCALE if left == right else 0}[op]()
            if not -MAXIMUM <= value <= MAXIMUM:
                status = 3
                break
        if len(stack) == 64:
            status = 2
            break
        stack.append(value)
    return " ".join(map(str, [status, steps, utility, target, *state]))


class StrategyBytecodeRuntimeTests(unittest.TestCase):
    def compile(self, directory: str, name: str, sources: list[str], extra: list[str]) -> Path:
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "g++ is required for bytecode runtime verification")
        binary = Path(directory) / name
        command = [str(compiler), "-std=c++17", "-O1", "-g0", "-DNDEBUG", "-Wall", "-Wextra",
                   "-Wpedantic", "-Werror", "-fno-elide-constructors", "-pthread", "-I", str(ROOT / "HeptaTrade"),
                   *[str(ROOT / s) for s in sources], *extra, "-o", str(binary)]
        built = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        return binary

    def test_signed_execution_kernel_guards_and_checkpoint_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = self.compile(directory, "runtime", [
                "tests/strategy_bytecode_runtime_tests.cpp",
                "HeptaTrade/strategy_runtime/strategy_bytecode_runtime.cpp",
                "HeptaTrade/strategy_runtime/strategy_artifact_verifier.cpp",
                "HeptaTrade/strategy_runtime/strategy_checkpoint_store.cpp",
                "HeptaTrade/strategy_runtime/strategy_proposal.cpp"], ["-Wl,--wrap=fork,--wrap=prctl,--wrap=EVP_DigestInit_ex",
                "-Wl,--wrap=_ZNSt6chrono3_V212steady_clock3nowEv", "-lcrypto"])
            run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("vm_assertions=", run.stdout)
            self.assertIn("vm_denied_syscall_probes=11", run.stdout)

    def test_independent_integer_oracle(self) -> None:
        rng = random.Random(20260905)
        rows, expected = [], []
        for _ in range(5000):
            inputs = [rng.choice([-MAXIMUM, -SCALE, -1, 0, 1, SCALE, MAXIMUM]) for _ in range(3)]
            size = rng.randrange(1, 40)
            code = []
            for _ in range(size):
                op = rng.randrange(1, 17)
                if op == 1:
                    arg = rng.choice(inputs + [rng.randint(-MAXIMUM, MAXIMUM), MAXIMUM + 1])
                elif op == 2:
                    arg = rng.randrange(-1, 4)
                elif op in (3, 4):
                    arg = rng.randrange(-1, 17)
                elif op in (11, 12):
                    arg = rng.randrange(-1, size + 1)
                else:
                    arg = 0
                code.append((op, arg))
            # Half the corpus explicitly exercises valid arithmetic/state paths.
            if rng.randrange(2):
                op = rng.randrange(5, 11)
                code = [(1, inputs[0]), (1, inputs[1]), (op, 0), (13, 0), (4, 0), (2, 2), (15, 0)]
            fuel = rng.randrange(1, 60)
            encoded = MAGIC + b"".join(struct.pack(">Bq", op, arg) for op, arg in code)
            rows.append(" ".join([encoded.hex(), str(fuel), str(len(inputs)), *map(str, inputs)]))
            expected.append(reference(code, inputs, fuel))
        with tempfile.TemporaryDirectory() as directory:
            binary = self.compile(directory, "oracle", ["tests/strategy_bytecode_oracle_driver.cpp"], [])
            run = subprocess.run([str(binary)], input="\n".join(rows) + "\n", text=True,
                                 capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            actual = run.stdout.splitlines()
            self.assertEqual(len(actual), len(expected))
            for i, (a, b) in enumerate(zip(actual, expected)):
                self.assertEqual(a, b, f"oracle case {i}: {rows[i]}")


if __name__ == "__main__":
    unittest.main()
