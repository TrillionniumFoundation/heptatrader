"""Run actual Neuron data/recovery controls in ordinary CPU-only CI.

The separate real-device conformance suite still requires an explicit config.
An isolated interpreter keeps the sidecar's bare module names out of core CI.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
CPU_CLASSES = ("CausalDataTests", "ForwardEvaluationRecoveryTests", "TrainingCadenceRecoveryTests")


class NeuronLearningControlsTests(unittest.TestCase):
    def test_actual_cpu_controls_without_native_or_gpu_access(self):
        code = r'''
import ctypes
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import numpy

root = Path(sys.argv[1])
names = json.loads(sys.argv[2])
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("neuron_cpu_controls", root / "tests/test_learning.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
with patch.object(ctypes, "CDLL", side_effect=AssertionError("CPU controls may not load native/GPU libraries")), \
     patch.object(subprocess, "Popen", side_effect=AssertionError("CPU controls may not launch native/GPU processes")):
    spec.loader.exec_module(module)
    loader = unittest.TestLoader()
    suites = [loader.loadTestsFromTestCase(getattr(module, name)) for name in names]
    counts = {name: suite.countTestCases() for name, suite in zip(names, suites)}
    if loader.errors or any(count == 0 for count in counts.values()):
        raise AssertionError("CPU control discovery incomplete: " + repr(counts) + repr(loader.errors))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(suites))
    print(json.dumps({"classes": counts, "run": result.testsRun, "skipped": len(result.skipped)}))
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
'''
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1",
                           OMP_NUM_THREADS="1")
        environment.pop("HEPTA_NEURON_TEST_CONFIG", None)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-W", "error::ResourceWarning", "-c", code,
             str(ROOT / "research/neuron_learning"),
             json.dumps(CPU_CLASSES)],
            env=environment, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(set(summary["classes"]), set(CPU_CLASSES))
        self.assertEqual(summary["run"], sum(summary["classes"].values()))
        self.assertEqual(summary["skipped"], 0)
        print(f"Neuron CPU controls: {summary['run']} actual cases, no native/GPU access", flush=True)


if __name__ == "__main__":
    unittest.main()
