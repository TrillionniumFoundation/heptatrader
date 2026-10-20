#!/usr/bin/env python3
"""Pocket4 closed-source PRICE diagnostics; CPU only, no deployment."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research/neuron_learning"))
from laya_cell import price_readout_diagnostics as d
from laya_cell import resident_action_v2_closed_source as closed
from laya_cell.dataset import BIN_EDGES_BPS
from laya_cell.integrity import canonical, digest, read_json


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_once(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    Path(path).chmod(0o400)
    return sha(path)


def hash_id(value):
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise argparse.ArgumentTypeError("Expected a lowercase SHA256")
    return value


def action_counts(rows):
    correct = sum(
        max(range(7), key=lambda i: row["frozen_parent_probs"][i]) == row["target_class"]
        for row in rows
    )
    return {"ACT_correct": correct, "ESCALATE_wrong": len(rows) - correct,
            "source": "ORIGINAL_FROZEN_PARENT_CORRECTNESS"}


def split_rows(rows, train_end_ms, hold_start_ms, hold_end_ms):
    if not 0 < train_end_ms < hold_start_ms < hold_end_ms:
        raise ValueError("Invalid fixed chronological boundaries")
    training = sorted(
        [r for r in rows if r["anchor_ms"] < train_end_ms
         and r["future_outcome_available_ms"] <= train_end_ms],
        key=lambda r: (r["anchor_ms"], r["row_id"]),
    )
    holdout = sorted(
        [r for r in rows if hold_start_ms <= r["anchor_ms"] < hold_end_ms],
        key=lambda r: (r["anchor_ms"], r["row_id"]),
    )
    if not training or not holdout:
        raise ValueError("The fixed training and holdout populations must be nonempty")
    training_ids = {r["row_id"] for r in training}
    holdout_ids = {r["row_id"] for r in holdout}
    if (len(training_ids) != len(training) or len(holdout_ids) != len(holdout)
            or training_ids & holdout_ids):
        raise ValueError("Duplicate or overlapping training/holdout row identities")
    return training, holdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--parent-id", type=hash_id, required=True)
    parser.add_argument("--cutoff-ms", type=int, required=True)
    parser.add_argument("--train-end-ms", type=int, required=True)
    parser.add_argument("--hold-start-ms", type=int, required=True)
    parser.add_argument("--hold-end-ms", type=int, required=True)
    parser.add_argument("--exclude-plan-id", type=hash_id, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if Path("/etc/hostname").read_text().strip() != "alex-G1628-04":
        raise RuntimeError("POCKET4_ONLY")
    began_ms = int(time.time() * 1000)
    if not (0 < args.train_end_ms < args.hold_start_ms < args.hold_end_ms
            <= args.cutoff_ms <= began_ms):
        raise ValueError("Invalid fixed chronological boundaries or future source cutoff")

    os.umask(0o077)
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    excluded = sorted(set(args.exclude_plan_id))
    protocol = {
        "schema": "heptatrader.price-readout-diagnostic-protocol.v2",
        "at_ms": began_ms, "evaluation_root": str(args.evaluation_root.resolve(strict=True)),
        "frozen_parent_id": args.parent_id, "source_cutoff_ms": args.cutoff_ms,
        "train_end_ms": args.train_end_ms, "hold_start_ms": args.hold_start_ms,
        "hold_end_ms": args.hold_end_ms, "excluded_plan_ids": excluded,
        "horizon_seconds": 300, "price_bins_bps": list(BIN_EDGES_BPS),
        "arms": ["parent", "uniform", "train_frequency", "intercept_only", "temperature_only"],
        "lambda": "1/N_TRAIN", "frequency_smoothing": 0.5, "bias_max_newton_steps": 50,
        "bias_gradient_tolerance": 1e-8, "inverse_temperature_bounds": [0.0, 10.0],
        "no_holdout_selection": True, "holdout_previously_seen": True,
        "retrospective_diagnostic": True, "new_prospective_qualification": False,
        "diagnostics_code_sha256": sha(Path(d.__file__)),
        "collector_code_sha256": sha(Path(closed.__file__)),
        "runner_sha256": sha(Path(__file__)), "numpy_version": np.__version__,
        "broker_orders_sent": 0, "model_promoted": False,
    }
    protocol_sha = write_once(args.output / "protocol.json", protocol)
    collected = closed.collect_parent_closed_train(
        args.evaluation_root, args.parent_id, args.cutoff_ms
    )
    closed.check_train_source(collected)
    rows = [r for r in collected["rows"]
            if r["plan_id"] not in excluded and r["state"]["horizon_sec"] == 300]
    if any(r["parent_id"] != args.parent_id for r in rows):
        raise ValueError("Frozen parent identity changed")
    frozen_source = {
        "schema": "heptatrader.price-readout-closed-source.v1",
        "protocol_sha256": protocol_sha, "collector_manifest": collected["manifest"],
        "excluded_plan_ids": excluded, "horizon_seconds": 300,
        "rows": rows, "rows_digest": digest(rows),
    }
    source_sha = write_once(args.output / "source.json", frozen_source)
    del collected, rows, frozen_source

    # Fit the exact frozen bytes; do not rescan a live evaluation directory.
    frozen_source = read_json(args.output / "source.json", 64 * 1024**2)
    if (sha(args.output / "source.json") != source_sha
            or digest(frozen_source["rows"]) != frozen_source["rows_digest"]):
        raise ValueError("Frozen source changed")
    training, holdout = split_rows(
        frozen_source["rows"], args.train_end_ms, args.hold_start_ms, args.hold_end_ms
    )
    tp = [r["frozen_parent_probs"] for r in training]
    ty = [r["target_class"] for r in training]
    started = time.monotonic()
    fits = {
        "schema": "heptatrader.price-readout-fixed-fit.v1",
        "protocol_sha256": protocol_sha, "source_sha256": source_sha,
        "training_row_ids": [r["row_id"] for r in training],
        "bias": d.fit_bias(tp, ty), "temperature": d.fit_temperature(tp, ty),
        "frequency": d.fit_frequency(ty),
    }
    elapsed_fit = time.monotonic() - started
    fit_sha = write_once(args.output / "fit.json", fits)

    # Freeze coefficients before computing any holdout scores.
    fits = read_json(args.output / "fit.json")
    if sha(args.output / "fit.json") != fit_sha:
        raise ValueError("Frozen fit changed")
    hp = [r["frozen_parent_probs"] for r in holdout]
    hy = [r["target_class"] for r in holdout]
    scores = {
        "parent": d.metrics(hp, hy),
        "uniform": d.metrics(np.full((len(hy), 7), 1 / 7), hy),
        "train_frequency": d.metrics(
            np.broadcast_to(fits["frequency"]["probabilities"], (len(hy), 7)), hy
        ),
        "intercept_only": d.metrics(hp, hy, bias=fits["bias"]["bias"]),
        "temperature_only": d.metrics(
            hp, hy, inverse_temperature=fits["temperature"]["inverse_temperature"]
        ),
    }
    result = {
        "schema": "heptatrader.price-readout-diagnostic.v2",
        "state": "COMPLETED_RESEARCH_ONLY", "at_ms": int(time.time() * 1000),
        "protocol_sha256": protocol_sha, "source_sha256": source_sha, "fit_sha256": fit_sha,
        "train_rows": len(training), "holdout_rows": len(holdout),
        "train_rows_digest": digest([r["row_id"] for r in training]),
        "holdout_rows_digest": digest([r["row_id"] for r in holdout]),
        "source_rows_not_in_split": len(frozen_source["rows"]) - len(training) - len(holdout),
        "excluded_plan_ids": excluded,
        "original_action_labels": {
            "training": action_counts(training), "holdout": action_counts(holdout)
        },
        "holdout_scores": scores, "elapsed_fit_seconds": elapsed_fit,
        "actual_optimizer_updates": fits["bias"]["applied_newton_steps"],
        "no_holdout_selection": True, "holdout_previously_seen": True,
        "retrospective_diagnostic": True, "new_prospective_qualification": False,
        "gpu_passes": 0, "model_forward_passes": 0, "broker_orders_sent": 0,
        "policy_gradient_steps": 0, "model_promoted": False,
    }
    result_sha = write_once(args.output / "result.json", result)
    fd = os.open(args.output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(json.dumps({
        "path": str(args.output / "result.json"), "sha256": result_sha,
        "train_rows": len(training), "holdout_rows": len(holdout),
        "original_action_labels": result["original_action_labels"],
        "holdout": {name: {key: value[key] for key in
            ("rows", "nll", "brier", "accuracy", "predicted_class_counts")}
            for name, value in scores.items()},
    }, sort_keys=True))


if __name__ == "__main__":
    main()
