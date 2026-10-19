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


PENDING_OUTCOMES = 'ARCHIVE_VERIFIED_PENDING_OUTCOME_V1'


def validate_pending_prediction(plan, prediction, expected_slot=None):
    if not isinstance(prediction, dict):
        raise ContractError('PENDING_OUTCOME_PREDICTION_IDENTITY')
    slot = prediction.get('slot')
    slots = plan['rule']['slots']
    if (type(slots) is not int or slots <= 0 or type(slot) is not int
            or not 0 <= slot < slots
            or prediction.get('candidate_id') != plan['candidate_id']
            or prediction.get('plan_id') != plan['plan_id']
            or (expected_slot is not None and slot != expected_slot)):
        raise ContractError('PENDING_OUTCOME_PREDICTION_IDENTITY')
    anchor = prediction.get('anchor_ms')
    if type(anchor) is not int or anchor <= 0:
        raise ContractError('PENDING_OUTCOME_ANCHOR')
    return anchor


def pending_outcome_bounds(plan, predictions, now_ms):
    horizon = plan['rule']['horizon_ms']
    if (type(now_ms) is not int or now_ms <= 0
            or type(horizon) is not int or horizon <= 0):
        raise ContractError('PENDING_OUTCOME_TIME_BOUND')
    anchors = []
    seen_slots = set()
    for prediction in predictions:
        anchor = validate_pending_prediction(plan, prediction)
        slot = prediction['slot']
        if slot in seen_slots:
            raise ContractError('DUPLICATE_PENDING_OUTCOME_SLOT')
        seen_slots.add(slot)
        if anchor + horizon <= now_ms:
            anchors.append(anchor)
    if not anchors:
        return None
    return min(anchors), min(now_ms, max(anchors) + horizon + 5000)


def read_for_pending_outcomes(config, plan, now_ms, predictions):
    from .resident_data import read_quotes
    from .resident_recent_source import read_original_quote_range
    profile = plan.get('future_outcome_read_profile')
    if profile is None:
        return (*read_quotes(config, now_ms), 'FULL_ORIGINAL_HISTORY')
    if profile != PENDING_OUTCOMES:
        raise ContractError('UNKNOWN_PENDING_OUTCOME_SOURCE_PROFILE')
    if declared_profile(config, plan) != RECENT:
        raise ContractError('PENDING_OUTCOME_REQUIRES_DECLARED_RECENT_SOURCE')
    if config.get('completed_history_sources'):
        return (*read_quotes(config, now_ms), 'FULL_ORIGINAL_HISTORY_WITH_COMPLETED_SOURCES')
    bounds = pending_outcome_bounds(plan, predictions, now_ms)
    if bounds is None:
        return [], [], PENDING_OUTCOMES
    return (*read_original_quote_range(config, now_ms, *bounds), PENDING_OUTCOMES)
