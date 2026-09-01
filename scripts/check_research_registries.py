#!/usr/bin/env python3
"""Fail-closed validation for point-in-time dataset and feature registries."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "docs/research/dataset-registry-v1.json"
FEATURES = ROOT / "docs/research/feature-registry-v1.json"
MAX_RAW = 9_000_000_000_000_000


class RegistryError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RegistryError(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise RegistryError(f"{path}: root must be an object")
    return value


def safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RegistryError("unsafe registry path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RegistryError("unsafe registry path")
    return value


def canonical_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise RegistryError("invalid sha256")
    if any(character not in "0123456789abcdef" for character in value):
        raise RegistryError("invalid sha256")
    return value


def exact_int(value: str, name: str) -> int:
    if not value or (value[0] == "-" and len(value) == 1):
        raise RegistryError(f"invalid integer column {name}")
    digits = value[1:] if value[0] == "-" else value
    if not digits.isdigit() or (len(digits) > 1 and digits[0] == "0"):
        raise RegistryError(f"invalid integer column {name}")
    parsed = int(value)
    if abs(parsed) > MAX_RAW:
        raise RegistryError(f"fixed numeric range exceeded in {name}")
    return parsed


def validate_dataset_registry(
    registry: dict[str, Any], root: Path = ROOT
) -> dict[str, dict[str, Any]]:
    if registry.get("schema") != "heptatrader.dataset-registry.v1":
        raise RegistryError("dataset registry schema mismatch")
    datasets = registry.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise RegistryError("dataset registry must be non-empty")
    indexed: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise RegistryError("dataset entry must be an object")
        expected = {
            "id", "path", "format", "sha256", "point_in_time",
            "observed_at_column", "available_at_column", "epoch_column",
            "sequence_column", "numeric_policy", "row_count",
        }
        if set(dataset) != expected:
            raise RegistryError("dataset fields mismatch")
        dataset_id = dataset.get("id")
        if not isinstance(dataset_id, str) or not dataset_id or dataset_id in indexed:
            raise RegistryError("dataset id invalid or duplicated")
        if dataset.get("format") != "csv" or dataset.get("point_in_time") is not True:
            raise RegistryError("dataset must be point-in-time CSV")
        if dataset.get("numeric_policy") != "hepta.numeric.fixed-v1":
            raise RegistryError("dataset numeric policy mismatch")
        if type(dataset.get("row_count")) is not int or dataset["row_count"] <= 0:
            raise RegistryError("dataset row_count invalid")
        path = root / safe_path(dataset.get("path"))
        if not path.is_file() or path.is_symlink():
            raise RegistryError(f"dataset file missing or unsafe: {path}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != canonical_sha256(dataset.get("sha256")):
            raise RegistryError(f"dataset digest mismatch: {dataset_id}")
        try:
            rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
        except (UnicodeError, csv.Error) as error:
            raise RegistryError(f"dataset parse failed: {dataset_id}: {error}") from error
        if len(rows) != dataset["row_count"]:
            raise RegistryError(f"dataset row count mismatch: {dataset_id}")
        required = {
            "venue", "instrument", dataset["epoch_column"],
            dataset["sequence_column"], dataset["observed_at_column"],
            dataset["available_at_column"], "bid_raw", "ask_raw",
        }
        if not rows or not required.issubset(rows[0]):
            raise RegistryError(f"dataset columns incomplete: {dataset_id}")
        last: dict[tuple[str, str], tuple[int, int]] = {}
        for row in rows:
            venue = row["venue"]
            instrument = row["instrument"]
            if not venue or not instrument:
                raise RegistryError("dataset identity empty")
            epoch = exact_int(row[dataset["epoch_column"]], "epoch")
            sequence = exact_int(row[dataset["sequence_column"]], "sequence")
            observed = exact_int(row[dataset["observed_at_column"]], "observed")
            available = exact_int(row[dataset["available_at_column"]], "available")
            bid = exact_int(row["bid_raw"], "bid_raw")
            ask = exact_int(row["ask_raw"], "ask_raw")
            if epoch <= 0 or sequence <= 0 or observed <= 0 or available < observed:
                raise RegistryError("dataset point-in-time envelope invalid")
            if ask < bid:
                raise RegistryError("dataset crossed quote invalid")
            key = (venue, instrument)
            previous = last.get(key)
            if previous is None:
                if sequence != 1:
                    raise RegistryError("dataset first sequence must be one")
            elif epoch == previous[0]:
                if sequence != previous[1] + 1:
                    raise RegistryError("dataset sequence gap")
            elif epoch > previous[0]:
                if sequence != 1:
                    raise RegistryError("dataset new epoch must restart at one")
            else:
                raise RegistryError("dataset epoch regression")
            last[key] = (epoch, sequence)
        indexed[dataset_id] = dataset
    return indexed


def validate_feature_registry(
    registry: dict[str, Any],
    datasets: dict[str, dict[str, Any]],
    root: Path = ROOT,
) -> None:
    if registry.get("schema") != "heptatrader.feature-registry.v1":
        raise RegistryError("feature registry schema mismatch")
    features = registry.get("features")
    if not isinstance(features, list) or not features:
        raise RegistryError("feature registry must be non-empty")
    seen: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            raise RegistryError("feature entry must be an object")
        expected = {
            "id", "version", "implementation", "input_contract",
            "output_contract", "dataset", "numeric_policy", "deterministic",
            "requires_complete_sequence", "requires_fresh_input", "outputs",
        }
        if set(feature) != expected:
            raise RegistryError("feature fields mismatch")
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not feature_id or feature_id in seen:
            raise RegistryError("feature id invalid or duplicated")
        seen.add(feature_id)
        if type(feature.get("version")) is not int or feature["version"] <= 0:
            raise RegistryError("feature version invalid")
        if feature.get("dataset") not in datasets:
            raise RegistryError("feature dataset unknown")
        if feature.get("numeric_policy") != "hepta.numeric.fixed-v1":
            raise RegistryError("feature numeric policy mismatch")
        if feature.get("deterministic") is not True or \
                feature.get("requires_complete_sequence") is not True or \
                feature.get("requires_fresh_input") is not True:
            raise RegistryError("feature safety flags must be true")
        implementation = root / safe_path(feature.get("implementation"))
        if not implementation.is_file() or implementation.is_symlink():
            raise RegistryError("feature implementation missing or unsafe")
        source = implementation.read_text(encoding="utf-8")
        if feature_id not in source:
            raise RegistryError("feature id absent from implementation")
        outputs = feature.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise RegistryError("feature outputs missing")
        names: set[str] = set()
        for output in outputs:
            if not isinstance(output, dict) or set(output) != {"name", "definition"}:
                raise RegistryError("feature output invalid")
            if not isinstance(output["name"], str) or not output["name"] or \
                    output["name"] in names:
                raise RegistryError("feature output name invalid or duplicated")
            if not isinstance(output["definition"], str) or not output["definition"]:
                raise RegistryError("feature output definition missing")
            names.add(output["name"])


def run(root: Path = ROOT) -> None:
    datasets = validate_dataset_registry(
        load_object(root / "docs/research/dataset-registry-v1.json"), root
    )
    validate_feature_registry(
        load_object(root / "docs/research/feature-registry-v1.json"),
        datasets,
        root,
    )


def main() -> int:
    try:
        run()
        print("[RESEARCH-REGISTRIES] PASS")
        return 0
    except RegistryError as error:
        print(f"[RESEARCH-REGISTRIES] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
