#!/usr/bin/env python3
"""Integrate sharded market data, deterministic features and research registries."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/gap-closure-m3b.yml"
SELF = Path(__file__)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new)


def write_json(path: Path, value: dict, compact: bool = False) -> None:
    path.write_text(
        (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
         if compact else json.dumps(value, ensure_ascii=False, indent=2)) + "\n",
        encoding="utf-8",
    )


def patch_cmake() -> None:
    path = ROOT / "HeptaTrade/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = (
        "add_library(hepta_numeric_core STATIC\n"
        "    numeric/fixed_decimal.cpp)\n"
        "hepta_runtime_target(hepta_numeric_core)\n"
    )
    addition = marker + (
        "\nadd_library(hepta_marketdata_core STATIC\n"
        "    marketdata/sharded_market_data.cpp)\n"
        "hepta_runtime_target(hepta_marketdata_core)\n"
        "target_link_libraries(hepta_marketdata_core PUBLIC\n"
        "    hepta_numeric_core\n"
        "    Threads::Threads\n"
        "    OpenSSL::Crypto)\n"
        "\nadd_library(hepta_feature_runtime STATIC\n"
        "    features/feature_generation.cpp)\n"
        "hepta_runtime_target(hepta_feature_runtime)\n"
        "target_link_libraries(hepta_feature_runtime PUBLIC\n"
        "    hepta_marketdata_core\n"
        "    hepta_numeric_core\n"
        "    Threads::Threads\n"
        "    OpenSSL::Crypto)\n"
    )
    path.write_text(
        replace_once(text, marker, addition, "data-plane targets"),
        encoding="utf-8",
    )

    path = ROOT / "tests/CMakeLists.txt"
    text = path.read_text(encoding="utf-8")
    marker = (
        "add_executable(hepta_fixed_decimal_tests\n"
        "    fixed_decimal_tests.cpp)\n"
        "target_link_libraries(hepta_fixed_decimal_tests\n"
        "    hepta_numeric_core)\n"
        "hepta_register_core_test(hepta_fixed_decimal_tests)\n"
    )
    addition = marker + (
        "\nadd_executable(hepta_sharded_market_data_tests\n"
        "    sharded_market_data_tests.cpp)\n"
        "target_link_libraries(hepta_sharded_market_data_tests\n"
        "    hepta_marketdata_core)\n"
        "hepta_register_core_test(hepta_sharded_market_data_tests)\n"
        "\nadd_executable(hepta_feature_generation_tests\n"
        "    feature_generation_tests.cpp)\n"
        "target_link_libraries(hepta_feature_generation_tests\n"
        "    hepta_feature_runtime)\n"
        "hepta_register_core_test(hepta_feature_generation_tests)\n"
    )
    path.write_text(
        replace_once(text, marker, addition, "data-plane tests"),
        encoding="utf-8",
    )


def patch_dev_loop() -> None:
    path = ROOT / "scripts/dev_core.sh"
    text = path.read_text(encoding="utf-8")
    marker = 'python3 "${ROOT_DIR}/scripts/check_schema_catalog.py"\n'
    path.write_text(
        replace_once(
            text,
            marker,
            marker + 'python3 "${ROOT_DIR}/scripts/check_research_registries.py"\n',
            "research registry gate",
        ),
        encoding="utf-8",
    )


def patch_ownership_and_manifests() -> None:
    path = ROOT / "docs/modules/source-ownership-registry-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    rules = value["physical_ownership_rules"]
    additions = [
        {
            "id": "marketdata-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/marketdata/"},
            "physical_owner": "hepta.marketdata.runtime",
            "priority": 200,
        },
        {
            "id": "feature-runtime",
            "selector": {"kind": "directory", "path": "HeptaTrade/features/"},
            "physical_owner": "hepta.feature.runtime",
            "priority": 200,
        },
    ]
    existing = {item["id"] for item in rules}
    for item in additions:
        if item["id"] in existing:
            raise SystemExit(f"ownership rule already exists: {item['id']}")
        rules.append(item)
    write_json(path, value, compact=True)

    for name in ("hepta-marketdata-runtime.json", "hepta-feature-runtime.json"):
        path = ROOT / "docs/modules/manifests" / name
        value = json.loads(path.read_text(encoding="utf-8"))
        value["lifecycle"] = "current"
        write_json(path, value)


def patch_document_registry() -> None:
    path = ROOT / "docs/document-registry-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    documents = value["documents"]
    additions = [
        {
            "path": "research/dataset-registry-v1.json",
            "class": "machine-registry",
            "owner": "@hepta/research-validation",
        },
        {
            "path": "research/feature-registry-v1.json",
            "class": "machine-registry",
            "owner": "@hepta/research-validation",
        },
    ]
    existing = {item["path"] for item in documents}
    for item in additions:
        if item["path"] not in existing:
            documents.append(item)
    documents.sort(key=lambda item: item["path"])
    write_json(path, value, compact=True)


def patch_test_matrix() -> None:
    path = ROOT / "docs/verification/test-matrix-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    evidence = {
        "marketdata-ordering": "sharded epoch/sequence/writer ordering CTest",
        "sequence-gap": "gap persistence, epoch reset and stale-reader CTest",
        "feature-determinism": "fixed-point feature digest replay CTest",
        "feature-generation": "input generation, duplicate and regression CTest",
        "point-in-time": "machine dataset registry digest and availability-time tests",
    }
    found = set()
    for check in value["checks"]:
        if check["id"] in evidence:
            check["state"] = "implemented"
            check["evidence"] = evidence[check["id"]]
            found.add(check["id"])
    if found != set(evidence):
        raise SystemExit(f"missing test-matrix checks: {sorted(set(evidence) - found)}")
    write_json(path, value, compact=True)


def patch_capability() -> None:
    path = ROOT / "docs/product/capability-registry-v2.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    matched = False
    for capability in value["capabilities"]:
        if capability["id"] != "hepta.data.feature-plane":
            continue
        matched = True
        capability["declared_state"] = "experimental"
        capability["design"] = "approved"
        capability["implementation"] = "implemented-core"
        capability["build"] = "default"
        capability["integration"] = {
            "simulator": "active-library",
            "paper": "absent",
            "live": "forbidden",
        }
        capability["release"] = "core"
    if not matched:
        raise SystemExit("feature-plane capability missing")
    write_json(path, value)


def patch_docs() -> None:
    path = ROOT / "docs/research/DATASET-REGISTRY.md"
    path.write_text(
        "# Dataset Registry\n\n"
        "Status: current normative\n"
        "Applies to: research and market-data/feature planes\n"
        "Verification: `python3 scripts/check_research_registries.py`\n"
        "Authority: `dataset-registry-v1.json`\n\n"
        "每个登记数据集绑定 repository-relative path、SHA-256、row count、"
        "point-in-time observed/available columns、producer epoch/sequence 与固定点数值策略。"
        "检查器拒绝 digest 漂移、未来可用时间、epoch 回退、sequence gap、"
        "crossed quote、非规范整数和越界 raw value。\n\n"
        "数据字节改变必须产生新 identity/digest；registry 不存储 credential，"
        "也不授予策略 activation。\n",
        encoding="utf-8",
    )
    path = ROOT / "docs/research/FEATURE-REGISTRY.md"
    path.write_text(
        "# Feature Registry\n\n"
        "Status: current normative\n"
        "Applies to: deterministic research and feature runtime\n"
        "Verification: `python3 scripts/check_research_registries.py` and feature CTest\n"
        "Authority: `feature-registry-v1.json`\n\n"
        "当前 `mid-spread-v1` 使用 `hepta.numeric.fixed-v1`，输出绑定 market input "
        "epoch、sequence、generation、watermark 与 digest。缺失、过期、sequence gap、"
        "输入回退或 odd-microunit midpoint 全部 fail closed；同输入重放返回相同 digest。\n\n"
        "Feature registry 声明实现、输入数据集、输出定义和安全要求；"
        "它不授予策略或交易 mutation capability。\n",
        encoding="utf-8",
    )


def main() -> None:
    patch_cmake()
    patch_dev_loop()
    patch_ownership_and_manifests()
    patch_document_registry()
    patch_test_matrix()
    patch_capability()
    patch_docs()
    subprocess.run(
        ["python3", "scripts/generate_documentation_views.py", "--write"],
        cwd=ROOT,
        check=True,
    )
    WORKFLOW.unlink()
    SELF.unlink()


if __name__ == "__main__":
    main()
