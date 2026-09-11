from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CANONICAL = ROOT / ".github/workflows/canonical-full-suite.yml"
RESILIENCE = ROOT / ".github/workflows/resilience-periodic.yml"


def executable_text(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


class ResilienceWorkflowTests(unittest.TestCase):
    def test_legacy_required_contexts_are_inert(self) -> None:
        text = executable_text(CANONICAL)
        self.assertEqual(text.count("compatibility-only context"), 3)
        self.assertNotIn("actions/checkout@", text)
        self.assertNotIn("apt-get", text)
        self.assertNotIn("cmake -S", text)
        self.assertNotIn("cmake --build", text)
        self.assertNotIn("ctest --test-dir", text)
        self.assertNotIn("python3 scripts/", text)

    def test_periodic_lane_owns_both_sanitizer_compilers(self) -> None:
        text = executable_text(RESILIENCE)
        self.assertIn("name: reliability-runtime-sanitizers (g++)", text)
        self.assertIn("name: reliability-runtime-sanitizers (clang++)", text)
        self.assertIn(
            "cmake --build build/reliability-gcc --target hepta_core_test_binaries",
            text,
        )
        self.assertIn(
            "ctest --test-dir build/reliability-gcc --output-on-failure -L core",
            text,
        )
        self.assertIn(
            "cmake --build build/reliability-clang --target hepta_core_test_binaries",
            text,
        )
        self.assertIn(
            "ctest --test-dir build/reliability-clang --output-on-failure -L core",
            text,
        )

    def test_periodic_lane_is_scheduled_and_high_risk_path_scoped(self) -> None:
        text = RESILIENCE.read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertIn("cron: '17 2 * * *'", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pull_request:", text)
        for path in (
            "HeptaTrade/execution/**",
            "HeptaTrade/risk/**",
            "HeptaTrade/oms_journal.*",
            "HeptaTrade/state/**",
            "HeptaTrade/reconcile/**",
            "HeptaTrade/adapter_ib/**",
            "HeptaTrade/simulator/**",
        ):
            self.assertIn(path, text)

    def test_resilience_lane_is_read_only(self) -> None:
        text = RESILIENCE.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("environment: ib-paper", text)
        self.assertNotIn("HEPTA_QUALIFICATION_MUTATIONS", text)


if __name__ == "__main__":
    unittest.main()
