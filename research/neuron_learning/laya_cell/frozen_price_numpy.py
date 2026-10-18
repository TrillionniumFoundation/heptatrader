"""CPU inference for the immutable 24 -> 32 tanh -> 7 research model."""
from __future__ import annotations
import hashlib
from pathlib import Path
import numpy as np
from .integrity import ContractError
from .price_year_gpu import FEATURE_NAMES

class FrozenPriceModel:
    def __init__(self, weights, expected_sha256, normalizer):
        from safetensors.numpy import load
        raw = Path(weights).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ContractError("FROZEN_PRICE_WEIGHTS_CHANGED")
        self.weights_sha256 = expected_sha256
        self.mean = np.asarray(normalizer["mean"], dtype=np.float32)
        self.std = np.asarray(normalizer["std"], dtype=np.float32)
        if (normalizer.get("train_only") is not True or normalizer.get("clip") != 6
            or self.mean.shape != (24,) or self.std.shape != (24,)
            or not np.isfinite(self.mean).all() or not np.isfinite(self.std).all()
            or (self.std < 1e-3).any()):
            raise ContractError("FROZEN_PRICE_NORMALIZER")
        self.tensors = load(raw)
        expected = {"0.weight": (32,24), "0.bias": (32,),
                    "2.weight": (7,32), "2.bias": (7,)}
        if set(self.tensors) != set(expected):
            raise ContractError("FROZEN_PRICE_TENSORS")
        for key, shape in expected.items():
            value = self.tensors[key]
            if value.shape != shape or value.dtype != np.float32 or not np.isfinite(value).all():
                raise ContractError("FROZEN_PRICE_TENSOR_SHAPE")
            value.setflags(write=False)

    def predict(self, features):
        x = np.asarray(features, dtype=np.float32)
        if x.ndim == 1: x = x.reshape(1,-1)
        if x.ndim != 2 or x.shape[1] != len(FEATURE_NAMES) or not np.isfinite(x).all():
            raise ContractError("FROZEN_PRICE_FEATURES")
        scaled = (x-self.mean)/self.std
        clipped = np.count_nonzero(np.abs(scaled)>6, axis=1)
        w = self.tensors
        hidden = np.tanh(np.clip(scaled,-6,6) @ w["0.weight"].T + w["0.bias"])
        logits = hidden @ w["2.weight"].T + w["2.bias"]
        exponent = np.exp(logits-logits.max(axis=1,keepdims=True))
        probs = exponent/exponent.sum(axis=1,keepdims=True)
        if not np.isfinite(probs).all():
            raise ContractError("FROZEN_PRICE_NONFINITE_OUTPUT")
        return probs, clipped
