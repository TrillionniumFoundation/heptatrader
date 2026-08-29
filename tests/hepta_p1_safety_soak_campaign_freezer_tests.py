#!/usr/bin/env python3
"""Contract tests for the fixed P1 campaign freezer."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MODULE_PATH = ROOT / "scripts" / "hepta_p1_safety_soak_campaign_freezer.py"
SPEC = importlib.util.spec_from_file_location("p1_campaign_freezer", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
FREEZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FREEZER
SPEC.loader.exec_module(FREEZER)


def load_module(name: str, relative_path: str):
    specification = importlib.util.spec_from_file_location(
        name, ROOT / relative_path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


PLANNER = load_module(
    "p1_policy_planner_boundary",
    "scripts/hepta_p1_safety_soak_policy_planner.py")
LAUNCHER = load_module(
    "p1_shadow_launcher_boundary",
    "scripts/hepta_p1_shadow_admission_launcher.py")


def digest(label: str) -> str:
    return FREEZER.digest_bytes(label.encode("ascii"))


def metadata(mode: int = 0o600) -> os.stat_result:
    return os.stat_result((stat.S_IFREG | mode, 1, 1, 1, os.getuid(),
                           os.getgid(), 1, 1, 1, 1))


def snapshot(path: str, document: dict, *, sealed: bool) -> object:
    payload = FREEZER.canonical_bytes(document)
    body = document["body_sha256"] if sealed else \
        FREEZER.digest_bytes(payload)
    return FREEZER.Snapshot(
        Path(path), payload, metadata(), document,
        FREEZER.digest_bytes(payload), body)


def source_baseline(freezer_sha: str) -> object:
    files = []
    for role, (source_path, _installed_path) in sorted(
            FREEZER.SOURCE_PRODUCER_PATHS.items()):
        files.append({
            "path": source_path,
            "sha256": freezer_sha if role == "campaign_freezer"
            else digest("source-" + role),
        })
    manifest_sha = FREEZER.digest_bytes(
        FREEZER.json.dumps(
            files, ensure_ascii=True, allow_nan=False, sort_keys=True,
            separators=(",", ":")).encode("ascii"))
    document = {
        "schema": FREEZER.SOURCE_BASELINE_SCHEMA,
        "version": "round95",
        "generated_at": "2026-07-31T00:00:00Z",
        "git_head": "1" * 40,
        "source_manifest": {
            "file_count": len(files), "sha256": manifest_sha,
            "files": files,
        },
        "source_baseline_frozen": True,
        "clean_checkout_certified": True,
        "release_authorized": False,
        "paper_authorized": False,
        "live_authorized": False,
        "worktree_status_entry_count": 0,
        "blocked_reason": None,
        "excluded_unsafe_tree": "compat/unsafe-direct-broker",
    }
    return snapshot("/evidence/source-baseline.json", document, sealed=False)


def strategy_inputs() -> tuple[object, dict[str, object], str]:
    config = snapshot("/evidence/strategy.json", {
        "schema": FREEZER.STRATEGY_SCHEMA,
        "strategy_id": "eurusd-confirmed-momentum",
        "strategy_version": "v2",
        "paper_only": True,
        "live_authorized": False,
    }, sealed=False)
    runtime = {
        role: snapshot(f"/runtime/{filename}", {
            "role": role, "implementation": filename,
        }, sealed=False)
        for role, filename in FREEZER.RUNTIME_FILES.items()
    }
    _identity, _version, strategy_sha, _files = FREEZER.validate_strategy(
        config, runtime)
    return config, runtime, strategy_sha


def trading_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    cursor = start
    while len(result) < count:
        if (cursor.weekday() < 5 and cursor.isoformat() not in
                FREEZER.CALENDAR_EXCLUDED_DAYS_2026):
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def policies(strategy_sha: str, *, start: date = date(2026, 8, 3),
             segments: int = 22, iterations: int = 241) -> list[object]:
    zone = ZoneInfo(FREEZER.CALENDAR_TIMEZONE)
    values = []
    valid_after = int(datetime(
        start.year, start.month, start.day, 9, 0,
        tzinfo=zone).timestamp() * 1000)
    interval = FREEZER.POLICY_SLOT_INTERVAL_MS
    previous_teardown = 0
    for index in range(1, segments + 1):
        if previous_teardown:
            valid_after = FREEZER.required_valid_after_after_teardown(
                previous_teardown, interval)
        campaign_id = (
            f"hepta-p1-shadow-soak-round{94 + index}-"
            f"{start:%Y%m%d}")
        campaign = {
            "schema": "hepta.strategy-shadow-observation-campaign.v1",
            "campaign_id": campaign_id,
            "valid_after_ms": valid_after,
            "expires_at_ms": valid_after + interval * iterations,
            "slot_interval_ms": interval,
            "maximum_iterations": iterations,
            "maximum_lateness_ms": FREEZER.POLICY_MAXIMUM_LATENESS_MS,
            "shadow_only": True,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        }
        document = FREEZER.seal({
            "schema": FREEZER.FORMAL_POLICY_SCHEMA,
            "version": 1,
            "campaign_id": campaign_id,
            "campaign_sha256": FREEZER.digest_bytes(
                FREEZER.canonical_bytes(campaign)),
            "strategy_id": "eurusd-confirmed-momentum",
            "strategy_version": "v2",
            "strategy_sha256": strategy_sha,
            "valid_after_ms": valid_after,
            "expires_at_ms": valid_after + interval * iterations,
            "slot_interval_ms": interval,
            "maximum_iterations": iterations,
            "maximum_lateness_ms": FREEZER.POLICY_MAXIMUM_LATENESS_MS,
            "shadow_only": True,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_attempted": False,
            "direct_broker_access": False,
        })
        values.append(snapshot(
            f"/evidence/{campaign_id}.json", document, sealed=True))
        previous_teardown = (
            document["expires_at_ms"] +
            FREEZER.POST_FORMAL_TEARDOWN_GUARD_MS)
    return values


def bundle_inputs(*, start: date = date(2026, 8, 3), segments: int = 22,
                  iterations: int = 241) -> dict:
    freezer_sha = digest("installed-freezer")
    source = source_baseline(freezer_sha)
    config, runtime, strategy_sha = strategy_inputs()
    before_start = start - timedelta(days=3)
    now = int(datetime(
        before_start.year, before_start.month, before_start.day, 12, 0,
        tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    return {
        "source_baseline": source,
        "strategy_config": config,
        "strategy_runtime": runtime,
        "formal_policies": policies(
            strategy_sha, start=start, segments=segments,
            iterations=iterations),
        "expected_source_baseline_file_sha256": source.file_sha256,
        "campaign_id": "p1-safety-soak-round95",
        "domain_id": "alpha",
        "trading_timezone": FREEZER.CALENDAR_TIMEZONE,
        "independent_auditor_id": "independent-root-auditor",
        "producer": {
            "path": str(FREEZER.INSTALLED_EXECUTABLE),
            "file_sha256": freezer_sha,
        },
        "now_ms": now,
        "boottime_ns": 10_000_000_000,
        "boot_id": "12345678-1234-1234-1234-123456789abc",
        "freeze_id": "a" * 32,
    }


class CampaignFreezerTests(unittest.TestCase):
    def test_planner_freezer_launcher_production_policy_contract(self) -> None:
        values = bundle_inputs()
        frozen_policy = values["formal_policies"][0]
        policy = frozen_policy.document

        class FixedBuilder:
            MINIMUM_WARMUP_MS = PLANNER.LAUNCHER_WARMUP_MS
            SLOT_INTERVAL_MS = PLANNER.SLOT_INTERVAL_MS
            MAXIMUM_ITERATIONS = PLANNER.MAXIMUM_ITERATIONS
            MAXIMUM_LATENESS_MS = PLANNER.MAXIMUM_LATENESS_MS

            @staticmethod
            def build_policy(**_arguments):
                return copy.deepcopy(policy)

        launcher_start = policy["valid_after_ms"] - \
            PLANNER.LAUNCHER_WARMUP_MS
        produced = PLANNER.plan_policy(
            campaign_id=policy["campaign_id"],
            launcher_start_ms=launcher_start,
            strategy_path=Path("/evidence/strategy.json"),
            runtime_directory=Path("/runtime"),
            expected_strategy_sha256=policy["strategy_sha256"],
            builder=FixedBuilder(), now_ms=values["now_ms"])
        _validated, _slots, record = FREEZER.validate_formal_policy(
            snapshot(str(frozen_policy.path), produced, sealed=True),
            strategy_id=produced["strategy_id"],
            strategy_version=produced["strategy_version"],
            strategy_sha256=produced["strategy_sha256"],
            now_ms=values["now_ms"])
        round_number = int(
            LAUNCHER.FORMAL_CAMPAIGN.fullmatch(
                policy["campaign_id"]).group(1))
        configuration = LAUNCHER.LaunchConfiguration(
            probe_campaign_id=(
                f"hepta-p1-shadow-load-probe-round{round_number - 1}-"
                f"{policy['campaign_id'][-8:]}") ,
            formal_campaign_id=policy["campaign_id"],
            formal_start_ms=record["launcher_start_ms"])
        self.assertEqual(
            LAUNCHER._validated_policy_schedule(
                configuration, policy["campaign_id"], produced),
            (policy["valid_after_ms"], policy["maximum_iterations"]))

    def test_builds_exact_bundle_and_source_coded_calendar(self) -> None:
        documents, receipt = FREEZER.build_bundle(**bundle_inputs())
        self.assertEqual(set(documents), set(FREEZER.OUTPUT_NAMES))
        self.assertEqual(set(receipt["anchors"]), set(FREEZER.ANCHOR_ROLES))
        self.assertEqual(receipt["trading_calendar_sha256"],
                         documents["trading_calendar"]["body_sha256"])
        self.assertEqual(
            receipt["scheduled_decision_count"],
            len(receipt["formal_policies"]) *
            FREEZER.POLICY_MAXIMUM_ITERATIONS)
        self.assertGreaterEqual(
            len(receipt["eligible_scheduled_at_ms"]),
            FREEZER.MINIMUM_ELIGIBLE_DECISIONS)
        previous_teardown = 0
        for formal in receipt["formal_policies"]:
            self.assertEqual(
                formal["launcher_start_ms"],
                formal["valid_after_ms"] - FREEZER.LAUNCHER_WARMUP_MS)
            self.assertEqual(
                formal["launcher_dispatch_at_ms"],
                formal["launcher_start_ms"] -
                FREEZER.LAUNCHER_EARLY_START_LEAD_MS)
            if previous_teardown:
                self.assertEqual(
                    formal["valid_after_ms"],
                    FREEZER.required_valid_after_after_teardown(
                        previous_teardown, formal["slot_interval_ms"]))
            previous_teardown = formal["teardown_deadline_ms"]
        self.assertEqual(receipt["declared_trading_days"],
                         documents["trading_calendar"]["sessions"] and
                         [item["trading_day"] for item in
                          documents["trading_calendar"]["sessions"]])
        FREEZER.validate_bundle_receipt(receipt)

    def test_calendar_rule_excludes_maintenance_slot(self) -> None:
        documents, receipt = FREEZER.build_bundle(**bundle_inputs())
        session = documents["trading_calendar"]["sessions"][0]
        maintenance = session["maintenance_windows"][0]["opens_at_ms"]
        self.assertNotIn(maintenance, receipt["eligible_scheduled_at_ms"])
        self.assertIn(session["opens_at_ms"],
                      receipt["eligible_scheduled_at_ms"])

    def test_dst_is_derived_from_fixed_timezone(self) -> None:
        before_documents, _receipt = FREEZER.build_bundle(**bundle_inputs(
            start=date(2026, 3, 6), segments=26))
        after_documents, _receipt = FREEZER.build_bundle(**bundle_inputs(
            start=date(2026, 3, 9), segments=22))
        before = before_documents["trading_calendar"]["sessions"][0]
        after = after_documents["trading_calendar"]["sessions"][0]
        self.assertEqual(after["opens_at_ms"] - before["opens_at_ms"],
                         71 * 60 * 60 * 1000)

    def test_source_pin_drift_is_rejected(self) -> None:
        values = bundle_inputs()
        values["producer"] = dict(values["producer"])
        values["producer"]["file_sha256"] = digest("wrong-image")
        with self.assertRaisesRegex(
                FREEZER.FreezeError, "EXECUTING_IMAGE_SOURCE_DRIFT"):
            FREEZER.build_bundle(**values)

    def test_first_launcher_dispatch_must_still_be_in_the_future(self) -> None:
        values = bundle_inputs()
        first = values["formal_policies"][0].document
        values["now_ms"] = first["valid_after_ms"] - \
            FREEZER.LAUNCHER_WARMUP_MS - \
            FREEZER.LAUNCHER_EARLY_START_LEAD_MS
        with self.assertRaisesRegex(
                FREEZER.FreezeError, "FORMAL_POLICY_INVALID"):
            FREEZER.build_bundle(**values)

    def test_trading_day_boundaries_are_exact(self) -> None:
        with self.assertRaisesRegex(
                FREEZER.FreezeError, "TRADING_CALENDAR_INVALID"):
            FREEZER.build_bundle(**bundle_inputs(segments=20))
        for segments, expected_days in ((22, 10), (49, 20)):
            with self.subTest(expected_days=expected_days):
                documents, receipt = FREEZER.build_bundle(
                    **bundle_inputs(segments=segments))
                self.assertEqual(
                    len(receipt["declared_trading_days"]), expected_days)
                self.assertEqual(
                    len(documents["trading_calendar"]["sessions"]),
                    expected_days)
        with self.assertRaisesRegex(
                FREEZER.FreezeError, "TRADING_CALENDAR_INVALID"):
            FREEZER.build_bundle(**bundle_inputs(segments=54))

    def test_bundle_schedule_field_drift_is_rejected(self) -> None:
        _documents, receipt = FREEZER.build_bundle(**bundle_inputs())
        forged = copy.deepcopy(receipt)
        forged.pop("body_sha256")
        forged["formal_policies"][0]["launcher_dispatch_at_ms"] += 1
        with self.assertRaisesRegex(
                FREEZER.FreezeError, "BUNDLE_RECEIPT_INVALID"):
            FREEZER.validate_bundle_receipt(FREEZER.seal(forged))

    def test_less_than_two_hundred_eligible_slots_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            FREEZER.FreezeError,
                "FORMAL_POLICY_INVALID|ELIGIBLE_DECISIONS_BELOW_MINIMUM|"
                "SCHEDULE_INVALID"):
            FREEZER.build_bundle(**bundle_inputs(segments=1, iterations=20))

    def test_bundle_receipt_rejects_manual_anchor_promotion(self) -> None:
        _documents, receipt = FREEZER.build_bundle(**bundle_inputs())
        forged = copy.deepcopy(receipt)
        forged.pop("body_sha256")
        forged["producer"] = {
            "path": "/tmp/manual-freezer",
            "file_sha256": digest("manual"),
        }
        forged = FREEZER.seal(forged)
        with self.assertRaisesRegex(FREEZER.FreezeError,
                                    "BUNDLE_RECEIPT_INVALID"):
            FREEZER.validate_bundle_receipt(forged)

    def test_atomic_no_replace_publication(self) -> None:
        documents, receipt = FREEZER.build_bundle(**bundle_inputs())
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            parent = Path(temporary)
            parent.chmod(0o700)
            target = parent / "freeze"
            committed = FREEZER.publish_bundle(
                target=target, documents=documents, receipt=receipt,
                expected_uid=os.getuid(), expected_gid=os.getgid())
            self.assertEqual(committed["anchors"]["source_anchor"]["path"],
                             str(target / "source-anchor.json"))
            self.assertEqual(committed["trading_calendar"]["path"],
                             str(target / "reviewed-trading-calendar.json"))
            with self.assertRaisesRegex(FREEZER.FreezeError,
                                        "BUNDLE_ALREADY_EXISTS"):
                FREEZER.publish_bundle(
                    target=target, documents=documents, receipt=receipt,
                    expected_uid=os.getuid(), expected_gid=os.getgid())

    def test_cli_requires_explicit_run_before_reading_inputs(self) -> None:
        arguments = [
            "--source-baseline", "/missing/source.json",
            "--expected-source-baseline-file-sha256", digest("source"),
            "--strategy-config", "/missing/strategy.json",
            "--strategy-runtime-directory", "/missing/runtime",
            "--formal-policy", "/missing/policy.json",
            "--campaign-id", "campaign", "--domain-id", "alpha",
            "--trading-timezone", FREEZER.CALENDAR_TIMEZONE,
            "--independent-auditor-id", "auditor",
            "--output-bundle", "/missing/output",
        ]
        self.assertEqual(FREEZER.main(arguments), 2)


if __name__ == "__main__":
    unittest.main()
