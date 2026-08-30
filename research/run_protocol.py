#!/usr/bin/env python3
"""Deterministic, capability-free research run protocol.

The module implements the canonical RunManifest -> EventLog -> RunSummary path.
It deliberately has no broker, session, permit, promotion or deployment APIs.
"""

from __future__ import annotations

import argparse
import ast
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal, getcontext
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from typing import Any, Mapping, Sequence

try:  # package import (``python -m research.run_protocol``)
    from .protocol_support import (
        ResearchProtocolError,
        _CEREMONY_FIELD_NORMALIZED,
        _DECIMAL_TEXT_RE,
        _EVENT_KIND_RE,
        _INSTRUMENT_RE,
        _MAX_DECIMAL_ADJUSTED,
        _MAX_JSON_DEPTH,
        _MAX_PROTOCOL_INTEGER,
        _QUOTE_FIELDS,
        _TARGET_FIELDS,
        _freeze_json,
        _json_copy,
        _reject_capability_fields,
        _reject_ceremony_fields,
        _sequence,
        _instrument,
        _thaw_json,
        canonical_json,
        canonical_sha256,
        decimal_text,
        decimal_value,
        integer_value,
        sha256_file as _sha256_file,
        sha256_json,
    )
except ImportError:  # direct script execution from an installed research dir
    from protocol_support import (  # type: ignore[no-redef]
        ResearchProtocolError,
        _CEREMONY_FIELD_NORMALIZED,
        _DECIMAL_TEXT_RE,
        _EVENT_KIND_RE,
        _INSTRUMENT_RE,
        _MAX_DECIMAL_ADJUSTED,
        _MAX_JSON_DEPTH,
        _MAX_PROTOCOL_INTEGER,
        _QUOTE_FIELDS,
        _TARGET_FIELDS,
        _freeze_json,
        _json_copy,
        _reject_capability_fields,
        _reject_ceremony_fields,
        _sequence,
        _instrument,
        _thaw_json,
        canonical_json,
        canonical_sha256,
        decimal_text,
        decimal_value,
        integer_value,
        sha256_file as _sha256_file,
        sha256_json,
    )

getcontext().prec = 50


_STATIC_CAPABILITY_FIELDS = frozenset(
    {"paper_mutation", "live_mutation", "broker_access", "session_management"}
)
_STATIC_SOURCE_PREFIXES = ("research/", "strategies/")
_STATIC_SUPPORT_PREFIXES = ("research/",)
_STATIC_FORBIDDEN_IMPORTS = frozenset(
    {
        "hepta_strategy_shadow_runner",
        "hepta_shadow_market_history",
        "validate_hepta_strategy_decision_receipt",
        "hepta_market_context_builder",
        "hepta_eurusd_confirmed_momentum_strategy",
    }
)
_MAX_PROTOCOL_INTEGER = (1 << 63) - 1
_MAX_DECIMAL_ADJUSTED = 1000
_MAX_JSON_BYTES = 16 * 1024 * 1024
_MAX_JSON_DEPTH = 128


