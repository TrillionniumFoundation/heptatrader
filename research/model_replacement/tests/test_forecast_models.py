"""Fake-backend contract tests, not model-performance or real-training evidence."""
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from research.model_replacement import forecast_models as fm


class FakeParameter:
    def __init__(self, values, trainable=True):
        self.values = np.asarray(values, dtype=np.float32).copy()
        self.requires_grad = trainable

    def requires_grad_(self, value):
        self.requires_grad = value
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class FakeModel:
    def __init__(self, lora=False):
        self.weights = {"base.weight": FakeParameter(np.arange(1000))}
        self.training = True
        if lora:
            self.weights["base.weight"].requires_grad_(False)
            self.weights["layer.lora_A.default.weight"] = FakeParameter(np.ones((2, 2)))
            self.weights["layer.lora_B.default.weight"] = FakeParameter(np.zeros((2, 2)))

    def named_parameters(self):
        return self.weights.items()

    def parameters(self):
        return iter(self.weights.values())

    def eval(self):
        self.training = False
        return self


class FakeTorch:
    float32 = "float32"
    cuda = SimpleNamespace(is_available=lambda: False)

    def __init__(self):
        self.threads = 8
        self.interop_threads = 4

    def set_num_threads(self, value):
        self.threads = value

    def get_num_interop_threads(self):
        return self.interop_threads

    def set_num_interop_threads(self, value):
        self.interop_threads = value

    def manual_seed(self, value):
        self.seed = value

    def use_deterministic_algorithms(self, value):
        self.deterministic = value

    def inference_mode(self):
        return nullcontext()


class FakeChronos:
    def __init__(self, model=None):
        self.model = model if model is not None else FakeModel()
        self.predict_calls = []
        self.fit_calls = []
        self.steps_executed = 0
        self.failure = None

    def predict_quantiles(self, contexts, **kwargs):
        self.predict_calls.append((contexts, kwargs))
        horizon = kwargs["prediction_length"]
        offset = 0.0
        if "layer.lora_B.default.weight" in self.model.weights:
            offset = self.model.weights["layer.lora_B.default.weight"].values.mean()
        return [x[-1] + np.arange(1, horizon + 1)[None, :, None] + np.array([-1, 0, 1])[None, None, :] + offset for x in contexts], [None for x in contexts]

    def fit(self, series, **kwargs):
        self.fit_calls.append(kwargs)
        trained = FakeChronos()
        trained.model = FakeModel(lora=True)
        callback = kwargs["callbacks"][0]
        if self.failure == "full":
            trained.model.weights["base.weight"].requires_grad_(True)
        state = SimpleNamespace(global_step=0)
        callback.on_train_begin(None, state, None, model=trained.model)
        self.steps_executed += kwargs["num_steps"]
        if self.failure != "no_change":
            trained.model.weights["layer.lora_B.default.weight"].values += 0.25
        if self.failure == "frozen_changed":
            trained.model.weights["base.weight"].values[0] += 1
        state.global_step = kwargs["num_steps"]
        callback.on_log(None, state, None, logs={"loss": 0.5})
        callback.on_train_end(None, state, None, model=trained.model)
        if self.failure != "no_checkpoint":
            out = Path(kwargs["output_dir"]) / kwargs["finetuned_ckpt_name"]
            out.mkdir()
            (out / "adapter_model.safetensors").write_bytes(b"explicit fake training checkpoint")
            (out / "adapter_config.json").write_text('{"peft_type":"LORA"}')
        return trained


class FakeTimestral:
    def __init__(self):
        self.model = FakeModel()
        self.calls = []

    def forecast(self, contexts, prediction_length):
        self.calls.append(contexts.copy())
        levels = (0.01, 0.1, 0.5, 0.9, 0.99)
        last = contexts[..., -1, None, None]
        q = last + np.arange(1, prediction_length + 1)[None, None, :, None]
        q = q + (np.asarray(levels)[None, None, None, :] - 0.5)
        return SimpleNamespace(quantiles=q, quantile_levels=levels)


class ForecastAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        (self.model_dir / "config.json").write_text('{"fixture":true}')
        (self.model_dir / "model.safetensors").write_bytes(b"explicit fake checkpoint")
        self.revision = "1" * 40
        self.torch = FakeTorch()
        self.chronos = FakeChronos()
        self.timestral = FakeTimestral()
        self.load_calls = []
        self.peft_available = True

        def load_chronos(path, **kwargs):
            self.load_calls.append(("chronos", path, kwargs))
            return self.chronos

        def load_timestral(path, **kwargs):
            self.load_calls.append(("timestral", path, kwargs))
            return self.timestral

        def restore_peft(model, path, **kwargs):
            self.restore_calls.append((path, kwargs))
            restored = FakeModel(lora=True)
            restored.weights["layer.lora_B.default.weight"].values += 0.25
            return restored

        self.restore_calls = []

        self.dependencies = {
            "torch": self.torch,
            "chronos": SimpleNamespace(Chronos2Pipeline=SimpleNamespace(from_pretrained=load_chronos)),
            "timestral": SimpleNamespace(TimestralForecaster=SimpleNamespace(from_pretrained=load_timestral)),
            "peft": SimpleNamespace(PeftModel=SimpleNamespace(from_pretrained=restore_peft)),
            "transformers": SimpleNamespace(TrainerCallback=object),
            "chronos.chronos2.pipeline": SimpleNamespace(is_peft_available=lambda: self.peft_available),
        }

        def dependency(name):
            if name not in self.dependencies:
                raise fm.ForecastAdapterError(f"DEPENDENCY_UNAVAILABLE:{name}")
            return self.dependencies[name]

        self.addCleanup(patch.stopall)
        patch.object(fm, "_dependency", side_effect=dependency).start()
        patch.object(fm, "_versions", return_value={"torch": "fake-test-backend"}).start()
        patch.dict(fm.os.environ, {}, clear=False).start()

    def config(self, kind="chronos2_small", **kwargs):
        return fm.ForecastModelConfig(kind, str(self.model_dir), self.revision, **kwargs)

    def fit_options(self):
        return fm.LoRAFitConfig(str(self.root / "fit"), num_steps=2)

    def series(self):
        return [np.linspace(100, 101, 42, dtype=np.float32)]

    def test_requires_immutable_revision_and_offline_mode(self):
        with self.assertRaisesRegex(ValueError, "IMMUTABLE"):
            fm.ForecastModelConfig("chronos2_small", str(self.model_dir), "main")
        with self.assertRaisesRegex(ValueError, "OFFLINE"):
            self.config(offline_only=False)
        with self.assertRaisesRegex(ValueError, "INVALID_BATCH_SIZE"):
            self.config(batch_size=0)

    def test_hash_pin_detects_same_size_replacement(self):
        inventory = fm._artifact_inventory(self.config())
        replacement = self.model_dir / "new"
        replacement.write_bytes(b"replaced fake checkpoint")
        self.assertEqual(replacement.stat().st_size, (self.model_dir / "model.safetensors").stat().st_size)
        replacement.replace(self.model_dir / "model.safetensors")
        with self.assertRaisesRegex(fm.ForecastAdapterError, "MODEL_FILE_HASH_MISMATCH"):
            fm._artifact_inventory(self.config(expected_files_sha256=inventory["files_sha256"]))

    def test_snapshot_revision_and_shard_path_validation(self):
        snapshot = self.root / "snapshots" / ("2" * 40)
        snapshot.mkdir(parents=True)
        config = fm.ForecastModelConfig("chronos2_small", str(snapshot), self.revision)
        with self.assertRaisesRegex(fm.ForecastAdapterError, "REVISION_MISMATCH"):
            fm._artifact_inventory(config)
        (self.model_dir / "model.safetensors").unlink()
        (self.model_dir / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "../escape.safetensors"}}))
        with self.assertRaisesRegex(fm.ForecastAdapterError, "FILE_NAME"):
            fm._artifact_inventory(self.config())

    def test_load_checks_artifact_again_after_backend_load(self):
        def changed_loader(path, **kwargs):
            (self.model_dir / "config.json").write_text('{"fixture":false}')
            return self.chronos
        self.dependencies["chronos"].Chronos2Pipeline.from_pretrained = changed_loader
        with self.assertRaisesRegex(fm.ForecastAdapterError, "CHANGED_WHILE_LOADING"):
            fm.create_forecast_adapter(self.config()).load()

    def test_chronos_freezes_and_records_cpu_limits_and_pin(self):
        adapter = fm.create_forecast_adapter(self.config(batch_size=2, context_length=4))
        receipt = adapter.load()
        self.assertEqual(receipt["trainable_parameters"], 0)
        self.assertEqual(receipt["total_parameters"], 1000)
        self.assertEqual(len(receipt["parameters_sha256"]), 64)
        self.assertFalse(self.chronos.model.training)
        self.assertFalse(any(x.requires_grad for x in self.chronos.model.parameters()))
        self.assertEqual(self.torch.threads, 2)
        self.assertEqual(self.torch.interop_threads, 1)
        self.assertEqual(self.load_calls[0][2]["device_map"], "cpu")
        self.assertIs(self.load_calls[0][2]["local_files_only"], True)
        self.assertIs(self.load_calls[0][2]["trust_remote_code"], False)
        result = adapter.predict([np.arange(1, 7), np.arange(10, 15), np.arange(20, 23)], 5)
        self.assertEqual(result.quantiles.shape, (3, 3, 5))
        self.assertEqual(result.features.shape, (3, 7))
        self.assertEqual(result.metadata["original_context_lengths"], [6, 5, 3])
        self.assertEqual(result.metadata["effective_context_lengths"], [4, 4, 3])
        self.assertEqual(len(self.load_calls), 1)
        self.assertEqual(len(self.chronos.predict_calls), 2)
        for _, options in self.chronos.predict_calls:
            self.assertIs(options["cross_learning"], False)
        np.testing.assert_allclose(result.quantiles[:, 1, -1], [11, 19, 27])

    def test_timestral_keeps_row_order_without_padding_or_quantile_axis_error(self):
        adapter = fm.create_forecast_adapter(self.config("timestral_5m", batch_size=4))
        contexts = [np.array([9, 10]), np.array([18, 19, 20]), np.array([29, 30]), np.array([38, 39, 40])]
        result = adapter.predict(contexts, 4)
        self.assertEqual(result.quantiles.shape, (4, 3, 4))
        np.testing.assert_allclose(result.quantiles[:, 1, -1], [14, 24, 34, 44])
        self.assertEqual(sorted(x.shape for x in self.timestral.calls), [(2, 1, 2), (2, 1, 3)])
        self.assertTrue(all(np.isfinite(x).all() for x in self.timestral.calls))
        with self.assertRaisesRegex(fm.ForecastAdapterError, "PUBLIC_LORA_TRAINING_UNAVAILABLE"):
            adapter.fit_lora(self.series(), 30, self.fit_options())

    def test_feature_map_is_scale_free_and_rejects_invalid_outputs(self):
        q = np.array([[[98, 108], [100, 110], [102, 112]]], dtype=float)
        expected = np.array([[0.08, 0.10, 0.12, 0.05, 0.0, 0.10, 0.04]])
        np.testing.assert_allclose(fm.forecast_features(q, np.array([100])), expected, atol=1e-12)
        np.testing.assert_allclose(fm.forecast_features(q * 10, np.array([1000])), expected, atol=1e-12)
        q[0, 1, 0] = np.nan
        with self.assertRaisesRegex(fm.ForecastAdapterError, "NONFINITE"):
            fm.forecast_features(q, np.array([100]))

    def test_predict_rejects_missing_prices_instead_of_inventing_quotes(self):
        adapter = fm.create_forecast_adapter(self.config())
        for series in ([], [[1, np.nan]], [[1, 0]], [[1, float("inf")]]):
            with self.assertRaises(fm.ForecastAdapterError):
                adapter.predict(series, 30)
        self.assertFalse(self.load_calls)

    def test_short_training_series_blocks_before_dependency_load(self):
        adapter = fm.create_forecast_adapter(self.config())
        with self.assertRaisesRegex(fm.ForecastAdapterError, "SHORT_TRAINING_SERIES:need=36"):
            adapter.fit_lora([np.linspace(1, 2, 24)], 30, self.fit_options())
        self.assertFalse(self.load_calls)
        self.assertFalse((self.root / "fit").exists())

    def test_missing_peft_cannot_fall_back_to_full_training(self):
        del self.dependencies["peft"]
        with self.assertRaisesRegex(fm.ForecastAdapterError, "DEPENDENCY_UNAVAILABLE:peft"):
            fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, self.fit_options())
        self.assertFalse(self.load_calls)
        self.assertFalse(self.chronos.fit_calls)

    def test_upstream_peft_availability_false_blocks_before_loading(self):
        self.peft_available = False
        with self.assertRaisesRegex(fm.ForecastAdapterError, "NO_FULL_TRAINING_FALLBACK"):
            fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, self.fit_options())
        self.assertFalse(self.load_calls)

    def test_unexpected_full_training_is_blocked_before_optimizer_steps(self):
        self.chronos.failure = "full"
        with self.assertRaisesRegex(fm.ForecastAdapterError, "NON_LORA_PARAMETERS"):
            fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, self.fit_options())
        self.assertEqual(self.chronos.steps_executed, 0)

    def test_lora_receipt_requires_changed_adapter_and_unchanged_base(self):
        adapter = fm.create_forecast_adapter(self.config())
        initial = adapter.load()
        before_forecast = adapter.predict([[99, 100]], 2).quantiles.copy()
        receipt = adapter.fit_lora(self.series(), 30, self.fit_options())
        self.assertEqual(receipt["completed_steps"], 2)
        self.assertEqual(receipt["trainable_parameters_during_fit"], 8)
        self.assertNotEqual(receipt["trainable_sha256_before"], receipt["trainable_sha256_after"])
        self.assertEqual(receipt["frozen_sha256_before"], receipt["frozen_sha256_after"])
        self.assertEqual(fm._parameter_audit(self.chronos.model)["sha256"], initial["parameters_sha256"])
        self.assertTrue(receipt["checkpoint_files_sha256"])
        self.assertEqual(receipt["inference_trainable_parameters"], 0)
        self.assertTrue((self.root / "fit/training-receipt.json").is_file())
        np.testing.assert_allclose(adapter.predict([[99, 100]], 2).quantiles - before_forecast, 0.25)
        call = self.chronos.fit_calls[0]
        self.assertEqual(call["finetune_mode"], "lora")
        self.assertEqual(call["optim"], "adamw_torch")
        self.assertEqual(call["dataloader_num_workers"], 0)
        self.assertEqual(call["lora_config"]["bias"], "none")
        with self.assertRaisesRegex(fm.ForecastAdapterError, "NEW_ADAPTER_REQUIRED"):
            adapter.fit_lora(self.series(), 30, self.fit_options())

    def test_rejects_noop_training_and_frozen_parameter_changes(self):
        for mode, expected in (("no_change", "DID_NOT_CHANGE"), ("frozen_changed", "FROZEN_PARAMETERS_CHANGED")):
            with self.subTest(mode=mode):
                self.chronos.failure = mode
                options = fm.LoRAFitConfig(str(self.root / mode), num_steps=2)
                with self.assertRaisesRegex(fm.ForecastAdapterError, expected):
                    fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, options)

    def test_training_refuses_source_directory_and_existing_output(self):
        adapter = fm.create_forecast_adapter(self.config())
        for output in (self.model_dir / "fit", self.model_dir):
            with self.assertRaisesRegex(fm.ForecastAdapterError, "SOURCE_CHECKPOINT"):
                adapter.fit_lora(self.series(), 30, fm.LoRAFitConfig(str(output), num_steps=2))
        out = self.root / "existing"
        out.mkdir()
        (out / "receipt").write_text("preserve")
        with self.assertRaisesRegex(fm.ForecastAdapterError, "MUST_BE_EMPTY"):
            adapter.fit_lora(self.series(), 30, fm.LoRAFitConfig(str(out), num_steps=2))

    def test_lora_restores_identical_frozen_predictions(self):
        trained = fm.create_forecast_adapter(self.config())
        receipt = trained.fit_lora(self.series(), 30, self.fit_options())
        expected = trained.predict([[99, 100]], 5).quantiles
        restored = fm.create_forecast_adapter(self.config())
        result = restored.restore_lora(self.root / "fit/training-receipt.json")
        self.assertEqual(result["parameters_sha256"], receipt["parameters_sha256"])
        self.assertEqual(result["trainable_parameters"], 0)
        self.assertIs(self.restore_calls[0][1]["is_trainable"], False)
        self.assertIs(self.restore_calls[0][1]["local_files_only"], True)
        np.testing.assert_array_equal(restored.predict([[99, 100]], 5).quantiles, expected)

    def test_lora_restore_rejects_tampered_checkpoint_before_peft_load(self):
        receipt = fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, self.fit_options())
        (Path(receipt["checkpoint_path"]) / "adapter_model.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(fm.ForecastAdapterError, "CHECKPOINT_HASH_MISMATCH"):
            fm.create_forecast_adapter(self.config()).restore_lora(receipt)
        self.assertFalse(self.restore_calls)

    def test_lora_restore_rejects_wrong_base_and_wrong_loaded_parameters(self):
        receipt = fm.create_forecast_adapter(self.config()).fit_lora(self.series(), 30, self.fit_options())
        with self.assertRaisesRegex(fm.ForecastAdapterError, "SOURCE_BINDING_MISMATCH"):
            fm.create_forecast_adapter(self.config()).restore_lora({**receipt, "source_revision": "3" * 40})
        self.assertFalse(self.restore_calls)
        receipt["parameters_sha256"] = "0" * 64
        restored = fm.create_forecast_adapter(self.config())
        with self.assertRaisesRegex(fm.ForecastAdapterError, "RESTORED_LORA_PARAMETERS_MISMATCH"):
            restored.restore_lora(receipt)
        self.assertEqual(restored.predict([[99, 100]], 1).quantiles[0, 1, 0], 101)


if __name__ == "__main__":
    unittest.main()
