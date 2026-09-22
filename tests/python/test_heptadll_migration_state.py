import json
import pathlib
import sys

REQUIRED = {
    "owner_acknowledgement", "original_artifact_sha256", "platform", "toolchain",
    "api_abi", "data_formats", "persisted_formats", "golden_evidence", "rollback_version",
}

def load(path):
    with pathlib.Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)

def main():
    migrations = load(sys.argv[1])
    lifecycle = load(sys.argv[2])
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
    assert "deployed strategy owner" in consumers["C07"]["source_inventory_result"]
    assert "cannot be enumerated" in consumers["C08"]["source_inventory_result"]

    for consumer_id in ("C07", "C08"):
        item = consumers[consumer_id]
        assert item["status"] == "retained_unconfirmed"
        assert item["blocks_legacy_retirement"] is True

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
    assert legacy["state"] in {"retained", "archived"}
    gates = lifecycle["retirement_gates"]
    all_gates = all(gates.values())
    assert legacy["archive_ready"] == all_gates
    if legacy["state"] == "archived":
        assert legacy["archive_ready"]
        assert not any(item["blocks_legacy_retirement"] for item in consumers.values())
    print("heptadll_migration_state: PASS")

if __name__ == "__main__":
    main()