def _final_oos_bounds(value: Any) -> tuple[int, int]:
    """Parse the concrete untouched-OOS interval from a run manifest."""

    if not isinstance(value, Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID")

    def bound(primary: str, alias: str) -> int:
        has_primary = primary in value
        has_alias = alias in value
        if not has_primary and not has_alias:
            raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID", primary)
        primary_value = (
            integer_value(value[primary], f"final_out_of_sample.{primary}", minimum=0)
            if has_primary
            else None
        )
        alias_value = (
            integer_value(value[alias], f"final_out_of_sample.{alias}", minimum=0)
            if has_alias
            else None
        )
        if primary_value is not None and alias_value is not None and primary_value != alias_value:
            raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID", primary)
        if primary_value is not None:
            return primary_value
        assert alias_value is not None
        return alias_value

    start = bound("start_ms", "start_ts_ms")
    end = bound("end_ms", "end_ts_ms")
    if start >= end:
        raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID")
    return start, end


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    kind: str
    authoritative_ts_ms: int
    payload: Mapping[str, Any]
    previous_digest: str
    digest: str

    def __post_init__(self) -> None:
        # ``frozen=True`` protects only the dataclass attributes.  Freeze the
        # nested payload as well so the digest cannot be invalidated by a
        # caller mutating a dict/list obtained from ``events``.
        object.__setattr__(self, "payload", _freeze_json(self.payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "authoritative_ts_ms": self.authoritative_ts_ms,
            "payload": _thaw_json(self.payload),
            "previous_digest": self.previous_digest,
            "digest": self.digest,
        }


class EventLog:
    def __init__(self) -> None:
        self._events: list[EventRecord] = []
        self._last_ts = -1
        # Instrument is part of an event's identity.  A multi-instrument feed
        # legitimately has one quote per instrument at the same timestamp;
        # treating ``(kind, timestamp)`` as globally unique would incorrectly
        # classify the second quote as a changed duplicate.
        self._at_timestamp: dict[tuple[str, int, str], str] = {}

    @property
    def events(self) -> tuple[EventRecord, ...]:
        return tuple(self._events)

    @property
    def digest(self) -> str:
        return self._events[-1].digest if self._events else "sha256:" + "0" * 64

    def append(
        self,
        kind: str,
        authoritative_ts_ms: int,
        payload: Mapping[str, Any],
    ) -> EventRecord:
        if (
            not isinstance(kind, str)
            or not _EVENT_KIND_RE.fullmatch(kind)
            or isinstance(authoritative_ts_ms, bool)
            or not isinstance(authoritative_ts_ms, int)
            or authoritative_ts_ms < 0
            or authoritative_ts_ms > _MAX_PROTOCOL_INTEGER
            or not isinstance(payload, Mapping)
        ):
            raise ResearchProtocolError("RESEARCH_EVENT_INVALID")
        if authoritative_ts_ms < self._last_ts:
            raise ResearchProtocolError("RESEARCH_EVENT_OUT_OF_ORDER")
        _reject_ceremony_fields(payload, "event.payload")
        _reject_capability_fields(payload, "event.payload")
        try:
            normalized_payload = json.loads(canonical_json(dict(payload)))
        except (TypeError, ValueError, OverflowError) as error:
            raise ResearchProtocolError("RESEARCH_EVENT_INVALID", "payload") from error
        if not isinstance(normalized_payload, dict):
            raise ResearchProtocolError("RESEARCH_EVENT_INVALID", "payload")
        signature = sha256_json({"kind": kind, "payload": normalized_payload})
        identity = normalized_payload.get("instrument", "")
        if not isinstance(identity, str):
            raise ResearchProtocolError("RESEARCH_EVENT_INVALID", "instrument")
        if identity:
            _instrument(identity, "event.payload.instrument")
        key = (kind, authoritative_ts_ms, identity)
        previous = self._at_timestamp.get(key)
        if previous is not None:
            if previous != signature:
                raise ResearchProtocolError("RESEARCH_CHANGED_DUPLICATE_TIMESTAMP")
            for event in reversed(self._events):
                if (
                    event.kind == kind
                    and event.authoritative_ts_ms == authoritative_ts_ms
                    and event.payload.get("instrument", "") == identity
                ):
                    return event
            raise AssertionError("event signature index lost its record")
        previous_digest = self.digest
        sequence = len(self._events) + 1
        body = {
            "sequence": sequence,
            "kind": kind,
            "authoritative_ts_ms": authoritative_ts_ms,
            "payload": normalized_payload,
            "previous_digest": previous_digest,
        }
        event = EventRecord(
            sequence=sequence,
            kind=kind,
            authoritative_ts_ms=authoritative_ts_ms,
            payload=normalized_payload,
            previous_digest=previous_digest,
            digest=sha256_json(body),
        )
        self._events.append(event)
        self._at_timestamp[key] = signature
        self._last_ts = authoritative_ts_ms
        return event


def _resolve_static_source(
    root: Path,
    relative: Any,
    field: str,
    allowed_prefixes: Sequence[str] = _STATIC_SOURCE_PREFIXES,
) -> Path:
    """Resolve one manifest source asset under an explicit checkout root.

    Paths are checked lexically for both POSIX and Windows traversal forms
    before filesystem resolution.  This keeps verification fail-closed and
    portable when a manifest is copied between host platforms.
    """

    normalized_relative = relative.replace("\\", "/") if isinstance(relative, str) else ""
    relative_parts = re.split(r"[\\/]", relative) if isinstance(relative, str) else ()
    windows_path = PureWindowsPath(relative) if isinstance(relative, str) else None
    unsafe_relative = (
        not isinstance(relative, str)
        or not relative
        or "\x00" in relative
        or Path(relative).is_absolute()
        or (windows_path is not None and windows_path.is_absolute())
        or (windows_path is not None and bool(windows_path.drive))
        or any(part == ".." for part in relative_parts)
        or not any(normalized_relative.startswith(prefix) for prefix in allowed_prefixes)
    )
    try:
        resolved = (root / relative).resolve() if not unsafe_relative else root
    except (OSError, RuntimeError, ValueError) as error:
        raise ResearchProtocolError("RESEARCH_STRATEGY_INPUT_MISSING", field) from error
    try:
        inside_root = resolved.is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        inside_root = False
    if unsafe_relative or not inside_root or not resolved.is_file():
        detail = field
        if not (root / "strategies").is_dir() or not (root / "scripts").is_dir():
            detail = (
                f"{field}; source assets are not installed, pass "
                "--root <source-checkout>"
            )
        raise ResearchProtocolError("RESEARCH_STRATEGY_INPUT_MISSING", detail)
    return resolved


def _validate_static_source_semantics(path: Path, field: str) -> None:
    """Reject legacy ceremony imports/fields in manifest-bound source files."""

    if path.suffix == ".py":
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError, RecursionError) as error:
            raise ResearchProtocolError(
                "RESEARCH_STRATEGY_INPUT_INVALID", field
            ) from error
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        forbidden = sorted(imports & _STATIC_FORBIDDEN_IMPORTS)
        if forbidden:
            raise ResearchProtocolError(
                "RESEARCH_CEREMONY_FORBIDDEN",
                f"{field}: {', '.join(forbidden)}",
            )
        return
    if path.suffix != ".json":
        return
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ResearchProtocolError("RESEARCH_STRATEGY_INPUT_INVALID", field) from error
    if not isinstance(descriptor, Mapping):
        raise ResearchProtocolError("RESEARCH_STRATEGY_INPUT_INVALID", field)
    # Static descriptors are data, but they still must not smuggle a runtime
    # capability or campaign/lease namespace into the current path.
    _reject_capability_fields(descriptor, field)
    _reject_ceremony_fields(descriptor, field)


def validate_static_manifest(document: Mapping[str, Any], root: Path | str) -> None:
    if not isinstance(document, Mapping):
        raise ResearchProtocolError("RESEARCH_MANIFEST_INVALID")
    # Detect cycles/capability-bearing branches before canonicalization so the
    # caller receives a protocol reason code rather than a raw JSON recursion
    # error.  The capability declaration itself is checked separately below.
    manifest_without_capability = {
        key: value for key, value in document.items() if key != "capability"
    }
    _reject_capability_fields(manifest_without_capability, "manifest")
    # The static capability declaration is intentionally allowed below, but
    # legacy campaign/lease/finalizer ceremony is never part of the current
    # research contract.  Reject those keys here as well as in runnable
    # manifests so an installed/static verifier cannot be pointed at a hybrid
    # descriptor that smuggles authority metadata through an otherwise valid
    # schema.
    _reject_ceremony_fields(document, "manifest")
    try:
        canonical_json(document)
    except (TypeError, ValueError, OverflowError) as error:
        raise ResearchProtocolError("RESEARCH_MANIFEST_INVALID") from error
    if document.get("schema") != "heptatrader.research-manifest.v1":
        raise ResearchProtocolError("RESEARCH_MANIFEST_SCHEMA_INVALID")
    if document.get("mode") != "shadow":
        raise ResearchProtocolError("RESEARCH_MODE_NOT_SHADOW")
    contract = document.get("run_contract")
    if not isinstance(contract, Mapping) or contract.get("campaign_or_finalizer_required") is not False:
        raise ResearchProtocolError("RESEARCH_RUN_CONTRACT_INVALID")
    capability = document.get("capability")
    if (
        not isinstance(capability, Mapping)
        or set(capability) != set(_STATIC_CAPABILITY_FIELDS)
        or any(value is not False for value in capability.values())
    ):
        raise ResearchProtocolError("RESEARCH_CAPABILITY_FORBIDDEN")
    # The static manifest's capability declaration is the one intentional
    # occurrence of capability-shaped keys.  Inspect every other field so a
    # nested token/permit cannot be hidden in an otherwise valid declaration.
    try:
        root = Path(root).expanduser().resolve()
    except (TypeError, ValueError, OSError, RuntimeError) as error:
        raise ResearchProtocolError("RESEARCH_SOURCE_ROOT_INVALID") from error
    if not root.is_dir():
        raise ResearchProtocolError("RESEARCH_SOURCE_ROOT_INVALID", str(root))
    strategy = document.get("strategy")
    if not isinstance(strategy, Mapping):
        raise ResearchProtocolError("RESEARCH_STRATEGY_MISSING")
    strategy_digests = document.get("strategy_digests")
    if (
        not isinstance(strategy_digests, Mapping)
        or set(strategy_digests) != {
            "definition",
            "implementation",
            "context_builder",
            "replay_evaluator",
        }
        or any(not canonical_sha256(value) for value in strategy_digests.values())
    ):
        raise ResearchProtocolError("RESEARCH_STRATEGY_DIGEST_INVALID")
    for field in ("definition", "implementation", "context_builder", "replay_evaluator"):
        relative = strategy.get(field)
        resolved = _resolve_static_source(root, relative, field)
        try:
            actual_digest = _sha256_file(resolved)
        except ResearchProtocolError:
            raise ResearchProtocolError(
                "RESEARCH_STRATEGY_INPUT_MISSING", field
            ) from None
        if actual_digest != strategy_digests[field]:
            raise ResearchProtocolError(
                "RESEARCH_STRATEGY_DIGEST_MISMATCH", field
            )
        _validate_static_source_semantics(resolved, field)
    support = document.get("runner_support")
    if (
        not isinstance(support, Mapping)
        or set(support) != {"path", "sha256"}
        or not canonical_sha256(support.get("sha256"))
    ):
        raise ResearchProtocolError("RESEARCH_STRATEGY_SUPPORT_INVALID")
    support_path = _resolve_static_source(
        root,
        support.get("path"),
        "runner_support",
        _STATIC_SUPPORT_PREFIXES,
    )
    try:
        support_digest = _sha256_file(support_path)
    except ResearchProtocolError:
        raise ResearchProtocolError(
            "RESEARCH_STRATEGY_INPUT_MISSING", "runner_support"
        ) from None
    if support_digest != support["sha256"]:
        raise ResearchProtocolError(
            "RESEARCH_STRATEGY_DIGEST_MISMATCH", "runner_support"
        )
    _validate_static_source_semantics(support_path, "runner_support")


def validate_run_manifest(document: Mapping[str, Any]) -> None:
    if not isinstance(document, Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_INVALID")
    _reject_ceremony_fields(document)
    _reject_capability_fields(document)
    try:
        canonical_json(document)
    except (TypeError, ValueError, OverflowError) as error:
        raise ResearchProtocolError("RUN_MANIFEST_INVALID") from error
    required_strings = (
        "run_id", "source_revision", "strategy_digest", "config_digest",
        "calendar", "session_semantics", "symbol_mapping",
    )
    if document.get("schema") != "heptatrader.run-manifest.v1":
        raise ResearchProtocolError("RUN_MANIFEST_SCHEMA_INVALID")
    if document.get("mode") != "shadow":
        raise ResearchProtocolError("RUN_MANIFEST_MODE_INVALID")
    for field in required_strings:
        value = document.get(field)
        if not isinstance(value, str) or not value:
            raise ResearchProtocolError("RUN_MANIFEST_FIELD_MISSING", field)
    for field in ("strategy_digest", "config_digest"):
        if not canonical_sha256(document[field]):
            raise ResearchProtocolError("RUN_MANIFEST_DIGEST_INVALID", field)
    strategy = document.get("strategy")
    if not isinstance(strategy, Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_STRATEGY_INVALID")
    for field in ("id", "version"):
        if not isinstance(strategy.get(field), str) or not strategy[field]:
            raise ResearchProtocolError("RUN_MANIFEST_STRATEGY_INVALID", field)
    for field in ("definition_digest", "implementation_digest"):
        if not canonical_sha256(strategy.get(field)):
            raise ResearchProtocolError("RUN_MANIFEST_STRATEGY_INVALID", field)
    datasets = document.get("datasets")
    if not isinstance(datasets, Sequence) or isinstance(datasets, (str, bytes, bytearray)) or not datasets:
        raise ResearchProtocolError("RUN_MANIFEST_DATASETS_MISSING")
    for index, dataset in enumerate(datasets):
        if (
            not isinstance(dataset, Mapping)
            or not isinstance(dataset.get("uri"), str)
            or not dataset.get("uri")
            or not canonical_sha256(dataset.get("sha256"))
        ):
            raise ResearchProtocolError("RUN_MANIFEST_DATASET_INVALID")
    costs = document.get("costs")
    capacity = document.get("capacity")
    if not isinstance(costs, Mapping) or not isinstance(capacity, Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_ECONOMICS_MISSING")
    for field in ("commission_per_unit", "slippage_bps", "impact_bps"):
        try:
            cost_value = decimal_value(costs.get(field), field)
        except ResearchProtocolError as error:
            raise ResearchProtocolError("RUN_MANIFEST_COST_INVALID", field) from error
        if cost_value < 0:
            raise ResearchProtocolError("RUN_MANIFEST_COST_INVALID", field)
    for field in ("spread_bps", "fee_bps", "borrow_bps", "funding_bps"):
        if field in costs:
            try:
                optional_cost = decimal_value(costs[field], field)
            except ResearchProtocolError as error:
                raise ResearchProtocolError("RUN_MANIFEST_COST_INVALID", field) from error
            if optional_cost < 0:
                raise ResearchProtocolError("RUN_MANIFEST_COST_INVALID", field)
    try:
        delay_ms = integer_value(costs.get("decision_to_fill_delay_ms"), "decision_to_fill_delay_ms", minimum=0)
    except ResearchProtocolError as error:
        raise ResearchProtocolError("RUN_MANIFEST_DELAY_INVALID") from error
    try:
        max_order = decimal_value(capacity.get("max_order_quantity"), "max_order_quantity")
        liquidity = decimal_value(capacity.get("available_liquidity"), "available_liquidity")
        participation = decimal_value(capacity.get("max_participation"), "max_participation")
    except ResearchProtocolError as error:
        raise ResearchProtocolError("RUN_MANIFEST_CAPACITY_INVALID") from error
    if max_order <= 0 or liquidity <= 0 or participation <= 0 or participation > 1:
        raise ResearchProtocolError("RUN_MANIFEST_CAPACITY_INVALID")
    if "max_quote_age_ms" in document:
        try:
            integer_value(document["max_quote_age_ms"], "max_quote_age_ms", minimum=0)
        except ResearchProtocolError as error:
            raise ResearchProtocolError("RUN_MANIFEST_QUOTE_AGE_INVALID") from error
    # These identity/evaluation fields are required by the durable run
    # contract.  Type-checking them here prevents a typo from silently
    # changing the meaning of a replay.
    if "deterministic_seed" not in document:
        raise ResearchProtocolError("RUN_MANIFEST_PARAMETERS_INVALID", "deterministic_seed")
    integer_value(document["deterministic_seed"], "deterministic_seed")
    if "numeric_tolerance" not in document:
        raise ResearchProtocolError("RUN_MANIFEST_NUMERIC_TOLERANCE_INVALID")
    tolerance = decimal_value(document["numeric_tolerance"], "numeric_tolerance")
    if tolerance < 0:
        raise ResearchProtocolError("RUN_MANIFEST_NUMERIC_TOLERANCE_INVALID")
    if "parameters" not in document or not isinstance(document["parameters"], Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_PARAMETERS_INVALID")
    if "output" not in document or not isinstance(document["output"], Mapping):
        raise ResearchProtocolError("RUN_MANIFEST_OUTPUT_INVALID")
    if "unsupported" not in document:
        raise ResearchProtocolError("RUN_MANIFEST_OUTPUT_INVALID")
    # An untouched final OOS segment is part of the executable validation
    # contract, rather than a narrative flag.  Requiring concrete boundaries
    # prevents a caller from accidentally reporting a walk-forward result as
    # final out-of-sample evidence (``true`` carries no timestamp semantics).
    if "final_out_of_sample" not in document:
        raise ResearchProtocolError(
            "RUN_MANIFEST_VALIDATION_INVALID", "final_out_of_sample"
        )
    try:
        _final_oos_bounds(document["final_out_of_sample"])
    except (KeyError, ResearchProtocolError) as error:
        raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID") from error
    unsupported = document["unsupported"]
    if (
        not isinstance(unsupported, Sequence)
        or isinstance(unsupported, (str, bytes, bytearray))
        or any(not isinstance(item, str) or not item for item in unsupported)
    ):
        raise ResearchProtocolError("RUN_MANIFEST_OUTPUT_INVALID")
    if "parameter_search_budget" not in document:
        raise ResearchProtocolError("RUN_MANIFEST_PARAMETERS_INVALID", "parameter_search_budget")
    budget_value = document["parameter_search_budget"]
    if isinstance(budget_value, Mapping):
        if not budget_value:
            raise ResearchProtocolError(
                "RUN_MANIFEST_PARAMETERS_INVALID", "parameter_search_budget"
            )
        try:
            canonical_json(budget_value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ResearchProtocolError("RUN_MANIFEST_PARAMETERS_INVALID") from error
        for budget_field in ("trials", "max_trials", "evaluations"):
            if budget_field in budget_value:
                integer_value(
                    budget_value[budget_field],
                    f"parameter_search_budget.{budget_field}",
                    minimum=0,
                )
    else:
        integer_value(budget_value, "parameter_search_budget", minimum=0)
    try:
        feature_horizon = integer_value(document.get("feature_horizon_ms"), "feature_horizon_ms", minimum=0)
        label_horizon = integer_value(document.get("label_horizon_ms"), "label_horizon_ms", minimum=0)
        purge = integer_value(document.get("purge_ms"), "purge_ms", minimum=0)
        embargo = integer_value(document.get("embargo_ms"), "embargo_ms", minimum=0)
    except ResearchProtocolError as error:
        raise ResearchProtocolError("RUN_MANIFEST_HORIZON_INVALID") from error
    validate_folds(
        document.get("folds", ()), feature_horizon, label_horizon, purge, embargo
    )
    try:
        final_start, _ = _final_oos_bounds(document["final_out_of_sample"])
        last_test_end = max(
            integer_value(fold["test_end_ms"], "test_end_ms", minimum=0)
            for fold in document["folds"]
        )
    except (KeyError, ResearchProtocolError) as error:
        raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID") from error
    if final_start <= last_test_end:
        raise ResearchProtocolError("RUN_MANIFEST_VALIDATION_INVALID")


def validate_folds(
    folds: Sequence[Mapping[str, Any]],
    feature_horizon_ms: int,
    label_horizon_ms: int,
    purge_ms: int,
    embargo_ms: int,
) -> None:
    if (
        isinstance(folds, (str, bytes, bytearray, Mapping))
        or not isinstance(folds, Sequence)
        or not folds
    ):
        raise ResearchProtocolError("RESEARCH_FOLDS_MISSING")
    try:
        feature_horizon_ms = integer_value(feature_horizon_ms, "feature_horizon_ms", minimum=0)
        label_horizon_ms = integer_value(label_horizon_ms, "label_horizon_ms", minimum=0)
        purge_ms = integer_value(purge_ms, "purge_ms", minimum=0)
        embargo_ms = integer_value(embargo_ms, "embargo_ms", minimum=0)
    except ResearchProtocolError as error:
        raise ResearchProtocolError("RESEARCH_HORIZON_INVALID") from error
    horizon = max(feature_horizon_ms, label_horizon_ms)
    if purge_ms < horizon or embargo_ms < horizon:
        raise ResearchProtocolError("RESEARCH_PURGE_EMBARGO_INSUFFICIENT")
    previous_test_end = -1
    for index, fold in enumerate(folds):
        if not isinstance(fold, Mapping):
            raise ResearchProtocolError("RESEARCH_FOLD_INVALID", str(index))
        try:
            train_start = integer_value(fold["train_start_ms"], "train_start_ms", minimum=0)
            train_end = integer_value(fold["train_end_ms"], "train_end_ms", minimum=0)
            test_start = integer_value(fold["test_start_ms"], "test_start_ms", minimum=0)
            test_end = integer_value(fold["test_end_ms"], "test_end_ms", minimum=0)
        except (KeyError, ResearchProtocolError) as error:
            raise ResearchProtocolError("RESEARCH_FOLD_INVALID", str(index)) from error
        if not (0 <= train_start < train_end < test_start < test_end):
            raise ResearchProtocolError("RESEARCH_FOLD_ORDER_INVALID", str(index))
        if test_start - train_end < purge_ms:
            raise ResearchProtocolError("RESEARCH_FOLD_PURGE_VIOLATION", str(index))
        if previous_test_end >= 0 and test_start <= previous_test_end:
            raise ResearchProtocolError("RESEARCH_FOLD_TEST_OVERLAP", str(index))
        previous_test_end = test_end


def _normalize_quotes_with_stats(
    quotes: Sequence[Mapping[str, Any]], log: EventLog | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Normalize quote observations and return deterministic quality counters.

    A quote stream may contain several instruments.  Ordering and duplicate
    detection are enforced per instrument; independent books may be
    interleaved arbitrarily because look-ahead checks use the selected book's
    timestamp rather than input-list position.  Quotes without an instrument
    use the legacy default stream and are available as a fallback for every
    target instrument.
    """

    try:
        quote_sequence = _sequence(quotes, "quotes")
    except ResearchProtocolError:
        raise
    normalized: list[dict[str, Any]] = []
    previous_by_instrument: dict[str, tuple[int, str]] = {}
    duplicate_count = 0
    raw_count = 0
    for index, raw in enumerate(quote_sequence):
        raw_count += 1
        if not isinstance(raw, Mapping):
            raise ResearchProtocolError("RESEARCH_QUOTE_INVALID", str(index))
        if any(not isinstance(key, str) or key not in _QUOTE_FIELDS for key in raw):
            raise ResearchProtocolError("RESEARCH_QUOTE_INVALID", str(index))
        try:
            ts = integer_value(raw["ts_ms"], "ts_ms")
            bid = decimal_value(raw["bid"], "bid")
            ask = decimal_value(raw["ask"], "ask")
        except KeyError as error:
            raise ResearchProtocolError("RESEARCH_QUOTE_INVALID", str(index)) from error
        instrument_value = raw.get("instrument", "")
        instrument = "" if instrument_value == "" else _instrument(instrument_value)
        if ts < 0 or bid <= 0 or ask < bid:
            raise ResearchProtocolError("RESEARCH_QUOTE_INVALID", str(index))
        canonical = canonical_json(
            {
                "instrument": instrument,
                "ts_ms": ts,
                "bid": decimal_text(bid),
                "ask": decimal_text(ask),
            }
        )
        previous = previous_by_instrument.get(instrument)
        if previous is not None:
            previous_ts, previous_canonical = previous
            if ts < previous_ts:
                raise ResearchProtocolError("RESEARCH_QUOTE_OUT_OF_ORDER", str(index))
            if ts == previous_ts:
                if canonical != previous_canonical:
                    raise ResearchProtocolError("RESEARCH_CHANGED_DUPLICATE_TIMESTAMP")
                duplicate_count += 1
                continue
        quote: dict[str, Any] = {
            "instrument": instrument,
            "ts_ms": ts,
            "bid": bid,
            "ask": ask,
        }
        normalized.append(quote)
        if log is not None:
            payload: dict[str, Any] = {
                "bid": decimal_text(bid),
                "ask": decimal_text(ask),
            }
            if instrument:
                payload["instrument"] = instrument
            log.append("quote", ts, payload)
        previous_by_instrument[instrument] = (ts, canonical)
    if not normalized:
        raise ResearchProtocolError("RESEARCH_QUOTES_MISSING")
    return normalized, {
        "quote_count": len(normalized),
        "raw_quote_count": raw_count,
        "duplicate_quote_count": duplicate_count,
        "out_of_order_quote_count": 0,
    }


def _normalize_quotes(
    quotes: Sequence[Mapping[str, Any]], log: EventLog | None = None
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper returning only normalized quotes."""

    normalized, _ = _normalize_quotes_with_stats(quotes, log)
    return normalized


def evaluate_run(
    manifest: Mapping[str, Any],
    quotes: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_run_manifest(manifest)
    _reject_ceremony_fields(quotes, "quotes")
    _reject_ceremony_fields(targets, "targets")
    _reject_capability_fields(quotes, "quotes")
    _reject_capability_fields(targets, "targets")
    try:
        # Preserve provenance of the caller-provided observation stream in a
        # raw digest.  ``input_digest`` below is the normalized semantic digest
        # (decimal spellings and exact duplicate records are intentionally
        # collapsed), while this digest binds the original JSON-shaped values.
        raw_input_digest = sha256_json(
            {
                "quotes": list(_sequence(quotes, "quotes")),
                "targets": list(_sequence(targets, "targets")),
            }
        )
    except (ResearchProtocolError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, ResearchProtocolError):
            raise
        raise ResearchProtocolError("RESEARCH_INPUT_INVALID") from error
    event_inputs: list[tuple[int, int, str, dict[str, Any]]] = []
    normalized_quotes, quote_quality = _normalize_quotes_with_stats(quotes)

    # Build independent quote books.  The empty key is the backwards-
    # compatible single-instrument stream and is used as a fallback when a
    # target names an instrument but the quote feed does not.
    quote_books: dict[str, list[dict[str, Any]]] = {}
    for quote in normalized_quotes:
        instrument = quote["instrument"]
        quote_books.setdefault(instrument, []).append(quote)
        payload: dict[str, Any] = {
            "bid": decimal_text(quote["bid"]),
            "ask": decimal_text(quote["ask"]),
        }
        if instrument:
            payload["instrument"] = instrument
        event_inputs.append((quote["ts_ms"], 0, "quote", payload))
    quote_times_by_instrument = {
        instrument: [quote["ts_ms"] for quote in book]
        for instrument, book in quote_books.items()
    }

    costs = manifest["costs"]
    capacity = manifest["capacity"]
    commission = decimal_value(costs["commission_per_unit"], "commission_per_unit")
    slippage_bps = decimal_value(costs["slippage_bps"], "slippage_bps")
    impact_bps = decimal_value(costs["impact_bps"], "impact_bps")
    optional_costs = {
        field: decimal_value(costs.get(field, "0"), field)
        for field in ("spread_bps", "fee_bps", "borrow_bps", "funding_bps")
    }
    spread_bps = optional_costs["spread_bps"]
    fee_bps = optional_costs["fee_bps"]
    borrow_bps = optional_costs["borrow_bps"]
    funding_bps = optional_costs["funding_bps"]
    delay_ms = integer_value(costs["decision_to_fill_delay_ms"], "decision_to_fill_delay_ms", minimum=0)
    max_order = decimal_value(capacity["max_order_quantity"], "max_order_quantity")
    available_liquidity = decimal_value(capacity["available_liquidity"], "available_liquidity")
    max_participation = decimal_value(capacity["max_participation"], "max_participation")
    max_quote_age_ms = integer_value(manifest.get("max_quote_age_ms", 60000), "max_quote_age_ms", minimum=0)

    try:
        target_sequence = _sequence(targets, "targets")
    except ResearchProtocolError:
        raise
    position_by_instrument: dict[str, Decimal] = {}
    cash_by_instrument: dict[str, Decimal] = {}
    cash = Decimal(0)
    explicit_cost = Decimal(0)
    turnover = Decimal(0)
    previous_target_by_instrument: dict[str, int] = {}
    seen_targets: dict[tuple[str, int], str] = {}
    duplicate_target_count = 0
    trade_count = 0
    max_capacity_utilization = Decimal(0)
    fills: list[dict[str, Any]] = []
    normalized_targets: list[dict[str, Any]] = []

    for index, raw_target in enumerate(target_sequence):
        if not isinstance(raw_target, Mapping):
            raise ResearchProtocolError("RESEARCH_TARGET_INVALID", str(index))
        if any(
            not isinstance(key, str) or key not in _TARGET_FIELDS
            for key in raw_target
        ):
            raise ResearchProtocolError("RESEARCH_TARGET_INVALID", str(index))
        try:
            instrument = _instrument(raw_target.get("instrument"))
            ts = integer_value(raw_target["ts_ms"], "ts_ms")
            target = decimal_value(raw_target["target_position"], "target_position")
        except KeyError as error:
            raise ResearchProtocolError("RESEARCH_TARGET_INVALID", str(index)) from error
        if ts < 0:
            raise ResearchProtocolError("RESEARCH_TARGET_OUT_OF_ORDER", str(index))
        if ts < previous_target_by_instrument.get(instrument, -1):
            raise ResearchProtocolError("RESEARCH_TARGET_OUT_OF_ORDER", str(index))
        signature = sha256_json(
            {"instrument": instrument, "target_position": decimal_text(target)}
        )
        key = (instrument, ts)
        if key in seen_targets:
            if seen_targets[key] != signature:
                raise ResearchProtocolError("RESEARCH_CHANGED_DUPLICATE_TIMESTAMP")
            duplicate_target_count += 1
            continue
        seen_targets[key] = signature
        previous_target_by_instrument[instrument] = ts
        normalized_targets.append(
            {
                "instrument": instrument,
                "ts_ms": ts,
                "target_position": decimal_text(target),
            }
        )

        quote_book = quote_books.get(instrument) or quote_books.get("")
        if not quote_book:
            raise ResearchProtocolError("RESEARCH_LOOKAHEAD_REQUIRED", instrument)
        quote_times = quote_times_by_instrument.get(instrument)
        if quote_times is None:
            quote_times = quote_times_by_instrument[""]
        quote_index = bisect_right(quote_times, ts) - 1
        if quote_index < 0:
            raise ResearchProtocolError("RESEARCH_LOOKAHEAD_REQUIRED", instrument)
        quote = quote_book[quote_index]
        if ts - quote["ts_ms"] > max_quote_age_ms:
            raise ResearchProtocolError("RESEARCH_QUOTE_STALE", instrument)
        position = position_by_instrument.get(instrument, Decimal(0))
        delta = target - position
        event_inputs.append(
            (
                ts,
                1,
                "target",
                {
                    "instrument": instrument,
                    "target_position": decimal_text(target),
                    "current_position": decimal_text(position),
                    "delta": decimal_text(delta),
                },
            )
        )
        if delta == 0:
            # Retain an explicit zero position for instruments that only have
            # no-op targets; summaries should not make a valid instrument
            # disappear merely because it never traded.
            position_by_instrument.setdefault(instrument, Decimal(0))
            continue
        # A no-op target has no execution and therefore does not need a quote
        # at the hypothetical fill time.  For an actual order, enforce the
        # delay/quote boundary separately so a future quote can never satisfy
        # a decision retroactively.
        fill_at = ts + delay_ms
        if fill_at > _MAX_PROTOCOL_INTEGER:
            raise ResearchProtocolError("RESEARCH_FILL_TIMESTAMP_INVALID", instrument)
        fill_index = bisect_right(quote_times, fill_at) - 1
        if fill_index < 0:
            raise ResearchProtocolError("RESEARCH_FILL_QUOTE_MISSING", instrument)
        fill_quote = quote_book[fill_index]
        if fill_at - fill_quote["ts_ms"] > max_quote_age_ms:
            raise ResearchProtocolError("RESEARCH_FILL_QUOTE_STALE", instrument)
        absolute_delta = abs(delta)
        participation_limit = available_liquidity * max_participation
        if absolute_delta > max_order or absolute_delta > participation_limit:
            raise ResearchProtocolError("RESEARCH_CAPACITY_EXCEEDED", instrument)
        utilization = absolute_delta / available_liquidity
        max_capacity_utilization = max(max_capacity_utilization, utilization)
        # ``spread_bps`` is an optional conservative spread stress layered on
        # top of the observed bid/ask quote.  The quote itself already
        # determines the side of the market (ask for buys, bid for sells), so
        # applying the stress as adverse bps avoids silently replacing a
        # point-in-time quote with a future/mid-market value.
        adverse_bps = spread_bps + slippage_bps + impact_bps * utilization
        if delta > 0:
            base_price = fill_quote["ask"]
            fill_price = base_price * (Decimal(1) + adverse_bps / Decimal(10000))
        else:
            base_price = fill_quote["bid"]
            fill_price = base_price * (Decimal(1) - adverse_bps / Decimal(10000))
        if fill_price <= 0:
            raise ResearchProtocolError("RESEARCH_FILL_PRICE_INVALID", instrument)
        commission_cost = absolute_delta * commission
        slippage_cost = absolute_delta * abs(fill_price - base_price)
        # ``fee_bps`` is a notional fee charged once per fill.  It is kept
        # separate from slippage in the event payload so downstream analysis
        # can attribute all-in costs without reverse-engineering the price.
        fee_cost = absolute_delta * fill_price * fee_bps / Decimal(10000)
        cost = commission_cost + slippage_cost + fee_cost
        cash -= delta * fill_price
        cash -= commission_cost + fee_cost
        cash_by_instrument[instrument] = cash_by_instrument.get(instrument, Decimal(0)) - (
            delta * fill_price + commission_cost + fee_cost
        )
        explicit_cost += cost
        turnover += absolute_delta * fill_price
        position_by_instrument[instrument] = target
        trade_count += 1
        fill_payload = {
            "instrument": instrument,
            "decision_ts_ms": ts,
            "quote_ts_ms": fill_quote["ts_ms"],
            "fill_ts_ms": fill_at,
            "quantity": decimal_text(delta),
            "fill_price": decimal_text(fill_price),
            "commission": decimal_text(commission_cost),
            "slippage_and_impact": decimal_text(slippage_cost),
            "fee": decimal_text(fee_cost),
        }
        event_inputs.append((fill_at, 2, "fill", fill_payload))
        fills.append(
            {
                "ts_ms": fill_at,
                "instrument": instrument,
                "quantity": delta,
                "fill_price": fill_price,
                "commission": commission_cost,
                "fee": fee_cost,
                "cost": cost,
            }
        )

    # Canonical tie-breaking includes instrument and payload.  This makes the
    # output independent of how equal-timestamp multi-instrument records were
    # interleaved by the caller.
    def event_sort_key(item: tuple[int, int, str, dict[str, Any]]) -> tuple[Any, ...]:
        timestamp, priority, kind, payload = item
        return (
            timestamp,
            priority,
            kind,
            str(payload.get("instrument", "")),
            canonical_json(payload),
        )

    log = EventLog()
    for timestamp, priority, kind, payload in sorted(event_inputs, key=event_sort_key):
        log.append(kind, timestamp, payload)

    # Replay fills against point-in-time marks to derive risk/quality metrics.
    # No mark uses a quote newer than the observation being evaluated.  The
    # timeline includes fill-only timestamps as well as quote timestamps so
    # annualized holding costs and drawdown do not silently skip a delayed
    # execution between two observations.
    quote_timeline = sorted(
        normalized_quotes,
        key=lambda quote: (quote["ts_ms"], quote["instrument"]),
    )
    quote_marks: dict[str, Decimal] = {}
    actual_positions: dict[str, Decimal] = {}
    replay_cash = Decimal(0)
    fill_index = 0
    sorted_fills = sorted(
        fills,
        key=lambda fill: (fill["ts_ms"], fill["instrument"], decimal_text(fill["quantity"])),
    )
    def mark_for(instrument: str, marks: Mapping[str, Decimal]) -> Decimal:
        # A legacy quote stream without an instrument is a valid fallback for
        # named targets.  Never mark such a position at zero merely because its
        # quote arrived under the empty/default key.
        return marks.get(instrument, marks.get("", Decimal(0)))

    # Group quotes by timestamp before applying marks.  This keeps equal-time
    # multi-instrument observations atomic: a mark in one book cannot create a
    # transient zero-value drawdown while another book at the same timestamp is
    # still being processed.
    quotes_by_timestamp: dict[int, list[dict[str, Any]]] = {}
    for quote in quote_timeline:
        quotes_by_timestamp.setdefault(quote["ts_ms"], []).append(quote)
    timeline_timestamps = sorted(
        set(quotes_by_timestamp)
        | {fill["ts_ms"] for fill in sorted_fills}
    )
    equity_points: list[tuple[int, Decimal, Decimal]] = []
    holding_cost = Decimal(0)
    holding_cost_by_instrument: dict[str, Decimal] = {}
    year_ms = Decimal(365 * 24 * 60 * 60 * 1000)
    basis_points = Decimal(10000)
    for timeline_index, timestamp in enumerate(timeline_timestamps):
        while (
            fill_index < len(sorted_fills)
            and sorted_fills[fill_index]["ts_ms"] <= timestamp
        ):
            fill = sorted_fills[fill_index]
            instrument = fill["instrument"]
            quantity = fill["quantity"]
            fill_price = fill["fill_price"]
            replay_cash -= quantity * fill_price
            # Commission and notional fees are paid from cash at execution.
            # Slippage/impact/spread stress is already reflected in
            # ``fill_price`` and must not be charged a second time here.
            replay_cash -= fill["commission"] + fill.get("fee", Decimal(0))
            actual_positions[instrument] = (
                actual_positions.get(instrument, Decimal(0)) + quantity
            )
            fill_index += 1
        for quote in quotes_by_timestamp.get(timestamp, ()):
            quote_marks[quote["instrument"]] = (
                quote["bid"] + quote["ask"]
            ) / Decimal(2)
        equity = replay_cash + sum(
            position * mark_for(name, quote_marks)
            for name, position in actual_positions.items()
        )
        gross = sum(
            abs(position * mark_for(name, quote_marks))
            for name, position in actual_positions.items()
        )
        equity_points.append((timestamp, equity, gross))

        # Borrow/funding assumptions are annualized basis-point rates.  Charge
        # them on point-in-time marked exposure over the interval until the
        # next quote/fill event.  Borrow applies to short exposure; funding
        # applies to gross exposure in either direction.  This convention is
        # explicit and deterministic, while callers may leave either rate at
        # zero when the asset class does not require it.
        if timeline_index + 1 < len(timeline_timestamps):
            duration_ms = timeline_timestamps[timeline_index + 1] - timestamp
            if duration_ms > 0 and actual_positions:
                interval_holding = Decimal(0)
                for instrument, position in actual_positions.items():
                    mark = mark_for(instrument, quote_marks)
                    gross_exposure = abs(position * mark)
                    short_exposure = abs(min(position, Decimal(0)) * mark)
                    instrument_cost = (
                        short_exposure * borrow_bps
                        + gross_exposure * funding_bps
                    ) / basis_points * Decimal(duration_ms) / year_ms
                    if instrument_cost:
                        holding_cost_by_instrument[instrument] = (
                            holding_cost_by_instrument.get(instrument, Decimal(0))
                            + instrument_cost
                        )
                        interval_holding += instrument_cost
                if interval_holding:
                    replay_cash -= interval_holding
                    holding_cost += interval_holding

    # ``timeline_timestamps`` includes every fill, so this normally has no
    # remainder.  Keep a defensive loop for callers that construct a fill list
    # manually while using the public evaluator internals.
    while fill_index < len(sorted_fills):
        fill = sorted_fills[fill_index]
        instrument = fill["instrument"]
        replay_cash -= fill["quantity"] * fill["fill_price"]
        replay_cash -= fill["commission"] + fill.get("fee", Decimal(0))
        actual_positions[instrument] = (
            actual_positions.get(instrument, Decimal(0)) + fill["quantity"]
        )
        fill_index += 1

    # The primary cash ledger is built during target evaluation.  Apply the
    # interval holding charges to that ledger as well, then attribute them to
    # each instrument for concentration/PnL slices.
    if holding_cost:
        cash -= holding_cost
        for instrument, instrument_cost in holding_cost_by_instrument.items():
            cash_by_instrument[instrument] = (
                cash_by_instrument.get(instrument, Decimal(0)) - instrument_cost
            )
        explicit_cost += holding_cost

    # Terminal marks use the last point-in-time quote of each instrument (or
    # the default stream for a target-only instrument, which cannot occur after
    # the quote checks above).
    terminal_marks: dict[str, Decimal] = {}
    for instrument, book in quote_books.items():
        terminal_marks[instrument] = (book[-1]["bid"] + book[-1]["ask"]) / Decimal(2)
    for instrument in actual_positions:
        if instrument not in terminal_marks and "" in terminal_marks:
            terminal_marks[instrument] = terminal_marks[""]
    # Preserve the historical API's exact final position/net-PnL semantics,
    # while using the replay curve for drawdown and exposure metrics.
    final_positions = position_by_instrument
    net_pnl = cash + sum(
        position * mark_for(instrument, terminal_marks)
        for instrument, position in final_positions.items()
    )
    peak_equity = Decimal(0)
    max_drawdown = Decimal(0)
    max_gross_exposure = Decimal(0)
    worst_step_loss: Decimal | None = None
    previous_equity: Decimal | None = None
    drawdown_start_ts: int | None = None
    max_drawdown_duration_ms = 0
    for timestamp, equity, gross in equity_points:
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        max_gross_exposure = max(max_gross_exposure, gross)
        if equity < peak_equity:
            if drawdown_start_ts is None:
                drawdown_start_ts = timestamp
        elif drawdown_start_ts is not None:
            max_drawdown_duration_ms = max(
                max_drawdown_duration_ms, timestamp - drawdown_start_ts
            )
            drawdown_start_ts = None
        if previous_equity is not None:
            loss = equity - previous_equity
            if worst_step_loss is None or loss < worst_step_loss:
                worst_step_loss = loss
        previous_equity = equity
    max_gross_exposure = max(max_gross_exposure, sum(
        abs(position * mark_for(instrument, terminal_marks))
        for instrument, position in final_positions.items()
    ))
    if worst_step_loss is None:
        worst_step_loss = Decimal(0)
    if drawdown_start_ts is not None and equity_points:
        max_drawdown_duration_ms = max(
            max_drawdown_duration_ms, equity_points[-1][0] - drawdown_start_ts
        )

    first_ts = quote_timeline[0]["ts_ms"]
    last_ts = quote_timeline[-1]["ts_ms"]
    total_span = max(0, last_ts - first_ts)
    # Approximate time-in-market from the same unified quote/fill timeline
    # used for holding costs.  This captures a delayed fill that lands between
    # two quotes instead of waiting until the next quote to count exposure.
    # Clamp intervals to the quote evaluation window so a terminal fill does
    # not expand the denominator beyond the observed data range.
    in_market_ms = 0
    interval_positions: dict[str, Decimal] = {}
    interval_fill_cursor = 0
    for interval_index, timestamp in enumerate(timeline_timestamps[:-1]):
        while (
            interval_fill_cursor < len(sorted_fills)
            and sorted_fills[interval_fill_cursor]["ts_ms"] <= timestamp
        ):
            fill = sorted_fills[interval_fill_cursor]
            name = fill["instrument"]
            interval_positions[name] = interval_positions.get(name, Decimal(0)) + fill["quantity"]
            interval_fill_cursor += 1
        interval_end = timeline_timestamps[interval_index + 1]
        left = max(timestamp, first_ts)
        right = min(interval_end, last_ts)
        if right > left and any(value != 0 for value in interval_positions.values()):
            in_market_ms += right - left
    time_in_market = (
        Decimal(in_market_ms) / Decimal(total_span) if total_span else Decimal(0)
    )

    # Instrument PnL concentration is represented by final marked contribution
    # and normalized to shares.  This remains useful for a one-instrument run
    # and deterministic for multi-instrument runs.
    instrument_contribution = {
        instrument: cash_by_instrument.get(instrument, Decimal(0))
        + position * mark_for(instrument, terminal_marks)
        for instrument, position in final_positions.items()
    }
    contribution_denominator = sum(abs(value) for value in instrument_contribution.values())
    concentration = {
        instrument: decimal_text(
            abs(value) / contribution_denominator if contribution_denominator else Decimal(0)
        )
        for instrument, value in sorted(instrument_contribution.items())
    }
    worst_instrument = None
    if instrument_contribution:
        worst_instrument = min(
            instrument_contribution,
            key=lambda instrument: (instrument_contribution[instrument], instrument),
        )
    nonzero_contributions = [
        value for value in instrument_contribution.values() if value != 0
    ]
    winning_contributions = [value for value in nonzero_contributions if value > 0]
    losing_contributions = [value for value in nonzero_contributions if value < 0]
    hit_rate = (
        Decimal(len(winning_contributions)) / Decimal(len(nonzero_contributions))
        if nonzero_contributions
        else Decimal(0)
    )
    payoff_ratio = (
        sum(winning_contributions) / abs(sum(losing_contributions))
        if losing_contributions
        else None
    )
    spread_values = [
        (quote["ask"] - quote["bid"]) / ((quote["ask"] + quote["bid"]) / Decimal(2)) * Decimal(10000)
        for quote in normalized_quotes
    ]
    average_spread_bps = (
        sum(spread_values) / Decimal(len(spread_values)) if spread_values else Decimal(0)
    )
    cost_share = (
        explicit_cost / turnover if turnover else Decimal(0)
    )
    # Lightweight, point-in-time slices.  The canonical protocol does not
    # prescribe a strategy-specific regime classifier, so ``unknown`` is an
    # explicit bucket rather than an invented regime label.  Time-of-day and
    # volatility buckets are derived solely from observations available up to
    # each quote.
    time_of_day_acc: dict[str, list[Decimal]] = {}
    volatility_acc: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    previous_mid_by_instrument: dict[str, Decimal] = {}
    for quote in normalized_quotes:
        mid = (quote["bid"] + quote["ask"]) / Decimal(2)
        quote_spread_bps = (quote["ask"] - quote["bid"]) / mid * Decimal(10000)
        hour = f"{(quote['ts_ms'] // 3_600_000) % 24:02d}:00"
        time_of_day_acc.setdefault(hour, []).append(quote_spread_bps)
        previous_mid = previous_mid_by_instrument.get(quote["instrument"])
        if previous_mid is not None and previous_mid > 0:
            move_bps = abs(mid - previous_mid) / previous_mid * Decimal(10000)
            bucket = "low" if move_bps <= 1 else "medium" if move_bps <= 5 else "high"
            volatility_acc[bucket] += 1
        previous_mid_by_instrument[quote["instrument"]] = mid
    time_of_day_slices = {
        hour: {
            "quote_count": len(values),
            "average_spread_bps": decimal_text(sum(values) / Decimal(len(values))),
        }
        for hour, values in sorted(time_of_day_acc.items())
    }
    volatility_slices = {
        bucket: {"quote_count": count}
        for bucket, count in volatility_acc.items()
        if count
    }
    fold_summaries: list[dict[str, Any]] = []
    left_closed = manifest["session_semantics"] in {
        "closed_interval_left",
        "left_closed",
        "half_open",
    }

    def in_fold_interval(timestamp: int, start: int, end: int) -> bool:
        return start <= timestamp < end if left_closed else start <= timestamp <= end

    for index, fold in enumerate(manifest["folds"]):
        train_start = integer_value(fold["train_start_ms"], "train_start_ms", minimum=0)
        train_end = integer_value(fold["train_end_ms"], "train_end_ms", minimum=0)
        test_start = integer_value(fold["test_start_ms"], "test_start_ms", minimum=0)
        test_end = integer_value(fold["test_end_ms"], "test_end_ms", minimum=0)
        fold_summaries.append(
            {
                "index": index,
                "train_start_ms": train_start,
                "train_end_ms": train_end,
                "test_start_ms": test_start,
                "test_end_ms": test_end,
                "train_quote_count": sum(
                    in_fold_interval(quote["ts_ms"], train_start, train_end)
                    for quote in normalized_quotes
                ),
                "test_quote_count": sum(
                    in_fold_interval(quote["ts_ms"], test_start, test_end)
                    for quote in normalized_quotes
                ),
                "train_target_count": sum(
                    in_fold_interval(target["ts_ms"], train_start, train_end)
                    for target in normalized_targets
                ),
                "test_target_count": sum(
                    in_fold_interval(target["ts_ms"], test_start, test_end)
                    for target in normalized_targets
                ),
            }
        )
    deterministic_seed = integer_value(
        manifest.get("deterministic_seed", 0), "deterministic_seed"
    )
    numeric_tolerance = decimal_value(
        manifest.get("numeric_tolerance", "0"), "numeric_tolerance"
    )
    strategy = manifest.get("strategy")
    if not isinstance(strategy, Mapping):
        strategy = {}
    budget_value = manifest.get("parameter_search_budget", 0)
    if isinstance(budget_value, Mapping):
        parameter_search_budget: int | dict[str, Any] = _json_copy(budget_value)
    else:
        parameter_search_budget = integer_value(
            budget_value, "parameter_search_budget", minimum=0
        )
    input_digest = sha256_json(
        {
            "quotes": [
                {
                    "instrument": quote["instrument"],
                    "ts_ms": quote["ts_ms"],
                    "bid": decimal_text(quote["bid"]),
                    "ask": decimal_text(quote["ask"]),
                }
                for quote in normalized_quotes
            ],
            "targets": normalized_targets,
        }
    )
    data_quality = dict(quote_quality)
    data_quality.update(
        {
            "target_count": len(seen_targets),
            "raw_target_count": len(target_sequence),
            "duplicate_target_count": duplicate_target_count,
            "stale_quote_count": 0,
            "missing_quote_count": 0,
            "calendar_gap_count": sum(
                1
                for book in quote_books.values()
                for left, right in zip(book, book[1:])
                if right["ts_ms"] - left["ts_ms"] > max_quote_age_ms
            ),
        }
    )
    summary_without_digest = {
        "schema": "heptatrader.run-summary.v1",
        "run_id": manifest["run_id"],
        "status": "VALID",
        "reason_code": "VALID",
        "failure_reason": None,
        "failures": [],
        "strategy_digest": manifest["strategy_digest"],
        "config_digest": manifest["config_digest"],
        "dataset_digests": [dataset["sha256"] for dataset in manifest["datasets"]],
        "input_digest": input_digest,
        "raw_input_digest": raw_input_digest,
        "strategy_id": strategy.get("id"),
        "strategy_version": strategy.get("version"),
        "calendar": manifest["calendar"],
        "session_semantics": manifest["session_semantics"],
        "symbol_mapping": manifest["symbol_mapping"],
        "deterministic_seed": deterministic_seed,
        "numeric_tolerance": decimal_text(numeric_tolerance),
        "parameters": _json_copy(manifest.get("parameters", {})),
        "parameter_search_budget": parameter_search_budget,
        "output": _json_copy(manifest.get("output", {})),
        "unsupported": _json_copy(list(manifest.get("unsupported", ()))),
        "event_count": len(log.events),
        "event_log_digest": log.digest,
        "net_pnl": decimal_text(net_pnl),
        "explicit_cost": decimal_text(explicit_cost),
        "holding_cost": decimal_text(holding_cost),
        "cost_share": decimal_text(cost_share),
        "turnover": decimal_text(turnover),
        "trade_count": trade_count,
        "hit_rate": decimal_text(hit_rate),
        "payoff_ratio": decimal_text(payoff_ratio) if payoff_ratio is not None else None,
        "final_position": decimal_text(sum(final_positions.values(), Decimal(0))),
        "final_positions": {
            instrument: decimal_text(position)
            for instrument, position in sorted(final_positions.items())
        },
        "max_drawdown": decimal_text(max_drawdown),
        "drawdown_duration_ms": max_drawdown_duration_ms,
        "tail_loss": decimal_text(min(Decimal(0), worst_step_loss)),
        "exposure": {
            "gross": decimal_text(
                sum(
                    abs(position * mark_for(instrument, terminal_marks))
                    for instrument, position in final_positions.items()
                )
            ),
            "net": decimal_text(
                sum(
                    position * mark_for(instrument, terminal_marks)
                    for instrument, position in final_positions.items()
                )
            ),
            "final_gross": decimal_text(
                sum(
                    abs(position * mark_for(instrument, terminal_marks))
                    for instrument, position in final_positions.items()
                )
            ),
            "max_gross": decimal_text(max_gross_exposure),
            "final_net": decimal_text(
                sum(
                    position * mark_for(instrument, terminal_marks)
                    for instrument, position in final_positions.items()
                )
            ),
        },
        "capacity": {
            "max_order_quantity": decimal_text(max_order),
            "available_liquidity": decimal_text(available_liquidity),
            "max_participation": decimal_text(max_participation),
            "max_utilization": decimal_text(max_capacity_utilization),
        },
        "cost_model": {
            "commission_per_unit": decimal_text(commission),
            "spread_bps": decimal_text(spread_bps),
            "slippage_bps": decimal_text(slippage_bps),
            "impact_bps": decimal_text(impact_bps),
            "fee_bps": decimal_text(fee_bps),
            "borrow_bps": decimal_text(borrow_bps),
            "funding_bps": decimal_text(funding_bps),
            "decision_to_fill_delay_ms": delay_ms,
            "spread_application": "additive_adverse_fill_bps",
            "fee_application": "per_fill_notional_bps",
            "borrow_application": "annualized_short_exposure_bps",
            "funding_application": "annualized_gross_exposure_bps",
            "annualization_ms": int(year_ms),
        },
        "evaluation_window": {
            "start_ts_ms": quote_timeline[0]["ts_ms"],
            "end_ts_ms": quote_timeline[-1]["ts_ms"],
        },
        "walk_forward": {
            "feature_horizon_ms": integer_value(
                manifest["feature_horizon_ms"], "feature_horizon_ms", minimum=0
            ),
            "label_horizon_ms": integer_value(
                manifest["label_horizon_ms"], "label_horizon_ms", minimum=0
            ),
            "purge_ms": integer_value(manifest["purge_ms"], "purge_ms", minimum=0),
            "embargo_ms": integer_value(manifest["embargo_ms"], "embargo_ms", minimum=0),
            "folds": fold_summaries,
            "final_out_of_sample": _json_copy(
                manifest.get("final_out_of_sample", None)
            ),
        },
        "features": {},
        "gates": {},
        "time_in_market": decimal_text(time_in_market),
        "slices": {
            "instrument_pnl": {
                instrument: decimal_text(value)
                for instrument, value in sorted(instrument_contribution.items())
            },
            "time_of_day": time_of_day_slices,
            "volatility": volatility_slices,
            "spread": {"average_bps": decimal_text(average_spread_bps)},
            "regime": {"unknown": {"quote_count": len(normalized_quotes)}},
        },
        "worst_slice": (
            {"dimension": "instrument", "value": worst_instrument}
            if worst_instrument is not None
            else None
        ),
        "concentration": concentration,
        "data_quality": data_quality,
        "validation": {
            "manifest": "PASS",
            "point_in_time": "PASS",
            "lookahead": "PASS",
            "purged_walk_forward": "PASS",
            "cost_model": "PASS",
            "capacity": "PASS",
            "determinism": "PASS",
        },
        "metrics": {
            "net_pnl": decimal_text(net_pnl),
            "holding_cost": decimal_text(holding_cost),
            "net_return": None,
            "volatility": None,
            "sharpe": None,
            "sortino": None,
            "max_drawdown": decimal_text(max_drawdown),
            "drawdown_duration_ms": max_drawdown_duration_ms,
            "recovery_duration_ms": None,
            "turnover": decimal_text(turnover),
            "trade_count": trade_count,
            "hit_rate": decimal_text(hit_rate),
            "payoff_ratio": decimal_text(payoff_ratio) if payoff_ratio is not None else None,
            "tail_loss": decimal_text(min(Decimal(0), worst_step_loss)),
            "time_in_market": decimal_text(time_in_market),
        },
        "regime_slices": {"unknown": {"quote_count": len(normalized_quotes)}},
        "source_revision": manifest["source_revision"],
        "manifest_digest": sha256_json(manifest),
    }
    summary = dict(summary_without_digest)
    summary["output_digest"] = sha256_json(
        {
            "manifest": manifest,
            "events": [event.as_dict() for event in log.events],
            "summary": summary_without_digest,
        }
    )
    summary["digests"] = {
        "manifest": summary["manifest_digest"],
        "input": summary["input_digest"],
        "raw_input": summary["raw_input_digest"],
        "event_log": summary["event_log_digest"],
        "output": summary["output_digest"],
    }
    return summary


def sample_run_manifest(adverse: bool = False) -> dict[str, Any]:
    return {
        "schema": "heptatrader.run-manifest.v1",
        "mode": "shadow",
        "run_id": "fixture-eurusd-v1",
        "source_revision": "0123456789abcdef0123456789abcdef01234567",
        "strategy": {
            "id": "eurusd-confirmed-momentum-shadow-v2",
            "version": "2.0.0",
            "definition_digest": "sha256:" + "5" * 64,
            "implementation_digest": "sha256:" + "4" * 64,
        },
        "strategy_digest": "sha256:" + "1" * 64,
        "config_digest": "sha256:" + "2" * 64,
        "datasets": [{"uri": "fixture://eurusd", "sha256": "sha256:" + "3" * 64}],
        "calendar": "UTC-24x5-fixture",
        "session_semantics": "closed_interval_left",
        "symbol_mapping": "EUR.USD=CASH:EUR/USD",
        "feature_horizon_ms": 1000,
        "label_horizon_ms": 1000,
        "purge_ms": 1000,
        "embargo_ms": 1000,
        "folds": [
            {"train_start_ms": 0, "train_end_ms": 10000,
             "test_start_ms": 11000, "test_end_ms": 20000},
            {"train_start_ms": 0, "train_end_ms": 20000,
             "test_start_ms": 21000, "test_end_ms": 30000},
        ],
        "final_out_of_sample": {"start_ms": 31000, "end_ms": 40000},
        "costs": {
            "commission_per_unit": "0.000001" if not adverse else "0.00005",
            "slippage_bps": "0.2" if not adverse else "8",
            "impact_bps": "0.5" if not adverse else "10",
            "decision_to_fill_delay_ms": 0,
        },
        "capacity": {
            "max_order_quantity": "1000000",
            "available_liquidity": "10000000",
            "max_participation": "0.2",
        },
        "max_quote_age_ms": 5000,
        "parameters": {"fixture": True},
        "parameter_search_budget": 0,
        "deterministic_seed": 0,
        "numeric_tolerance": "0.000000000001",
        "output": {"event_log": "memory", "summary": "stdout"},
        "unsupported": ["paper_promotion", "live_promotion"],
    }


def sample_quotes() -> list[dict[str, Any]]:
    return [
        {"ts_ms": 1000, "bid": "1.1000", "ask": "1.1002"},
        {"ts_ms": 2000, "bid": "1.1010", "ask": "1.1012"},
        {"ts_ms": 3000, "bid": "1.1020", "ask": "1.1022"},
    ]


def sample_targets() -> list[dict[str, Any]]:
    return [
        {"ts_ms": 1000, "instrument": "EUR.USD", "target_position": "100000"},
        {"ts_ms": 2000, "instrument": "EUR.USD", "target_position": "0"},
    ]


def self_test() -> dict[str, Any]:
    first = evaluate_run(sample_run_manifest(), sample_quotes(), sample_targets())
    second = evaluate_run(sample_run_manifest(), sample_quotes(), sample_targets())
    if first != second:
        raise ResearchProtocolError("RESEARCH_DETERMINISM_FAILED")
    adverse = evaluate_run(sample_run_manifest(adverse=True), sample_quotes(), sample_targets())
    if Decimal(adverse["net_pnl"]) >= Decimal(first["net_pnl"]):
        raise ResearchProtocolError("RESEARCH_ADVERSE_COST_TEST_FAILED")
    return first


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path, code: str = "RESEARCH_JSON_INVALID") -> Any:
    try:
        contents = path.read_bytes()
        if len(contents) > _MAX_JSON_BYTES:
            raise ResearchProtocolError("RESEARCH_JSON_TOO_LARGE", str(path))
        text = contents.decode("utf-8-sig")
        return json.loads(text, object_pairs_hook=_strict_object)
    except ResearchProtocolError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ResearchProtocolError(code, str(path)) from error


def _repository_root(path: Path) -> Path:
    """Find the repository root for relative static-manifest references."""

    candidates = (path.parent, *path.parents)
    for candidate in candidates:
        if (candidate / "strategies").is_dir() and (candidate / "scripts").is_dir():
            return candidate
    # A caller may intentionally provide a standalone static manifest.  In
    # that case relative paths are resolved from its containing directory.
    return path.parent


def _resolve_source_root(manifest_path: Path, explicit_root: str | None) -> Path:
    """Resolve the source tree used for static-manifest verification.

    The runtime install intentionally contains the runner and contract
    manifest, but not experimental strategy source assets.  Keeping the root
    explicit makes that boundary visible to callers: an installed runner can
    still execute ``self-test``/``run`` and can verify a checked-out manifest
    when ``--root`` names the checkout, while a missing source tree fails
    closed instead of silently treating absent assets as verified.
    """

    # ``Path("").resolve()`` silently becomes the process working directory.
    # Treat an explicitly supplied empty root as invalid instead of allowing a
    # caller to believe it selected a source checkout while verification uses
    # an unrelated cwd (especially relevant for installed runners).
    if explicit_root is not None:
        if not isinstance(explicit_root, str) or explicit_root == "":
            raise ResearchProtocolError("RESEARCH_SOURCE_ROOT_INVALID")
        return _resolved_path(explicit_root, "RESEARCH_SOURCE_ROOT_INVALID")
    return _repository_root(manifest_path)


def _resolved_path(raw: str | Path, code: str = "RESEARCH_PATH_INVALID") -> Path:
    if isinstance(raw, str) and "\x00" in raw:
        raise ResearchProtocolError(code, str(raw))
    try:
        return Path(raw).expanduser().resolve()
    except (TypeError, ValueError, OSError, RuntimeError) as error:
        raise ResearchProtocolError(code, str(raw)) from error


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        raise ResearchProtocolError("RESEARCH_OUTPUT_WRITE_FAILED", str(path)) from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic capability-free replay protocol")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="validate the checked-in static manifest and fixture")
    verify.add_argument("--manifest", required=True)
    verify.add_argument(
        "--root",
        help=(
            "source checkout root for relative strategy assets; required when "
            "verifying a manifest from an installed runtime tree"
        ),
    )
    replay = subparsers.add_parser(
        "run", aliases=["replay"], help="evaluate a RunManifest against quote/target JSON arrays"
    )
    replay.add_argument("--manifest", required=True)
    replay.add_argument("--quotes", required=True)
    replay.add_argument("--targets", required=True)
    replay.add_argument("--output")
    subparsers.add_parser("self-test", help="run the deterministic built-in fixture")
    arguments = parser.parse_args(argv)
    if arguments.command == "verify":
        path = _resolved_path(arguments.manifest)
        document = _load_json(path)
        validate_static_manifest(document, _resolve_source_root(path, arguments.root))
        summary = self_test()
        print(canonical_json({"status": "PASS", "fixture_digest": summary["output_digest"]}))
        return 0
    if arguments.command in {"run", "replay"}:
        manifest_path = _resolved_path(arguments.manifest)
        quotes_path = _resolved_path(arguments.quotes)
        targets_path = _resolved_path(arguments.targets)
        output_path = _resolved_path(arguments.output) if arguments.output else None
        if output_path is not None and output_path in {
            manifest_path,
            quotes_path,
            targets_path,
        }:
            raise ResearchProtocolError(
                "RESEARCH_OUTPUT_PATH_CONFLICT", str(output_path)
            )
        manifest = _load_json(manifest_path)
        quotes = _load_json(quotes_path)
        targets = _load_json(targets_path)
        summary = evaluate_run(manifest, quotes, targets)
        if output_path is not None:
            _write_json(output_path, summary)
        print(canonical_json(summary))
        return 0
    summary = self_test()
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ResearchProtocolError as error:
        print(
            canonical_json(
                {
                    "status": "FAIL",
                    "reason_code": error.code,
                    "failure_reason": error.detail or error.code,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
