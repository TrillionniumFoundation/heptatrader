"""Check pinned inventory consistency, not API equivalence or test execution.

Canonical evidence paths must exist, but behavioral acceptance is performed by
the research/native/installed-consumer CTest lanes, not by this validator.
"""
import hashlib
import json
import pathlib
import re
import sys

ALLOWED_DISPOSITIONS = {
    "canonicalized",
    "bounded_canonicalized",
    "retained_authority_runtime",
    "retained_compatibility_abi",
    "retained_reference_data",
    "superseded_infrastructure",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")

def load(path):
    with pathlib.Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)

def main():
    disposition = load(sys.argv[1])
    root = pathlib.Path(sys.argv[2]).resolve()
    migrations = load(sys.argv[3])

    assert disposition["schema_version"] == 1
    assert disposition["legacy_repository"] == "TrillionniumFoundation/HeptaDLL-main"
    assert disposition["source_baseline"] == migrations["legacy_source_inventory"]["source_baseline"]
    assert HEX40.fullmatch(disposition["source_baseline"])
    assert HEX40.fullmatch(disposition["source_tree"])

    scope = disposition["scope"]
    prefix = scope["prefix"]
    extensions = tuple(scope["extensions"])
    assets = disposition["assets"]
    assert scope["asset_count"] == len(assets) == 93
    assert scope["excludes"] and "Interface/" in scope["excludes"]

    paths = [item["path"] for item in assets]
    assert paths == sorted(paths), "source disposition assets must be sorted"
    assert len(paths) == len(set(paths)), "duplicate legacy source asset"
    for item in assets:
        assert item["path"].startswith(prefix)
        assert item["path"].endswith(extensions)
        assert HEX40.fullmatch(item["blob_sha1"])

    manifest = "".join(
        f"{item['path']}\t{item['blob_sha1']}\n" for item in assets
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    assert digest == scope["source_manifest_sha256"]

    groups = disposition["group_definitions"]
    used = {item["group"] for item in assets}
    assert used == set(groups), "every group must be used and every asset group defined"
    summary = {}
    for item in assets:
        assert item["group"] in groups
        summary[item["group"]] = summary.get(item["group"], 0) + 1
    assert summary == disposition["summary"]

    for group_id, group in groups.items():
        assert group["disposition"] in ALLOWED_DISPOSITIONS
        assert group["boundary"]
        assert group["evidence"]
        for evidence in group["evidence"]:
            candidate = (root / evidence).resolve()
            assert candidate.is_relative_to(root), f"evidence escapes repository: {evidence}"
            assert candidate.exists(), f"missing canonical evidence: {evidence}"
        if group["disposition"] in {"canonicalized", "bounded_canonicalized"}:
            assert group["canonical_targets"], f"{group_id} lacks canonical target"

    by_path = {item["path"]: groups[item["group"]]["disposition"] for item in assets}
    critical_runtime = {
        "heptaHeptaDLL/heptaBasicTradeSpi.cpp",
        "heptaHeptaDLL/heptaFtdTradeSpi.cpp",
        "heptaHeptaDLL/heptaBasicSimulator.cpp",
        "heptaHeptaDLL/heptaQdpMdSpi.cpp",
        "heptaHeptaDLL/heptaOrderReference.cpp",
    }
    for path in critical_runtime:
        assert by_path[path] == "retained_authority_runtime"

    for path in (
        "heptaHeptaDLL/heptaBasicStrategy.cpp",
        "heptaHeptaDLL/heptaAgentManager.cpp",
        "heptaHeptaDLL/heptaOrderBook.cpp",
        "heptaHeptaDLL/heptaTickTradeManager.cpp",
        "heptaHeptaDLL/heptaProductTradeTime.cpp",
        "heptaHeptaDLL/heptaNetValueEvaluation.cpp",
    ):
        assert by_path[path] == "bounded_canonicalized"

    for path in (
        "heptaHeptaDLL/heptaKindleStickSeries.cpp",
        "heptaHeptaDLL/heptaDataFileHelper.cpp",
    ):
        assert by_path[path] == "canonicalized"

    assert by_path["heptaHeptaDLL/TradingSession.xml"] == "retained_reference_data"
    assert by_path["heptaHeptaDLL/heptaTradeCommonDefine.h"] == "retained_compatibility_abi"

    print("heptadll_source_disposition: PASS")

if __name__ == "__main__":
    main()
