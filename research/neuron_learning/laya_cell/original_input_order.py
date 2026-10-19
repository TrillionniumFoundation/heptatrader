"""Reconstruct the ORIGINAL live, insertion-ordered Laya causal state.

The live producer creates a dict in laya_cell.dataset.causal_state with the
specific field order below. The original forecaster encodes that unsorted
dict BEFORE write_once() persists the canonical sorted-key JSON. Reading
the sealed JSON directly and serializing it with laya.common.serialize_state
changes the model's token sequence, despite the stable semantic input digest.

Research-only: do not rewrite original JSON, official prediction, input_id,
original forecaster, native Broker or model artifacts.
"""
from __future__ import annotations

from .integrity import ContractError, digest
from .dataset import encode_rows

ORIGINAL_CAUSAL_STATE_FIELD_ORDER = (
    "instrument", "anchor_ms", "reference_mid", "horizon_sec",
    "columns", "quotes", "quantity",
)


def original_forward_row(row):
    """Build exactly the original live value insertion order, without mutation."""
    if not isinstance(row, dict) or not isinstance(row.get("state"), dict):
        raise ContractError("ORIGINAL_FORWARD_TYPED_ROW")
    state=row["state"]
    if set(state) != set(ORIGINAL_CAUSAL_STATE_FIELD_ORDER) or len(state)!=7:
        raise ContractError("ORIGINAL_FORWARD_CAUSAL_STATE_FIELDS")
    if (row.get("input_id") != digest(state) or
            type(state.get("anchor_ms")) is not int or
            state["anchor_ms"]!=row.get("anchor_ms") or
            state["horizon_sec"]!=300 or
            state["instrument"]!="EUR.USD" or
            state["columns"]!=["offset_ms","bid","ask"] or
            state["quantity"]!="underlying MID return; not execution profit"):
        raise ContractError("ORIGINAL_FORWARD_STATE_PROVENANCE")
    ordered={key:state[key] for key in ORIGINAL_CAUSAL_STATE_FIELD_ORDER}
    if digest(ordered)!=row["input_id"]:
        raise ContractError("ORIGINAL_FORWARD_CANONICAL_INPUT_CHANGED")
    # Preserve object identity/contents of the old source. Never edit row/state.
    return {**row,"state":ordered}


def encode_original_forward_rows(tokenizer, rows, max_len=1024):
    """Original source chronology and the ORIGINAL live text/token ordering."""
    if not isinstance(rows,list) or not rows:
        raise ContractError("ORIGINAL_FORWARD_NONEMPTY_ROWS")
    return encode_rows(tokenizer,[original_forward_row(row) for row in rows],
                       max_len=max_len)
