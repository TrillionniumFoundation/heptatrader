"""Offline research forecasters; no broker, production, or registration imports.

Chronos uses the official Chronos2Pipeline.fit(finetune_mode="lora") API.
Timestral has an inference adapter only. A separate evaluator fits the common
classification head to ForecastBatch.features, using chronological partitions.

Sources checked 2026-11-02:
https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos2/pipeline.py
https://github.com/mistralai/timestral/blob/main/src/timestral/forecaster.py

Inputs are already prepared, causal, regularly sampled price sequences. This
module never fills gaps, invents future observations, or joins different rows.
Run each experiment in its own process: thread limits and RNG seeds are process
settings. All model loads require local files and an immutable source revision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import random
import re
import time
from typing import Any, Mapping, Sequence
import warnings

import numpy as np


MODEL_IDS = {
    "chronos2_small": "autogluon/chronos-2-small",
    "timestral_5m": "mistralai/Timestral-5M",
}
FEATURE_NAMES = (
    "terminal_return_q10", "terminal_return_q50", "terminal_return_q90",
    "mean_median_return", "min_median_return", "max_median_return",
    "mean_q90_q10_width",
)
QUANTILE_LEVELS = (0.1, 0.5, 0.9)


class ForecastAdapterError(RuntimeError):
    """A research arm cannot proceed with the requested, auditable semantics."""


@dataclass(frozen=True)
class ForecastModelConfig:
    model_kind: str
    model_path: str
    revision: str
    device: str = "cpu"
    threads: int = 2
    interop_threads: int = 1
    batch_size: int = 4
    seed: int = 1729
    context_length: int | None = None
    offline_only: bool = True
    expected_files_sha256: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.model_kind not in MODEL_IDS:
            raise ValueError("UNKNOWN_MODEL_KIND")
        if not re.fullmatch(r"[0-9a-f]{40}", self.revision):
            raise ValueError("IMMUTABLE_40_HEX_SOURCE_REVISION_REQUIRED")
        if not self.offline_only:
            raise ValueError("OFFLINE_LOADING_REQUIRED")
        if not re.fullmatch(r"cpu|cuda(?::[0-9]+)?", self.device):
            raise ValueError("UNSUPPORTED_DEVICE")
        for name, maximum in (("threads", 32), ("interop_threads", 4), ("batch_size", 64)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"INVALID_{name.upper()}")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("INVALID_SEED")
        if self.context_length is not None and (
            type(self.context_length) is not int or not 1 <= self.context_length <= 32768
        ):
            raise ValueError("INVALID_CONTEXT_LENGTH")


@dataclass(frozen=True)
class LoRAFitConfig:
    output_dir: str
    context_length: int = 24
    min_past: int = 6
    num_steps: int = 16
    batch_size: int = 2
    learning_rate: float = 1e-5
    rank: int = 4
    alpha: int = 8
    max_trainable_fraction: float = 0.1

    def __post_init__(self) -> None:
        for name, maximum in (
            ("context_length", 8192), ("min_past", 8192), ("num_steps", 4096),
            ("batch_size", 64), ("rank", 32), ("alpha", 128),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"INVALID_LORA_{name.upper()}")
        if self.min_past > self.context_length:
            raise ValueError("MIN_PAST_EXCEEDS_CONTEXT")
        if not math.isfinite(self.learning_rate) or not 0 < self.learning_rate <= 0.01:
            raise ValueError("INVALID_LORA_LEARNING_RATE")
        if not 0 < self.max_trainable_fraction <= 0.1:
            raise ValueError("INVALID_TRAINABLE_FRACTION")
        if not self.output_dir:
            raise ValueError("EXPLICIT_OUTPUT_DIRECTORY_REQUIRED")


@dataclass(frozen=True)
class ForecastBatch:
    quantiles: np.ndarray  # [series, quantile, horizon], in original price units
    quantile_levels: tuple[float, ...]
    features: np.ndarray  # [series, feature], scale-free relative price changes
    feature_names: tuple[str, ...]
    metadata: dict[str, Any]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file_sha256(path: Path) -> str:
    def identity(st: os.stat_result) -> tuple[int, ...]:
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = identity(os.fstat(stream.fileno()))
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
        if before != identity(os.fstat(stream.fileno())):
            raise ForecastAdapterError("MODEL_FILE_CHANGED_DURING_READ")
    if before != identity(path.stat()):
        raise ForecastAdapterError("MODEL_FILE_REPLACED_DURING_READ")
    return digest.hexdigest()


def _artifact_inventory(config: ForecastModelConfig) -> dict[str, Any]:
    root = Path(config.model_path).expanduser().absolute()
    if not root.is_dir():
        raise ForecastAdapterError("LOCAL_MODEL_DIRECTORY_REQUIRED")
    revision_named = re.fullmatch(r"[0-9a-f]{40}", root.name) is not None
    if (root.parent.name == "snapshots" or revision_named) and root.name != config.revision:
        raise ForecastAdapterError("HF_SNAPSHOT_REVISION_MISMATCH")
    names = {"config.json"}
    if (root / "model.safetensors").is_file():
        names.add("model.safetensors")
    elif (root / "model.safetensors.index.json").is_file():
        index_path = root / "model.safetensors.index.json"
        index = json.loads(index_path.read_text())
        weights = index.get("weight_map", {})
        if not isinstance(weights, dict) or not weights:
            raise ForecastAdapterError("EMPTY_SAFETENSORS_INDEX")
        names.add(index_path.name)
        names.update(weights.values())
    else:
        raise ForecastAdapterError("SAFETENSORS_CHECKPOINT_REQUIRED")
    for name in names:
        if not isinstance(name, str) or Path(name).name != name or name in {".", ".."}:
            raise ForecastAdapterError("INVALID_CHECKPOINT_FILE_NAME")
        if name not in {"config.json", "model.safetensors.index.json"} and not name.endswith(".safetensors"):
            raise ForecastAdapterError("NON_SAFETENSORS_WEIGHT_FILE")
    files = {name: _file_sha256(root / name) for name in sorted(names)}
    if config.expected_files_sha256 is not None and dict(config.expected_files_sha256) != files:
        raise ForecastAdapterError("MODEL_FILE_HASH_MISMATCH")
    return {
        "source_path": str(root), "source_revision": config.revision,
        "revision_binding": "revision_directory" if revision_named else "operator_supplied",
        "files_sha256": files,
        "artifact_sha256": hashlib.sha256(_canonical(files)).hexdigest(),
    }


def _numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value)


def _parameter_audit(model: Any, *, trainable: bool | None = None) -> dict[str, Any]:
    digest = hashlib.sha256()
    count, names = 0, []
    for name, parameter in sorted(model.named_parameters(), key=lambda item: item[0]):
        if trainable is not None and bool(parameter.requires_grad) != trainable:
            continue
        values = np.ascontiguousarray(_numpy(parameter))
        if not np.isfinite(values).all():
            raise ForecastAdapterError("NONFINITE_MODEL_PARAMETER")
        digest.update(_canonical([name, str(values.dtype), list(values.shape)]))
        digest.update(memoryview(values).cast("B"))
        count += int(values.size)
        names.append(name)
    return {"sha256": digest.hexdigest(), "parameters": count, "names": names}


def _freeze(model: Any) -> None:
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    model.eval()


def _validate_series(values: Sequence[Any], *, max_context: int | None = None) -> list[np.ndarray]:
    if not isinstance(values, (list, tuple)) or not values or len(values) > 10000:
        raise ForecastAdapterError("NONEMPTY_BOUNDED_SERIES_LIST_REQUIRED")
    result = []
    for index, item in enumerate(values):
        array = np.asarray(item, dtype=np.float32)
        if array.ndim != 1 or not 1 <= array.size <= 131072:
            raise ForecastAdapterError(f"INVALID_SERIES_SHAPE:{index}")
        if not np.isfinite(array).all() or (array <= 0).any():
            raise ForecastAdapterError(f"FINITE_POSITIVE_PRICES_REQUIRED:{index}")
        if max_context is not None:
            array = array[-max_context:]
        result.append(np.ascontiguousarray(array))
    return result


def _series_sha256(series: Sequence[np.ndarray]) -> str:
    digest = hashlib.sha256()
    for item in series:
        digest.update(_canonical([str(item.dtype), list(item.shape)]))
        digest.update(memoryview(item).cast("B"))
    return digest.hexdigest()


def forecast_features(quantiles: np.ndarray, last_prices: np.ndarray) -> np.ndarray:
    """Fixed feature map; no label-dependent thresholds or fitted preprocessing."""
    q = np.asarray(quantiles, dtype=np.float64)
    last = np.asarray(last_prices, dtype=np.float64)
    if q.ndim != 3 or q.shape[1] != 3 or q.shape[2] < 1 or last.shape != (q.shape[0],):
        raise ForecastAdapterError("INVALID_FORECAST_SHAPE")
    if not np.isfinite(q).all() or not np.isfinite(last).all() or (last <= 0).any():
        raise ForecastAdapterError("NONFINITE_FORECAST_OR_INVALID_ANCHOR")
    relative = q / last[:, None, None] - 1.0
    median = relative[:, 1, :]
    return np.column_stack((
        relative[:, 0, -1], relative[:, 1, -1], relative[:, 2, -1],
        median.mean(axis=1), median.min(axis=1), median.max(axis=1),
        (relative[:, 2, :] - relative[:, 0, :]).mean(axis=1),
    ))


def _horizon(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 1024:
        raise ValueError("HORIZON_MUST_BE_IN_1_1024")
    return value


def _dependency(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ForecastAdapterError(f"DEPENDENCY_UNAVAILABLE:{name}") from exc


def _versions() -> dict[str, str]:
    result = {}
    for package in ("numpy", "torch", "transformers", "peft", "chronos-forecasting", "timestral"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


class _ForecastAdapter:
    def __init__(self, config: ForecastModelConfig):
        self.config = config
        self._backend: Any = None
        self._torch: Any = None
        self._receipt: dict[str, Any] | None = None
        self._trained = False

    def _prepare_runtime(self) -> None:
        for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
            os.environ[key] = "1"
        for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ[key] = str(self.config.threads)
        torch = _dependency("torch")
        torch.set_num_threads(self.config.threads)
        if torch.get_num_interop_threads() != self.config.interop_threads:
            try:
                torch.set_num_interop_threads(self.config.interop_threads)
            except RuntimeError as exc:
                raise ForecastAdapterError("INTEROP_THREAD_LIMIT_REQUIRES_FRESH_PROCESS") from exc
        if self.config.device != "cpu" and not torch.cuda.is_available():
            raise ForecastAdapterError("REQUESTED_ACCELERATOR_UNAVAILABLE")
        random.seed(self.config.seed)
        np.random.seed(self.config.seed)
        torch.manual_seed(self.config.seed)
        torch.use_deterministic_algorithms(True)
        self._torch = torch

    def _sync(self) -> None:
        if self.config.device != "cpu":
            self._torch.cuda.synchronize(self.config.device)

    def _load_backend(self, source_path: str) -> Any:
        raise NotImplementedError

    def load(self) -> dict[str, Any]:
        if self._receipt is not None:
            return dict(self._receipt)
        start = time.perf_counter()
        before = _artifact_inventory(self.config)
        self._prepare_runtime()
        load_start = time.perf_counter()
        backend = self._load_backend(before["source_path"])
        self._sync()
        load_seconds = time.perf_counter() - load_start
        after = _artifact_inventory(self.config)
        if before != after:
            raise ForecastAdapterError("MODEL_ARTIFACT_CHANGED_WHILE_LOADING")
        _freeze(backend.model)
        audit = _parameter_audit(backend.model)
        if audit["parameters"] == 0:
            raise ForecastAdapterError("EMPTY_MODEL")
        code = {}
        for obj in (type(backend), type(backend.model)):
            filename = inspect.getsourcefile(obj)
            if filename and Path(filename).is_file():
                code[f"{obj.__module__}.{obj.__name__}"] = _file_sha256(Path(filename))
        receipt = {
            "schema": "research.forecast_model.v1", "model_kind": self.config.model_kind,
            "model_id": MODEL_IDS[self.config.model_kind], **before,
            "device": self.config.device, "dtype": "float32", "seed": self.config.seed,
            "threads": self.config.threads, "interop_threads": self.config.interop_threads,
            "batch_size": self.config.batch_size, "context_length_cap": self.config.context_length,
            "offline_only": True, "training_mode": "frozen", "trainable_parameters": 0,
            "total_parameters": audit["parameters"], "parameters_sha256": audit["sha256"],
            "versions": _versions(), "implementation_sha256": code,
            "load_seconds": load_seconds, "load_total_seconds": time.perf_counter() - start,
        }
        self._backend, self._receipt = backend, receipt
        return dict(receipt)

    def _predict_chunk(self, contexts: list[np.ndarray], horizon: int) -> np.ndarray:
        raise NotImplementedError

    def predict(self, contexts: Sequence[Any], horizon_steps: int) -> ForecastBatch:
        horizon = _horizon(horizon_steps)
        series = _validate_series(contexts)
        original_lengths = [int(item.size) for item in series]
        if self.config.context_length is not None:
            series = [item[-self.config.context_length:] for item in series]
        self.load()
        if any(parameter.requires_grad for parameter in self._backend.model.parameters()):
            raise ForecastAdapterError("FORECASTER_MUST_BE_FROZEN_DURING_PREDICTION")
        start = time.perf_counter()
        batches = []
        with self._torch.inference_mode():
            for first in range(0, len(series), self.config.batch_size):
                part = series[first:first + self.config.batch_size]
                q = self._predict_chunk(part, horizon)
                if q.shape != (len(part), 3, horizon):
                    raise ForecastAdapterError(f"BACKEND_FORECAST_SHAPE_MISMATCH:{q.shape}")
                batches.append(q)
        self._sync()
        forecast_seconds = time.perf_counter() - start
        quantiles = np.concatenate(batches, axis=0)
        features = forecast_features(quantiles, np.asarray([item[-1] for item in series]))
        metadata = {
            "model": dict(self._receipt or {}), "input_sha256": _series_sha256(series),
            "series_count": len(series), "original_context_lengths": original_lengths,
            "effective_context_lengths": [int(item.size) for item in series],
            "horizon_steps": horizon, "cross_learning": False,
            "forecast_seconds": forecast_seconds, "total_seconds": time.perf_counter() - start,
            "quantile_crossing_cells": int(np.count_nonzero(np.diff(quantiles, axis=1) < 0)),
            "classification_calibration": "external_train_only_head_required",
        }
        return ForecastBatch(quantiles, QUANTILE_LEVELS, features, FEATURE_NAMES, metadata)

    def fit_lora(self, train_series: Sequence[Any], prediction_length: int, options: LoRAFitConfig) -> dict[str, Any]:
        raise ForecastAdapterError("PUBLIC_LORA_TRAINING_UNAVAILABLE_FOR_THIS_MODEL")

    def restore_lora(self, training_receipt: Mapping[str, Any] | str | Path, *, checkpoint_dir: str | None = None) -> dict[str, Any]:
        raise ForecastAdapterError("PUBLIC_LORA_TRAINING_UNAVAILABLE_FOR_THIS_MODEL")


def _lora_snapshot(model: Any, max_fraction: float) -> dict[str, Any]:
    trained = _parameter_audit(model, trainable=True)
    if not trained["parameters"]:
        raise ForecastAdapterError("NO_TRAINABLE_LORA_PARAMETERS")
    if any("lora_A" not in name and "lora_B" not in name for name in trained["names"]):
        raise ForecastAdapterError("NON_LORA_PARAMETERS_ARE_TRAINABLE")
    frozen = _parameter_audit(model, trainable=False)
    total = trained["parameters"] + frozen["parameters"]
    if trained["parameters"] / total > max_fraction:
        raise ForecastAdapterError("LORA_TRAINABLE_FRACTION_EXCEEDED")
    return {"all": {"parameters": total}, "trainable": trained, "frozen": frozen}


def _audit_callback(callback_base: type, max_fraction: float, expected_steps: int) -> Any:
    class AuditCallback(callback_base):
        def __init__(self) -> None:
            self.before: dict[str, Any] | None = None
            self.after: dict[str, Any] | None = None
            self.steps = 0
            self.training_seconds: float | None = None
            self.losses: list[dict[str, float]] = []

        def on_train_begin(self, args: Any, state: Any, control: Any, model: Any = None, **kwargs: Any) -> None:
            self.before = _lora_snapshot(model, max_fraction)
            self._started = time.perf_counter()

        def on_log(self, args: Any, state: Any, control: Any, logs: Any = None, **kwargs: Any) -> None:
            if logs and "loss" in logs:
                loss = float(logs["loss"])
                if not math.isfinite(loss):
                    raise ForecastAdapterError("NONFINITE_TRAINING_LOSS")
                self.losses.append({"step": int(state.global_step), "loss": loss})

        def on_train_end(self, args: Any, state: Any, control: Any, model: Any = None, **kwargs: Any) -> None:
            self.training_seconds = time.perf_counter() - self._started
            self.steps = int(state.global_step)
            self.after = _lora_snapshot(model, max_fraction)
            if self.before is None or self.steps != expected_steps:
                raise ForecastAdapterError("LORA_EXPECTED_STEPS_NOT_COMPLETED")
            if self.before["frozen"]["sha256"] != self.after["frozen"]["sha256"]:
                raise ForecastAdapterError("FROZEN_PARAMETERS_CHANGED")
            if self.before["trainable"]["sha256"] == self.after["trainable"]["sha256"]:
                raise ForecastAdapterError("LORA_PARAMETERS_DID_NOT_CHANGE")

    return AuditCallback()


class Chronos2SmallAdapter(_ForecastAdapter):
    def _load_backend(self, source_path: str) -> Any:
        pipeline = _dependency("chronos").Chronos2Pipeline
        return pipeline.from_pretrained(
            source_path, device_map=self.config.device, torch_dtype=self._torch.float32,
            local_files_only=True, trust_remote_code=False,
        )

    def _predict_chunk(self, contexts: list[np.ndarray], horizon: int) -> np.ndarray:
        kwargs: dict[str, Any] = {
            "prediction_length": horizon, "quantile_levels": list(QUANTILE_LEVELS),
            "batch_size": self.config.batch_size, "cross_learning": False,
            "limit_prediction_length": True,
        }
        if self.config.context_length is not None:
            kwargs["context_length"] = self.config.context_length
        predictions, _ = self._backend.predict_quantiles(contexts, **kwargs)
        if len(predictions) != len(contexts):
            raise ForecastAdapterError("CHRONOS_SERIES_COUNT_MISMATCH")
        converted = []
        for value in predictions:
            array = _numpy(value)
            if array.shape != (1, horizon, 3):
                raise ForecastAdapterError(f"CHRONOS_QUANTILE_SHAPE_MISMATCH:{array.shape}")
            converted.append(array[0].T)
        return np.stack(converted)

    def fit_lora(self, train_series: Sequence[Any], prediction_length: int, options: LoRAFitConfig) -> dict[str, Any]:
        horizon = _horizon(prediction_length)
        series = _validate_series(train_series)
        short = [index for index, item in enumerate(series) if item.size < options.min_past + horizon]
        if short:
            raise ForecastAdapterError(
                f"SHORT_TRAINING_SERIES:need={options.min_past + horizon},indices={short[:20]}"
            )
        if self._trained:
            raise ForecastAdapterError("NEW_ADAPTER_REQUIRED_FOR_ANOTHER_FIT")
        # Check both imports and the exact availability predicate used upstream.
        _dependency("peft")
        pipeline_module = _dependency("chronos.chronos2.pipeline")
        availability = getattr(pipeline_module, "is_peft_available", None)
        if not callable(availability) or not availability():
            raise ForecastAdapterError("PEFT_UNAVAILABLE_NO_FULL_TRAINING_FALLBACK")
        callback_base = _dependency("transformers").TrainerCallback
        self.load()
        output_dir = Path(options.output_dir).expanduser().absolute()
        model_root = Path(self.config.model_path).expanduser().resolve()
        if output_dir.resolve() == model_root or model_root in output_dir.resolve().parents:
            raise ForecastAdapterError("TRAINING_OUTPUT_MUST_NOT_MODIFY_SOURCE_CHECKPOINT")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ForecastAdapterError("TRAINING_OUTPUT_DIRECTORY_MUST_BE_EMPTY")
        output_dir.mkdir(parents=True, exist_ok=True)
        callback = _audit_callback(callback_base, options.max_trainable_fraction, options.num_steps)
        original = _parameter_audit(self._backend.model)
        random.seed(self.config.seed)
        np.random.seed(self.config.seed)
        self._torch.manual_seed(self.config.seed)
        started = time.perf_counter()
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message=".*Falling back to.*full.*")
            trained = self._backend.fit(
                series, prediction_length=horizon, finetune_mode="lora",
                lora_config={
                    "r": options.rank, "lora_alpha": options.alpha, "lora_dropout": 0.0,
                    "bias": "none", "target_modules": [
                        "self_attention.q", "self_attention.k", "self_attention.v", "self_attention.o",
                        "output_patch_embedding.output_layer",
                    ],
                },
                context_length=options.context_length, min_past=options.min_past,
                learning_rate=options.learning_rate, num_steps=options.num_steps,
                batch_size=options.batch_size, output_dir=output_dir,
                finetuned_ckpt_name="lora-checkpoint", callbacks=[callback],
                disable_data_parallel=True, remove_printer_callback=True,
                optim="adamw_torch", bf16=False, fp16=False, tf32=False,
                use_cpu=self.config.device == "cpu", seed=self.config.seed, data_seed=self.config.seed,
                dataloader_num_workers=0, dataloader_pin_memory=False, report_to="none",
                logging_steps=1, logging_nan_inf_filter=False, disable_tqdm=True,
            )
        self._sync()
        fit_seconds = time.perf_counter() - started
        if callback.before is None or callback.after is None:
            raise ForecastAdapterError("LORA_TRAINING_AUDIT_CALLBACK_NOT_EXECUTED")
        if _parameter_audit(self._backend.model)["sha256"] != original["sha256"]:
            raise ForecastAdapterError("SOURCE_MODEL_CHANGED_DURING_FIT")
        _freeze(trained.model)
        final_audit = _parameter_audit(trained.model)
        checkpoint = output_dir / "lora-checkpoint"
        if not checkpoint.is_dir() or not list(checkpoint.glob("*.safetensors")):
            raise ForecastAdapterError("LORA_CHECKPOINT_NOT_EXPORTED")
        checkpoint_files = {p.name: _file_sha256(p) for p in sorted(checkpoint.iterdir()) if p.is_file()}
        receipt = {
            "schema": "research.forecast_lora.v1", "model_id": MODEL_IDS[self.config.model_kind],
            "source_revision": self.config.revision, "source_artifact_sha256": self._receipt["artifact_sha256"],
            "source_implementation_sha256": self._receipt["implementation_sha256"],
            "source_parameters_sha256": original["sha256"],
            "options": asdict(options), "seed": self.config.seed, "device": self.config.device,
            "series_count": len(series), "series_lengths": [int(item.size) for item in series],
            "training_input_sha256": _series_sha256(series), "prediction_length": horizon,
            "completed_steps": callback.steps, "training_seconds": callback.training_seconds,
            "fit_total_seconds": fit_seconds, "losses": callback.losses,
            "trainable_parameters_during_fit": callback.before["trainable"]["parameters"],
            "trainable_parameter_names": callback.before["trainable"]["names"],
            "trainable_sha256_before": callback.before["trainable"]["sha256"],
            "trainable_sha256_after": callback.after["trainable"]["sha256"],
            "frozen_sha256_before": callback.before["frozen"]["sha256"],
            "frozen_sha256_after": callback.after["frozen"]["sha256"],
            "parameters_sha256": final_audit["sha256"], "source_model_unchanged": True,
            "inference_trainable_parameters": 0, "checkpoint_path": str(checkpoint),
            "checkpoint_files_sha256": checkpoint_files,
        }
        (output_dir / "training-receipt.json").write_bytes(_canonical(receipt) + b"\n")
        self._backend = trained
        self._trained = True
        self._receipt = {
            **(self._receipt or {}), "training_mode": "lora_then_frozen",
            "parameters_sha256": final_audit["sha256"], "total_parameters": final_audit["parameters"],
            "training_receipt_sha256": hashlib.sha256(_canonical(receipt)).hexdigest(),
        }
        return receipt

    def restore_lora(self, training_receipt: Mapping[str, Any] | str | Path, *, checkpoint_dir: str | None = None) -> dict[str, Any]:
        """Restore verified PEFT weights over the pinned base, without retraining.

        The evaluator pins this receipt in its model-state artifact. A path
        override only relocates identical content; it cannot change any hash.
        """
        if self._trained:
            raise ForecastAdapterError("NEW_ADAPTER_REQUIRED_FOR_ANOTHER_RESTORE")
        if isinstance(training_receipt, (str, Path)):
            receipt = json.loads(Path(training_receipt).read_text())
        else:
            receipt = dict(training_receipt)
        self.load()
        if (
            receipt.get("schema") != "research.forecast_lora.v1"
            or receipt.get("model_id") != MODEL_IDS[self.config.model_kind]
            or receipt.get("source_revision") != self.config.revision
            or receipt.get("source_artifact_sha256") != self._receipt["artifact_sha256"]
            or receipt.get("source_parameters_sha256") != self._receipt["parameters_sha256"]
            or receipt.get("source_implementation_sha256") != self._receipt["implementation_sha256"]
        ):
            raise ForecastAdapterError("LORA_SOURCE_BINDING_MISMATCH")
        expected_files = receipt.get("checkpoint_files_sha256")
        if not isinstance(expected_files, dict) or not expected_files:
            raise ForecastAdapterError("LORA_CHECKPOINT_HASHES_REQUIRED")
        for name, digest in expected_files.items():
            if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ForecastAdapterError("INVALID_LORA_CHECKPOINT_MANIFEST")
        checkpoint = Path(checkpoint_dir or receipt["checkpoint_path"]).expanduser().absolute()

        def inventory() -> dict[str, str]:
            if not checkpoint.is_dir():
                raise ForecastAdapterError("LOCAL_LORA_CHECKPOINT_REQUIRED")
            return {p.name: _file_sha256(p) for p in sorted(checkpoint.iterdir()) if p.is_file()}

        if inventory() != expected_files:
            raise ForecastAdapterError("LORA_CHECKPOINT_HASH_MISMATCH")
        if "adapter_config.json" not in expected_files or "adapter_model.safetensors" not in expected_files:
            raise ForecastAdapterError("PEFT_SAFETENSORS_ADAPTER_REQUIRED")
        peft = _dependency("peft")
        start = time.perf_counter()
        # PEFT modifies its base in place; keep the current verified forecaster
        # intact until the candidate restore passes all checks.
        restored = peft.PeftModel.from_pretrained(
            copy.deepcopy(self._backend.model), str(checkpoint), is_trainable=False,
            local_files_only=True, torch_device=self.config.device, autocast_adapter_dtype=False,
        )
        _freeze(restored)
        self._sync()
        actual = _parameter_audit(restored)
        if inventory() != expected_files:
            raise ForecastAdapterError("LORA_CHECKPOINT_CHANGED_WHILE_LOADING")
        if actual["sha256"] != receipt.get("parameters_sha256"):
            raise ForecastAdapterError("RESTORED_LORA_PARAMETERS_MISMATCH")
        self._backend = type(self._backend)(model=restored)
        self._trained = True
        receipt_digest = hashlib.sha256(_canonical(receipt)).hexdigest()
        self._receipt = {
            **(self._receipt or {}), "training_mode": "restored_lora_frozen",
            "parameters_sha256": actual["sha256"], "total_parameters": actual["parameters"],
            "training_receipt_sha256": receipt_digest,
        }
        return {
            "schema": "research.forecast_lora_restore.v1", "training_receipt_sha256": receipt_digest,
            "parameters_sha256": actual["sha256"], "trainable_parameters": 0,
            "restore_seconds": time.perf_counter() - start, "checkpoint_path": str(checkpoint),
        }


class Timestral5MAdapter(_ForecastAdapter):
    def _load_backend(self, source_path: str) -> Any:
        forecaster = _dependency("timestral").TimestralForecaster
        return forecaster.from_pretrained(
            source_path, device=self.config.device, revision=self.config.revision,
            local_files_only=True, max_context=self.config.context_length,
        )

    def _predict_chunk(self, contexts: list[np.ndarray], horizon: int) -> np.ndarray:
        # Group equal lengths so no invented observations or batch-dependent
        # padding are introduced by this adapter. Restore the input row order.
        result = np.empty((len(contexts), 3, horizon), dtype=np.float64)
        for length in sorted({item.size for item in contexts}):
            indices = [i for i, item in enumerate(contexts) if item.size == length]
            array = np.stack([contexts[i] for i in indices])[:, None, :]
            forecast = self._backend.forecast(array, prediction_length=horizon)
            raw = _numpy(forecast.quantiles)
            levels = np.asarray(forecast.quantile_levels, dtype=np.float64)
            if raw.shape != (len(indices), 1, horizon, len(levels)):
                raise ForecastAdapterError(f"TIMESTRAL_QUANTILE_SHAPE_MISMATCH:{raw.shape}")
            selected = []
            for level in QUANTILE_LEVELS:
                matches = np.flatnonzero(np.isclose(levels, level, rtol=0, atol=1e-8))
                if matches.size != 1:
                    raise ForecastAdapterError("TIMESTRAL_REQUIRED_QUANTILE_MISSING")
                selected.append(int(matches[0]))
            result[indices] = raw[:, 0][:, :, selected].transpose(0, 2, 1)
        return result


def create_forecast_adapter(config: ForecastModelConfig) -> _ForecastAdapter:
    return {"chronos2_small": Chronos2SmallAdapter, "timestral_5m": Timestral5MAdapter}[config.model_kind](config)
