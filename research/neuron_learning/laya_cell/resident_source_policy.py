"""Version-pinned, prospective-only original quote source selection.

No existing frozen candidate plan is upgraded. Opt-in plans must name this
reader and pin the source module SHA at plan registration time.
"""
from __future__ import annotations
from pathlib import Path
from .integrity import ContractError, digest, sha_file

RECENT = "ARCHIVE_VERIFIED_RECENT_ORIGINAL_V2"
RUNTIME_PIN = (
    "learn/laya_cell/resident_recent_source.py",
    "learn/laya_cell/resident_source_policy.py",
)


def declared_profile(config, plan):
    mode = plan.get("future_source_read_profile")
    if mode is None:
        return "ORIGINAL_FULL_HISTORY_V1"
    if mode != RECENT or config.get("future_source_read_profile") != RECENT:
        raise ContractError("FUTURE_RECENT_SOURCE_PROFILE_NOT_ENROLLED")
    if (plan.get("schema") != "heptatrader.laya-resident-future-plan.v1"
            or plan.get("plan_id") != digest({
                k: v for k, v in plan.items() if k != "plan_id"})):
        raise ContractError("FUTURE_RECENT_SOURCE_PLAN_NOT_SEALED")
    actual = plan.get("evaluation_runtime_sha256")
    expected = config.get("runtime_sha256")
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        raise ContractError("FUTURE_RECENT_SOURCE_CODE_NOT_PINNED")
    if any(actual.get(path) is None or actual.get(path) != expected.get(path)
           for path in RUNTIME_PIN):
        raise ContractError("FUTURE_RECENT_SOURCE_CODE_NOT_PINNED")
    # A matching pair of caller-supplied hashes is NOT an installed release
    # identity. Revalidate the exact on-disk modules before GPU model loading
    # or issuing a source read, including direct predictor invocations that
    # did not first traverse the resident supervisor's config_check().
    runtime=Path(config.get("runtime_directory") or "")
    if (not runtime.is_absolute() or runtime.is_symlink()
            or runtime.resolve()!=runtime or not runtime.is_dir()):
        raise ContractError("FUTURE_RECENT_SOURCE_RUNTIME_DIRECTORY_UNPINNED")
    for rel in RUNTIME_PIN:
        file=runtime/rel
        if (file.is_symlink() or not file.is_file()
                or sha_file(file)!=expected[rel]):
            raise ContractError("FUTURE_RECENT_SOURCE_RUNTIME_HASH_CHANGED")
    return RECENT


def read_for_future_plan(config, plan, now_ms):
    mode = declared_profile(config, plan)
    if mode == RECENT:
        from .resident_recent_source import read_recent_original_quotes
        return (*read_recent_original_quotes(config, now_ms), mode)
    from .resident_data import read_quotes
    return (*read_quotes(config, now_ms), mode)
