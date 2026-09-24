import json
import pathlib
import sys
import unittest

REQUIRED = {
    "owner_acknowledgement", "original_artifact_sha256", "platform", "toolchain",
    "api_abi", "data_formats", "persisted_formats", "golden_evidence", "rollback_version",
}

def load(path):
    with pathlib.Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)

def validate(migrations, lifecycle):
    assert migrations["schema_version"] == 2
    assert lifecycle["schema_version"] == 2
    consumers = {item["id"]: item for item in migrations["consumers"]}
    assert set(consumers) == {"C%02d" % n for n in range(1, 11)}
    for item in consumers.values():
        if item["status"] in {"migrated", "retired"}:
            missing = [key for key in REQUIRED if not item.get(key)]
            assert not missing, f"{item['id']} unsupported completion claim: {missing}"
            digest = item["original_artifact_sha256"]
            assert len(digest) == 71 and digest.startswith("sha256:")
    inventory = migrations["legacy_source_inventory"]
    assert inventory["scope"].startswith("default-branch")
    assert len(inventory["compatibility_head"]) == 40
    assert len(inventory["source_baseline"]) == 40
    c07_sources = consumers["C07"]["source_inventory"]
    assert c07_sources and all(path.startswith("heptaHeptaDLL/") for path in c07_sources)

    final_support = {
        "C05": "support_ended_read_only_reconciliation",
        "C06": "support_ended_source_preserved",
        "C07": "support_ended_source_preserved",
        "C08": "support_ended_unverified_external",
        "C10": "private_source_preserved",
    }
    for consumer_id, status in final_support.items():
        item = consumers[consumer_id]
        assert item["status"] == status
        assert item["blocks_legacy_retirement"] is False
        assert item["migration_claim"] is False
        assert item["deployment_absence_claim"] is False
        assert item["archive_source_preserved"] is True

    policy = migrations["retirement_policy"]
    assert policy["canonical_abi_compatibility_promised"] is False
    assert policy["direct_spi_runtime_allowed_in_canonical"] is False
    assert policy["unknown_deployment_migration_claim"] is False
    assert policy["archive_preserves_source_access"] is True

    c05 = consumers["C05"]
    reconciliation = c05["canonical_read_only_reconciliation"]
    assert reconciliation["api"] == "NativeStrategyClient::InspectLegacyHro1"
    assert reconciliation["mutation_capability"] is False
    assert reconciliation["record_conversion"] is False
    assert reconciliation["missing_recovery_binding_invented"] is False
    assert set(reconciliation["evidence"]) == {
        "tests/research/native_client_tests.cpp",
        "tests/research/native_execution_tests.cpp",
    }

    canonical = lifecycle["canonical_development"]
    legacy = lifecycle["legacy_repository"]
    assert len(canonical["accepted_baseline_commit"]) == 40
    assert len(canonical["accepted_tree"]) == 40
    evidence = canonical["acceptance_evidence"]
    assert evidence["identical_tree_pr117"]["tree"] == canonical["accepted_tree"]
    for run_id in evidence["main"].values():
        assert isinstance(run_id, int) and run_id > 0
    assert evidence["identical_tree_pr117"]["modular_integration_run"] > 0

    assert canonical["state"] == "integrated"
    assert legacy["state"] == "archived"
    assert legacy["abi_support_state"] == "retired"
    assert legacy["canonical_runtime_support"] == "forbidden"
    assert legacy["archive_target_visibility"] == "private"
    assert legacy["source_preserved_after_archive"] is True
    assert legacy["archive_action_pending"] is False
    assert isinstance(legacy["archive_notice_pr"], int) and legacy["archive_notice_pr"] > 0
    assert len(legacy["archive_notice_merge_commit"]) == 40
    int(legacy["archive_notice_merge_commit"], 16)
    gates = lifecycle["retirement_gates"]
    all_gates = all(gates.values())
    assert legacy["archive_ready"] == all_gates
    if legacy["archive_ready"]:
        assert not any(item["blocks_legacy_retirement"] for item in consumers.values())
    if legacy["state"] == "archived":
        assert legacy["archive_ready"]
        assert not any(item["blocks_legacy_retirement"] for item in consumers.values())

class HistoricalMigrationTests(unittest.TestCase):
    def records(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        return (load(root / "docs/technical/heptadll-consumer-migrations.json"),
                load(root / "docs/technical/heptadll-lifecycle-status.json"))

    def test_pinned_historical_record_is_consistent(self):
        validate(*self.records())

    def test_narrative_rewording_is_not_a_runtime_contract(self):
        migrations, lifecycle = self.records()
        for consumer in migrations["consumers"]:
            if "source_inventory_result" in consumer:
                consumer["source_inventory_result"] = "Equivalent historical explanation."
        validate(migrations, lifecycle)

    def test_authority_and_unverified_migration_claims_still_reject(self):
        for key in ("canonical_abi_compatibility_promised",
                    "direct_spi_runtime_allowed_in_canonical",
                    "unknown_deployment_migration_claim"):
            migrations, lifecycle = self.records()
            migrations["retirement_policy"][key] = True
            with self.subTest(key=key), self.assertRaises(AssertionError):
                validate(migrations, lifecycle)

if __name__ == "__main__":
    if len(sys.argv) == 3:
        validate(load(sys.argv[1]), load(sys.argv[2]))
        print("heptadll_migration_state: PASS (historical metadata only)")
    else:
        unittest.main()
