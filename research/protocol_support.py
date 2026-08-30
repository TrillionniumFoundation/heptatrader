"""Shared, side-effect-free helpers for the deterministic research runner.

The public command remains :mod:`research.run_protocol`; this module only
holds validation, canonicalization and immutable-value primitives so the
runner stays a small orchestration entry point.  It intentionally imports no
broker, session, campaign or runtime modules.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence


_INSTRUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_EVENT_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,63}$")
_DECIMAL_TEXT_RE = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))(?:[eE][+-]?[0-9]+)?$"
)
_CAPABILITY_FIELD_NAMES = {
    "session_token", "session_id", "session_credential", "token",
    "credential", "secret", "broker_credential", "broker_credentials",
    "broker_secret", "broker_token", "broker_access", "direct_broker_access",
    "preview_permit", "execution_permit", "paper_mutation", "live_mutation",
    "paper_authorized", "live_authorized", "paper_authorization",
    "live_authorization", "mutation_attempted", "promotion_grant",
    "promotion_authorization", "mutation_capability", "session_management",
    "capability",
}
_CAPABILITY_FIELD_NORMALIZED = {
    re.sub(r"[^a-z0-9]", "", name.lower()) for name in _CAPABILITY_FIELD_NAMES
}
_CEREMONY_FIELD_NAMES = {
    "campaign", "campaign_id", "campaign_sha256", "campaign_open_request_id",
    "campaign_close_request_id", "campaign_renew_request_id",
    "campaign_repair_request_id", "campaign_finalizer", "finalizer",
    "finalizer_receipt", "final_audit", "final_audit_receipt",
    "final_audit_receipt_sha256", "root_custodian", "custodian",
    "custodian_receipt", "lease", "lease_generation", "watch_lease",
    "watch_generation", "renewal", "renewer", "repair", "repair_receipt",
    "closure_grade", "certification_receipt",
}
_CEREMONY_FIELD_NORMALIZED = {
    re.sub(r"[^a-z0-9]", "", name.lower()) for name in _CEREMONY_FIELD_NAMES
}
# Quote/target records are closed protocol objects.  Keeping these sets in the
# support module ensures package and direct-script runners share the exact same
# field contract.
_QUOTE_FIELDS = frozenset({"ts_ms", "bid", "ask", "instrument"})
_TARGET_FIELDS = frozenset({"ts_ms", "target_position", "instrument"})
_MAX_PROTOCOL_INTEGER = (1 << 63) - 1
_MAX_DECIMAL_ADJUSTED = 1000
_MAX_JSON_DEPTH = 128


class ResearchProtocolError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _validate_json_shape(
    value: Any, *, _depth: int = 0, _seen: set[int] | None = None
) -> None:
    if _seen is None:
        _seen = set()
    if isinstance(value, Mapping):
        if _depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds protocol limit")
        identity = id(value)
        if identity in _seen:
            raise ValueError("cyclic JSON value")
        _seen.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("JSON object keys must be strings")
                key.encode("utf-8")
                _validate_json_shape(child, _depth=_depth + 1, _seen=_seen)
        finally:
            _seen.remove(identity)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if _depth > _MAX_JSON_DEPTH:
            raise ValueError("JSON nesting exceeds protocol limit")
        identity = id(value)
        if identity in _seen:
            raise ValueError("cyclic JSON value")
        _seen.add(identity)
        try:
            for child in value:
                _validate_json_shape(child, _depth=_depth + 1, _seen=_seen)
        finally:
            _seen.remove(identity)
        return
    if isinstance(value, str):
        value.encode("utf-8")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def canonical_json(value: Any) -> str:
    _validate_json_shape(value)
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except RecursionError as error:
        raise ValueError("JSON nesting exceeds protocol limit") from error


def _json_copy(value: Any) -> Any:
    return json.loads(canonical_json(value))


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def decimal_value(value: Any, field: str) -> Decimal:
    if isinstance(value, str) and not _DECIMAL_TEXT_RE.fullmatch(value):
        if value.strip().lower() not in {
            "nan", "+nan", "-nan", "inf", "+inf", "-inf",
            "infinity", "+infinity", "-infinity",
        }:
            raise ResearchProtocolError("RESEARCH_DECIMAL_INVALID", field)
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ResearchProtocolError("RESEARCH_DECIMAL_INVALID", field) from error
    if not parsed.is_finite():
        raise ResearchProtocolError("RESEARCH_DECIMAL_NONFINITE", field)
    if not parsed.is_zero() and abs(parsed.adjusted()) > _MAX_DECIMAL_ADJUSTED:
        raise ResearchProtocolError("RESEARCH_DECIMAL_RANGE", field)
    return parsed


def integer_value(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or value is None:
        raise ResearchProtocolError("RESEARCH_INTEGER_INVALID", field)
    if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+", value):
        raise ResearchProtocolError("RESEARCH_INTEGER_INVALID", field)
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise ResearchProtocolError("RESEARCH_INTEGER_INVALID", field) from error
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ResearchProtocolError("RESEARCH_INTEGER_INVALID", field)
    try:
        result = int(parsed)
    except (OverflowError, ValueError) as error:
        raise ResearchProtocolError("RESEARCH_INTEGER_INVALID", field) from error
    if abs(result) > _MAX_PROTOCOL_INTEGER:
        raise ResearchProtocolError("RESEARCH_INTEGER_RANGE", field)
    if minimum is not None and result < minimum:
        raise ResearchProtocolError("RESEARCH_INTEGER_RANGE", field)
    return result


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(value, Sequence):
        raise ResearchProtocolError("RESEARCH_SEQUENCE_INVALID", field)
    return value


def _instrument(value: Any, field: str = "instrument") -> str:
    if not isinstance(value, str) or not _INSTRUMENT_RE.fullmatch(value):
        raise ResearchProtocolError("RESEARCH_INSTRUMENT_INVALID", field)
    if "/" in value and any(part in {"", ".", ".."} for part in value.split("/")):
        raise ResearchProtocolError("RESEARCH_INSTRUMENT_INVALID", field)
    return value


def _reject_capability_fields(
    value: Any, path: str = "manifest", _seen: set[int] | None = None,
    _depth: int = 0,
) -> None:
    if _seen is None:
        _seen = set()
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray)):
        if _depth > _MAX_JSON_DEPTH:
            raise ResearchProtocolError("RESEARCH_INPUT_DEPTH_EXCEEDED", path)
        identity = id(value)
        if identity in _seen:
            raise ResearchProtocolError("RESEARCH_INPUT_CYCLE", path)
        _seen.add(identity)
        try:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = re.sub(r"[^a-z0-9]", "", key.lower()) if isinstance(key, str) else ""
                    if normalized in _CAPABILITY_FIELD_NORMALIZED:
                        raise ResearchProtocolError("RESEARCH_CAPABILITY_FORBIDDEN", f"{path}.{key}")
                    _reject_capability_fields(child, f"{path}.{key}", _seen, _depth + 1)
            else:
                for index, child in enumerate(value):
                    _reject_capability_fields(child, f"{path}[{index}]", _seen, _depth + 1)
        finally:
            _seen.remove(identity)


def _reject_ceremony_fields(
    value: Any, path: str = "manifest", _seen: set[int] | None = None,
    _depth: int = 0,
) -> None:
    if _seen is None:
        _seen = set()
    if isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray)):
        if _depth > _MAX_JSON_DEPTH:
            raise ResearchProtocolError("RESEARCH_INPUT_DEPTH_EXCEEDED", path)
        identity = id(value)
        if identity in _seen:
            raise ResearchProtocolError("RESEARCH_INPUT_CYCLE", path)
        _seen.add(identity)
        try:
            if isinstance(value, Mapping):
                for key, child in value.items():
                    normalized = re.sub(r"[^a-z0-9]", "", key.lower()) if isinstance(key, str) else ""
                    if normalized in _CEREMONY_FIELD_NORMALIZED:
                        raise ResearchProtocolError("RESEARCH_CEREMONY_FORBIDDEN", f"{path}.{key}")
                    _reject_ceremony_fields(child, f"{path}.{key}", _seen, _depth + 1)
            else:
                for index, child in enumerate(value):
                    _reject_ceremony_fields(child, f"{path}[{index}]", _seen, _depth + 1)
        finally:
            _seen.remove(identity)


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(
        character in "0123456789abcdef" for character in value[7:]
    )


def sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    except (OSError, UnicodeError) as error:
        raise ResearchProtocolError("RESEARCH_STRATEGY_INPUT_MISSING", str(path)) from error
