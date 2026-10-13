"""Stage a version-pinned Laya supervisor archive-aware runtime cutover.

No systemd mutation or service restart in this module. The original period,
work tree, account scope, historical receipts and state remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from historical_training.common import EvidenceError, read, sha

SOURCE_PIN = "c17f33846bde974bf1c89c7f040a8b206981172d"
MIGRATED_FILES = (
    "learn/laya_cell/dataset.py",
    "learn/laya_cell/resident_data.py",
)
EXTRA_PIN = "learn/laya_cell/source_archive.py"
MIN_OLD_PINS = 53
SCHEMA = "heptatrader.laya-archive-runtime-cutover.v1"


def fail_if_not(condition, code):
    if not condition:
        raise EvidenceError(code)


def ensure_private_existing(path, *, directory=False, readonly_source=False):
    path = Path(path)
    fail_if_not(path.is_absolute() and not path.is_symlink() and path.exists(),
                "ARCHIVE_RUNTIME_PATH_INVALID")
    stat = path.stat()
    # Installed read-only release directories are 0555: public readability
    # does not grant another user write access. Secret-bearing JSON configs
    # must remain 0600, whereas source paths must disallow foreign writes.
    forbidden = 0o022 if readonly_source else 0o077
    fail_if_not(stat.st_uid == os.getuid() and not stat.st_mode & forbidden,
                "ARCHIVE_RUNTIME_PRIVATE_OWNER_REQUIRED")
    fail_if_not(path.is_dir() if directory else path.is_file(),
                "ARCHIVE_RUNTIME_PATH_WRONG_TYPE")
    return path


def source_pin(repository):
    repository = Path(repository)
    fail_if_not(repository.is_absolute() and not repository.is_symlink()
                and repository.is_dir(), "ARCHIVE_SOURCE_REPO")
    result = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=8, check=False)
    fail_if_not(result.returncode == 0 and result.stdout.strip() == SOURCE_PIN,
                "ARCHIVE_SOURCE_COMMIT_DRIFT")
    for rel in ("research/neuron_learning/laya_cell/resident_data.py",
                "research/neuron_learning/laya_cell/dataset.py",
                "research/neuron_learning/laya_cell/source_archive.py"):
        result = subprocess.run(
            ["git", "-C", str(repository), "diff", "--quiet", "HEAD", "--", rel],
            capture_output=True, timeout=8, check=False)
        fail_if_not(result.returncode == 0, "ARCHIVE_SOURCE_DIRTY")
    return repository


def _checked_file(root, relative):
    item = Path(relative)
    fail_if_not(not item.is_absolute() and ".." not in item.parts
                and relative == item.as_posix(), "ARCHIVE_RUNTIME_RELATIVE_ESCAPE")
    path = root / item
    fail_if_not(path.is_file() and not path.is_symlink(),
                "ARCHIVE_RUNTIME_REQUIRED_FILE_MISSING")
    return path


def validate_old(guard_file, original_file):
    guard_file = ensure_private_existing(guard_file)
    original_file = ensure_private_existing(original_file)
    g = read(guard_file)
    c = read(original_file)
    fail_if_not(g.get("schema") == "heptatrader.resident-admission-wrapper-config.v1"
                and g.get("trade_authority") is False
                and g.get("original_config") == str(original_file)
                and g.get("original_config_sha256") == sha(original_file),
                "ARCHIVE_OLD_GUARD_BINDING")
    fail_if_not(c.get("schema") == "heptatrader.laya-resident-config.v1"
                and c.get("automatic_trading") is False
                and c.get("period_sha256") == g.get("period_sha256")
                and c.get("training_cadence_ms") == 900000
                and c.get("canonical_owner_endpoint") is None,
                "ARCHIVE_ORIGINAL_POLICY")
    old_root = ensure_private_existing(c["runtime_directory"], directory=True, readonly_source=True)
    pins = c.get("runtime_sha256")
    fail_if_not(type(pins) is dict and len(pins) >= MIN_OLD_PINS
                and all(type(x) is str and type(y) is str and len(y) == 64
                        for x, y in pins.items()),
                "ARCHIVE_ORIGINAL_RUNTIME_PINS")
    for rel, expected in pins.items():
        fail_if_not(sha(_checked_file(old_root, rel)) == expected,
                    "ARCHIVE_OLD_SOURCE_HASH_CHANGED")
    return g, c, old_root


def stage_cutover(guard_file, original_file, target_runtime, repository, output_root):
    old_guard, old_config, old_runtime = validate_old(guard_file, original_file)
    repo = source_pin(repository)
    new_runtime = ensure_private_existing(target_runtime, directory=True, readonly_source=True)
    target_pins = {}
    changed = []
    for rel, pinned in sorted(old_config["runtime_sha256"].items()):
        latest_hash = sha(_checked_file(new_runtime, rel))
        target_pins[rel] = latest_hash
        if latest_hash != pinned:
            changed.append(rel)
    fail_if_not(tuple(changed) == MIGRATED_FILES,
                "ARCHIVE_RELEASE_NOT_EXACT_TWO_FILE_MIGRATION")
    mapping = {
        "learn/laya_cell/dataset.py":
            "research/neuron_learning/laya_cell/dataset.py",
        "learn/laya_cell/resident_data.py":
            "research/neuron_learning/laya_cell/resident_data.py",
        EXTRA_PIN:
            "research/neuron_learning/laya_cell/source_archive.py",
    }
    for rel, upstream in mapping.items():
        fail_if_not(sha(_checked_file(new_runtime, rel)) ==
                    sha(_checked_file(repo, upstream)),
                    "ARCHIVE_SOURCE_RUNTIME_NOT_IDENTICAL")
    fail_if_not(EXTRA_PIN not in target_pins, "ARCHIVE_ALREADY_PINNED")
    target_pins[EXTRA_PIN] = sha(_checked_file(new_runtime, EXTRA_PIN))
    updated = dict(old_config)
    updated["runtime_directory"] = str(new_runtime)
    updated["runtime_sha256"] = target_pins
    fail_if_not(all(updated[k] == v for k, v in old_config.items()
                    if k not in ("runtime_directory", "runtime_sha256")),
                "ARCHIVE_UNINTENDED_TRAINING_POLICY_CHANGE")
    fail_if_not(updated["automatic_trading"] is False
                and updated.get("promotion_protocol") ==
                    old_config.get("promotion_protocol")
                and updated.get("decision_attribution_registrations") ==
                    old_config.get("decision_attribution_registrations"),
                "ARCHIVE_TRAINING_POLICY_CHANGED")
    target = Path(output_root)
    fail_if_not(target.is_absolute() and not target.exists() and not target.is_symlink(),
                "ARCHIVE_STAGING_MUST_BE_NEW")
    target.mkdir(parents=True, mode=0o700)
    os.chmod(target, 0o700)
    staged_config = target / "resident.json"
    _write_new(staged_config, updated)
    next_guard = dict(old_guard)
    next_guard["original_config"] = str(staged_config)
    next_guard["original_config_sha256"] = sha(staged_config)
    fail_if_not(all(next_guard[k] == v for k, v in old_guard.items()
                    if k not in ("original_config", "original_config_sha256")),
                "ARCHIVE_UNINTENDED_RESOURCE_GUARD_CHANGE")
    _write_new(target / "guard.json", next_guard)
    receipt = {
        "schema": SCHEMA,
        "source_commit": SOURCE_PIN,
        "old_guard_sha256": sha(guard_file),
        "old_config_sha256": sha(original_file),
        "original_runtime": str(old_runtime),
        "new_runtime": str(new_runtime),
        "exact_retained_pin_count": len(target_pins) - len(MIGRATED_FILES) - 1,
        "exact_changed_source_paths": list(changed),
        "added_source_archive_sha256": target_pins[EXTRA_PIN],
        "new_config_sha256": sha(staged_config),
        "new_guard_sha256": sha(target / "guard.json"),
        "no_original_source_deleted": True,
        "no_existing_config_replaced": True,
        "systemd_unit_modified": False,
        "services_restarted": False,
        "trade_authority": False,
        "automatic_promotion_enabled": False,
        "canonical_owner_enrolled": False,
        "broker_orders_sent": 0,
    }
    _write_new(target / "receipt.json", receipt)
    return receipt


def _write_new(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser()
    for name in ("guard", "original", "target-runtime", "source-repository", "output-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    receipt = stage_cutover(args.guard, args.original, args.target_runtime,
                            args.source_repository, args.output_root)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
