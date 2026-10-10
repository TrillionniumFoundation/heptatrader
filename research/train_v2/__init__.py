"""HeptaTrader Train V2: research-only causal data and event contracts.

No broker transport, model weights, or execution/promotion operations.
"""
from .contracts import (
    ContractError, compile_dataset, compile_example, feature_spec_id,
    validate_spec, validate_observation,
)

__all__ = [
    "ContractError", "compile_dataset", "compile_example",
    "feature_spec_id", "validate_spec", "validate_observation",
]
