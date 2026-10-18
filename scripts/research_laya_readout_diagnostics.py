#!/usr/bin/env python3
"""Pocket4-only frozen training readout diagnosis; no prospective scoring."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

ROOT = Path("/home/alex/.local/state/heptatrader/operator-evidence/train-readout-diagnostics-20261018")
SUBSET = ROOT / "fixed-training-subset.json"
SUBSET_SHA = "915803497a24dd66c0d211e043adb1d968f00cf33f159d7d44bd958b0cdc50cd"
GUARD = Path("/home/alex/.local/state/heptatrader/operator-evidence/train-forward-plan-admission-20261017/trainer-forward-plan-guard.v1.json")
GUARD_SHA = "9839fb4e3c148cf58ebd420f9ec58c2ab4d77d4c50fb381d1cc217b0e0dd834d"
MAX_SECONDS = 360


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024**2), b""):
            h.update(data)
    return h.hexdigest()


def write_once(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    Path(path).chmod(0o400)
    return hashlib.sha256(raw).hexdigest()


class Admission:
    """Preserve current thresholds, manual hold, Value priority and global lease."""
    def __init__(self):
        from historical_training.resident_resource_guard import verify_config, Lease
        if sha(GUARD) != GUARD_SHA or sha(SUBSET) != SUBSET_SHA:
            raise RuntimeError("FROZEN_READOUT_INPUT_OR_GUARD_CHANGED")
        self.c = json.loads(GUARD.read_bytes())
        self.original = verify_config(self.c)
        self.lease = Lease(Path(self.c["state_root"]) / "heavy-worker.lock")
        self.report = None

    def observe(self):
        from historical_training.resident_resource_guard import assess_resources, parse_meminfo, parse_full_psi
        from historical_training.value_priority_admission import decision
        c = self.c
        report = assess_resources(parse_meminfo(Path("/proc/meminfo").read_text()),
                                  parse_full_psi(Path("/proc/pressure/memory").read_text()),
                                  int(time.time()*1000), os.path.lexists(c["manual_hold_path"]))
        if sha(GUARD) != GUARD_SHA or sha(SUBSET) != SUBSET_SHA or sha(c["original_config"]) != c["original_config_sha256"] or sha(c["period_path"]) != c["period_sha256"]:
            report["reasons"].append("FROZEN_CONFIG_SOURCE_OR_PERIOD_CHANGED")
        held, reason, proof = decision(c["value_priority"], "trainer", int(time.time()*1000))
        if held:
            report["reasons"].append(reason or "VALUE_PRIORITY_HOLD")
        report.update(admit_new_worker=not report["reasons"], value_priority_evidence=proof,
                      scope="SEALED_HISTORICAL_READOUT_DIAGNOSTIC_ONLY",
                      source_sha256=SUBSET_SHA, maximum_seconds=MAX_SECONDS,
                      fresh_original_live_source_proven=False, model_promotion=False, orders_sent=0)
        self.report = report
        return report["admit_new_worker"]

    def acquire(self):
        if not self.observe():
            return False
        if not self.lease.acquire():
            self.report["admit_new_worker"] = False
            self.report["reasons"].append("OTHER_RESEARCH_WORKER_ACTIVE")
            return False
        try:
            if self.observe():
                self.report["original_heavy_lease_acquired"] = True
                return True
        except Exception:
            self.lease.release()
            raise
        self.lease.release()
        return False


def run(out, admission, sandbox):
    import torch
    from torch.nn import functional as F
    from safetensors.torch import load_file, save_file
    from laya_cell.integrity import digest, read_json
    from laya_cell.model import load_verified_laya, tensor_hash
    from laya_cell.resident_model import ResidentDecisionCell
    from laya_cell.resident_train import validate_parent
    from laya_cell.original_input_order import encode_original_forward_rows
    from laya_cell.dataset import BIN_EDGES_BPS, BIN_TEXT, question
    from laya_cell.resident_readout_diagnostics import tensor_summary, action_margin_parts, geometry, probability_metrics

    started = time.monotonic()
    report = {"schema": "heptatrader.frozen-train-readout-diagnostics.v1",
              "status": "IN_PROGRESS", "at_ms": int(time.time()*1000),
              "program_sha256": sha(__file__), "subset_sha256": SUBSET_SHA,
              "sandbox": sandbox, "admission": admission.report,
              "old_holdout_forwards": 0, "optimizer_steps": 0,
              "model_promotion": False, "orders_sent": 0}
    handles = []
    try:
        data = read_json(SUBSET, 4*1024**2)
        rows = data["rows"]
        if len(rows) != 32 or sum(r["parent_correct"] for r in rows) != 2:
            raise RuntimeError("FIXED_TRAIN_SUBSET_CHANGED")
        if any(r["anchor_ms"] >= data["training_cutoff_ms"] or
               r["future_outcome_available_ms"] > data["training_cutoff_ms"] or
               r["question"] != question(300) for r in rows):
            raise RuntimeError("TRAIN_TIME_OR_PRICE_BIN_MAPPING_CHANGED")
        parent_dir = Path(admission.original["initial_parent_directory"])
        parent = validate_parent(parent_dir)
        if parent["candidate_id"] != data["frozen_parent_id"]:
            raise RuntimeError("FROZEN_PARENT_CHANGED")
        cutoff = parent.get("trained_until_ms", parent.get("window", {}).get("training_end_ms"))
        if cutoff is None or any(r["anchor_ms"] < cutoff for r in rows):
            raise RuntimeError("PARENT_AFTER_TRAIN_ANCHOR")
        torch.set_num_threads(2); torch.manual_seed(20261007)
        base = Path(admission.original["base_directory"])
        raw, tok, base_id = load_verified_laya(base, read_json(base/"base-manifest.json"))
        if base_id != parent["base_id"]:
            raise RuntimeError("PARENT_BASE_CHANGED")
        cell = ResidentDecisionCell(raw, base_id)
        cell.restore_parent(load_file(str(parent_dir/"adapter.safetensors")), allow_scorer_only=True)
        initial = cell.trainable_state()
        initial_id = digest({n: tensor_hash(v) for n, v in initial.items()})
        if initial_id != data["initialization_sha256"]:
            raise RuntimeError("PARENT_INITIALIZATION_CHANGED")
        cell.eval()
        torch.cuda.reset_peak_memory_stats()
        report.update(base_id=base_id, parent_id=parent["candidate_id"], initialization_sha256=initial_id,
                      torch_version=torch.__version__, rocm_version=torch.version.hip,
                      device=str(torch.cuda.get_device_properties(0).gcnArchName),
                      dtype=str(next(cell.parameters()).dtype), autocast_enabled=False,
                      transformer_batch_first=[x.self_attn.batch_first for x in raw.head.layers],
                      transformer_norm_first=[x.norm_first for x in raw.head.layers],
                      price_bins={"edges_bps": list(BIN_EDGES_BPS), "index_to_text": list(BIN_TEXT)},
                      parent_norm=cell.norm_report())
        if not all(report["transformer_batch_first"]):
            raise RuntimeError("TRANSFORMER_LAYOUT_CHANGED")
        print("READOUT_PARENT_LOADED", initial_id, flush=True)

        def deadline():
            if time.monotonic() - started > MAX_SECONDS:
                raise RuntimeError("READOUT_DEADLINE")
            if os.path.lexists(admission.c["manual_hold_path"]):
                raise RuntimeError("MANUAL_RESEARCH_HOLD_APPEARED")

        def encode(row):
            return {k: v.to("cuda:0") for k, v in encode_original_forward_rows(tok, [row]).items()}

        representatives = [next(i for i, r in enumerate(rows) if r["parent_correct"] is flag)
                           for flag in (True, False)]
        plain = {}
        with torch.no_grad():
            for i in representatives:
                deadline()
                p, a = cell(**encode(rows[i]))
                plain[i] = (p.detach().cpu().clone(), a.detach().cpu().clone())

        current = {}
        def put(name, value):
            if name in current:
                raise RuntimeError("HOOK_CALLED_TWICE:" + name)
            current[name] = value.detach().to("cpu").clone()

        handles.append(raw.encoder.register_forward_hook(
            lambda m, args, value: put("encoder_cls", value.last_hidden_state[:, 0])))
        handles.append(raw.type_emb.register_forward_hook(
            lambda m, args, value: put("type_embedding", value)))
        for index, layer in enumerate(raw.head.layers):
            handles.append(layer.register_forward_hook(
                lambda m, args, value, key="head_%d_cls" % index: put(key, value[:, 0])))
        for name, module in [("price_layernorm", raw.scorer[0]),
                             ("price_final", raw.scorer[3]),
                             ("action_first", raw.act_head[0]),
                             ("action_final", raw.act_head[2]),
                             ("action_final_base", raw.act_head[2].base)]:
            handles.append(module.register_forward_pre_hook(
                lambda m, args, key=name+"_input": put(key, args[0])))
            handles.append(module.register_forward_hook(
                lambda m, args, value, key=name+"_output": put(key, value)))
        captured, prices, actions, metadata, parity = [], [], [], [], []
        with torch.no_grad():
            for i, row in enumerate(rows):
                deadline(); current.clear(); batch = encode(row)
                p, a = cell(**batch)
                p, a = p.detach().cpu().clone(), a.detach().cpu().clone()
                if not bool(torch.isfinite(p).all() and torch.isfinite(a).all()):
                    raise RuntimeError("NONFINITE_PARENT_FORWARD")
                pp = p.softmax(-1)[0].tolist()
                original_error = max(abs(x-y) for x, y in zip(pp, row["frozen_parent_probs"]))
                if original_error > 5e-5 or (p.argmax(-1).item() == row["target_class"]) != row["parent_correct"]:
                    raise RuntimeError("FROZEN_ORIGINAL_PARENT_PARITY")
                if i in plain:
                    price_error = float((p.softmax(-1)-plain[i][0].softmax(-1)).abs().max())
                    action_error = float((a-plain[i][1]).abs().max())
                    parity.append({"row_id": row["row_id"], "ACT_target": row["parent_correct"],
                                   "price_probability_max_abs": price_error,
                                   "action_logit_max_abs": action_error,
                                   "plain_price": plain[i][0][0].tolist(),
                                   "hook_price": p[0].tolist(), "plain_action": plain[i][1][0].tolist(),
                                   "hook_action": a[0].tolist()})
                    if price_error > 5e-5 or action_error > .05:
                        raise RuntimeError("HOOK_CHANGED_FORWARD")
                markers = batch["marker_pos"][0].cpu()
                ids = batch["input_ids"][0].cpu()
                if batch["marker_mask"].sum().item() != 7:
                    raise RuntimeError("PRICE_OPTION_COUNT_CHANGED")
                metadata.append({"row_id": row["row_id"], "input_id": row["input_id"],
                                 "target_class": row["target_class"], "ACT_target": row["parent_correct"],
                                 "sequence_length": int(batch["attention_mask"].sum()),
                                 "marker_positions": markers.tolist(),
                                 "marker_token_ids": ids[markers].tolist(),
                                 "encoded_tensors_sha256": digest({k:tensor_hash(v) for k,v in batch.items()}),
                                 "original_probability_max_abs": original_error})
                captured.append(dict(current)); prices.append(p); actions.append(a)
                print("READOUT_ROW", i+1, "elapsed", round(time.monotonic()-started, 2), flush=True)
        for handle in handles: handle.remove()
        handles.clear()
        trace = {k: torch.cat([v[k] for v in captured], 0).contiguous() for k in captured[0]}
        trace["price_logits"] = torch.cat(prices, 0).contiguous()
        trace["action_logits"] = torch.cat(actions, 0).contiguous()
        last = raw.act_head[2]
        weights = {"action_first_weight": raw.act_head[0].weight.detach().cpu().contiguous(),
                   "action_first_bias": raw.act_head[0].bias.detach().cpu().contiguous(),
                   "action_final_weight": last.base.weight.detach().cpu().contiguous(),
                   "action_final_bias": last.base.bias.detach().cpu().contiguous(),
                   "action_A": last.A.detach().cpu().contiguous(),
                   "action_B": last.B.detach().cpu().contiguous()}
        parts = action_margin_parts(trace["action_final_input"], weights["action_final_weight"],
                                    weights["action_final_bias"], weights["action_A"], weights["action_B"],
                                    last.col_index.cpu())
        actual_margin = trace["action_logits"][:, 0].double() - trace["action_logits"][:, 1].double()
        residual = parts["reconstructed_margin_fp64"] - actual_margin
        if float(residual.abs().max()) > .05:
            raise RuntimeError("ACTION_DECOMPOSITION_RESIDUAL")
        x = trace["action_first_input"].double()
        w0 = weights["action_first_weight"].double()
        first_cls = F.linear(x[:, :768], w0[:, :768])
        first_features = F.linear(x[:, 768:], w0[:, 768:])
        first_bias = weights["action_first_bias"].double().expand_as(first_cls)
        first_residual = first_cls + first_features + first_bias - trace["action_first_output"].double()
        if float(first_residual.abs().max()) > .05:
            raise RuntimeError("ACTION_FIRST_LAYER_RECONSTRUCTION")
        trace.update(action_first_cls_contribution=first_cls, action_first_price_feature_contribution=first_features,
                     action_margin_base_by_column=parts["base_by_column"],
                     action_margin_reconstruction_residual=residual,
                     action_first_preactivation_reconstruction_residual=first_residual)
        report["first_layer"] = {"CLS_contribution": tensor_summary(first_cls),
                                 "price_feature_contribution": tensor_summary(first_features),
                                 "bias_contribution": tensor_summary(first_bias),
                                 "reconstruction_residual": tensor_summary(first_residual),
                                 "price_features": tensor_summary(x[:, 768:])}
        report["trace_summaries"] = {k: tensor_summary(v) for k, v in trace.items()}
        report["margin_parts"] = {k: tensor_summary(v) for k, v in parts.items()
                                  if k not in ("base_by_column", "effective_delta")}
        report["reconstruction_residual"] = tensor_summary(residual)
        report["effective_action_delta"] = tensor_summary(parts["effective_delta"])
        report["per_dimension_action_hidden"] = {
            "mean": trace["action_final_input"].double().mean(0).tolist(),
            "std": trace["action_final_input"].double().std(0, unbiased=False).tolist()}
        report["geometry_adapted64"] = geometry(trace["action_final_input"][:, :64], actual_margin, 9.5/179)
        report["metrics"] = probability_metrics(trace["price_logits"], trace["action_logits"],
                                                [r["target_class"] for r in rows],
                                                [0 if r["parent_correct"] else 1 for r in rows])
        report["row_metadata"] = metadata; report["hook_parity"] = parity
        report["input_units"] = {"anchor_ms": [min(r["anchor_ms"] for r in rows), max(r["anchor_ms"] for r in rows)],
                                 "offset_ms": [min(q[0] for r in rows for q in r["state"]["quotes"]), 0],
                                 "quote_prices": [min(float(v) for r in rows for q in r["state"]["quotes"] for v in q[1:]),
                                                  max(float(v) for r in rows for q in r["state"]["quotes"] for v in q[1:])],
                                 "ingestion": "TEXT_TOKEN_IDS_NOT_RAW_NUMERIC_FEATURE_VECTOR"}
        for name, tensors in [("activation-trace.safetensors", trace), ("frozen-readout.safetensors", weights)]:
            save_file(tensors, str(out/name)); (out/name).chmod(0o400)
            report[name+"_sha256"] = sha(out/name)
        cell.assert_frozen()
        if digest({n:tensor_hash(v) for n,v in cell.trainable_state().items()}) != initial_id:
            raise RuntimeError("DIAGNOSIS_CHANGED_ADAPTER")
        torch.cuda.synchronize()
        report.update(status="COMPLETED", elapsed_seconds=time.monotonic()-started,
                      peak_gpu_allocated_bytes=torch.cuda.max_memory_allocated(),
                      frozen_base_unchanged=True, adapter_unchanged=True)
        write_once(out/"result.json", report)
        print("READOUT_DIAGNOSIS_COMPLETE", json.dumps({"metrics":report["metrics"],
              "elapsed_seconds":report["elapsed_seconds"],"peak_gpu_allocated_bytes":report["peak_gpu_allocated_bytes"]}), flush=True)
        return 0
    except Exception as exc:
        report.update(status="FAILED", error_type=type(exc).__name__, error=str(exc)[:300],
                      elapsed_seconds=time.monotonic()-started)
        write_once(out/"failure.json", report)
        print("READOUT_DIAGNOSIS_FAILED", str(exc), flush=True)
        return 2
    finally:
        for handle in handles: handle.remove()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(); out = args.output_directory
    if out.parent != ROOT or out.exists() or ROOT.resolve() != ROOT:
        parser.error("new direct Pocket4 readout evidence directory required")
    os.umask(0o077); out.mkdir(mode=0o700)
    admission = Admission()
    if not admission.acquire():
        write_once(out/"denied.json", admission.report)
        print("READOUT_NOT_ADMITTED", json.dumps(admission.report), flush=True)
        return 3
    try:
        from sandbox import enter
        c = admission.original
        proof = enter({"output_dir": str(out), "period_path": c["period_path"],
                       "legacy_capture": str(SUBSET), "oms_path": str(SUBSET), "export_dir": str(SUBSET),
                       "read_only_paths": [str(Path(__file__).resolve().parent),
                           str(Path(__file__).resolve().parents[1]/"research/neuron_learning"),
                           "/home/alex/.local/share/heptatrader/laya-runtime",
                           c["initial_parent_directory"], c["runtime_directory"], str(GUARD),
                           "/proc/pressure/memory"]})
        write_once(out/"sandbox.json", proof)
        return run(out, admission, proof)
    finally:
        admission.lease.release()


if __name__ == "__main__":
    raise SystemExit(main())
