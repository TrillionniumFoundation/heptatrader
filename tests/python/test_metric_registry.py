from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs/verification/metric-registry-v1.json"
TELEMETRY_CPP = ROOT / "HeptaTrade/observability/runtime_telemetry.cpp"
TELEMETRY_H = ROOT / "HeptaTrade/observability/runtime_telemetry.h"
METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,95}$")
CALL_PATTERNS = {
    "counter": re.compile(
        r"(?:\.|->)IncrementKey\s*\(\s*\"([a-z][a-z0-9_]*)\"",
        re.MULTILINE,
    ),
    "gauge": re.compile(
        r"(?:\.|->)SetGaugeKey\s*\(\s*\"([a-z][a-z0-9_]*)\"",
        re.MULTILINE,
    ),
    "histogram": re.compile(
        r"(?:\.|->)ObserveLatencyKey\s*\(\s*\"([a-z][a-z0-9_]*)\"",
        re.MULTILINE,
    ),
}
LATENCY_SCOPE = re.compile(
    r"RuntimeLatencyScope\s+[A-Za-z_][A-Za-z0-9_]*\s*\(\s*"
    r"\"([a-z][a-z0-9_]*)\"",
    re.MULTILINE,
)


class MetricRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.metrics = self.document["metrics"]
        self.by_name = {item["name"]: item for item in self.metrics}

    def _literal_runtime_metrics(self) -> dict[str, str]:
        discovered: dict[str, str] = {}
        for path in sorted((ROOT / "HeptaTrade").rglob("*")):
            if path.suffix.lower() not in {".c", ".cc", ".cpp", ".h", ".hpp"}:
                continue
            text = path.read_text(encoding="utf-8-sig")
            for metric_type, pattern in CALL_PATTERNS.items():
                for name in pattern.findall(text):
                    prior = discovered.setdefault(name, metric_type)
                    self.assertEqual(
                        prior,
                        metric_type,
                        f"{name} is emitted as both {prior} and {metric_type}",
                    )
            for name in LATENCY_SCOPE.findall(text):
                prior = discovered.setdefault(name, "histogram")
                self.assertEqual(
                    prior,
                    "histogram",
                    f"{name} is emitted as both {prior} and histogram",
                )
        return discovered

    def test_registry_is_closed_bounded_and_unique(self) -> None:
        self.assertEqual("heptatrader.metric-registry.v1", self.document["schema"])
        self.assertEqual(len(self.metrics), len(self.by_name))
        policy = self.document["policy"]
        self.assertTrue(policy["bounded_labels_only"])
        self.assertEqual("redacted", policy["unknown_label_value"])
        self.assertEqual(2048, policy["series_cap"])
        allowed = set(policy["allowed_labels"])
        forbidden = set(policy["forbidden_labels"])
        self.assertFalse(allowed & forbidden)
        for item in self.metrics:
            self.assertRegex(item["name"], METRIC_NAME)
            self.assertIn(item["type"], {"counter", "gauge", "histogram"})
            self.assertIn(item["state"], {"implemented", "declared"})
            self.assertTrue(item["description"].strip())
            self.assertTrue(item["unit"].strip())
            self.assertRegex(item["owner"], r"^hepta(?:\.[a-z0-9-]+)+$")
            self.assertEqual(len(item["labels"]), len(set(item["labels"])))
            self.assertTrue(set(item["labels"]).issubset(allowed), item["name"])
            self.assertFalse(set(item["labels"]) & forbidden, item["name"])

    def test_every_literal_runtime_emission_is_registered_with_correct_type(self) -> None:
        discovered = self._literal_runtime_metrics()
        self.assertTrue(discovered)
        missing = sorted(set(discovered) - set(self.by_name))
        self.assertEqual([], missing, f"unregistered runtime metrics: {missing}")
        for name, metric_type in discovered.items():
            self.assertEqual(metric_type, self.by_name[name]["type"], name)
            self.assertEqual("implemented", self.by_name[name]["state"], name)

    def test_histogram_buckets_and_series_cap_match_runtime(self) -> None:
        cpp = TELEMETRY_CPP.read_text(encoding="utf-8-sig")
        bucket_match = re.search(
            r"kLatencyBuckets\[12\]\s*=\s*\{([^}]*)\}",
            cpp,
            re.DOTALL,
        )
        self.assertIsNotNone(bucket_match)
        assert bucket_match is not None
        buckets = [
            int(value)
            for value in re.findall(r"[0-9]+", bucket_match.group(1))
        ]
        self.assertEqual(
            self.document["policy"]["histogram_buckets_us"], buckets
        )

        header = TELEMETRY_H.read_text(encoding="utf-8-sig")
        cap_match = re.search(r"kMaximumSeries\s*=\s*([0-9]+)", header)
        self.assertIsNotNone(cap_match)
        assert cap_match is not None
        self.assertEqual(
            self.document["policy"]["series_cap"], int(cap_match.group(1))
        )

    def test_latency_metric_names_do_not_lie_about_units(self) -> None:
        for item in self.metrics:
            name = item["name"]
            if item["type"] != "histogram":
                continue
            if name.endswith("_microseconds"):
                self.assertEqual("microseconds", item["unit"], name)
            if name.endswith("_milliseconds") or name.endswith("_ms"):
                self.assertEqual("milliseconds", item["unit"], name)


if __name__ == "__main__":
    unittest.main()
