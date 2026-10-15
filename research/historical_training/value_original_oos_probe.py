"""Read-only, time-purged net-value diagnostic on original PAPER decisions.

It fits a small CPU ridge readout to PRE-DECISION quote features and observed
closed outcomes. Neither selected-trade hypothetical PnL nor trading efficacy
may be inferred: only executed behavior is labeled, and future counterfactuals
are missing. No network, brokerage API, model export, or promotion operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from decimal import Decimal
from pathlib import Path

import numpy as np

from .common import EvidenceError, digest

EMBARGO_MS = 15 * 60_000
ALPHAS = (1.0, 10.0, 100.0, 1000.0)
MAX_PREFIX_BYTES = 256 * 1024**2


def features(row):
    """Only snapshots actually recorded before the decision; no reward fields."""
    request = row["source_request"]["request"]
    ts = row["input_started_at_ms"]
    if type(ts) is not int or request["started_at_ms"] != ts:
        raise EvidenceError("INPUT_TIME_OR_IDENTITY")
    bars = request["history_snapshot"]
    if len(bars) != 24:
        raise EvidenceError("PRETRADE_QUOTE_WINDOW")
    times = [q["observed_at_ms"] for q in bars]
    if any(type(t) is not int or t >= ts for t in times) or any(a >= b for a, b in zip(times, times[1:])):
        raise EvidenceError("NONCAUSAL_QUOTE")
    if ts - times[-1] > 120_000:
        raise EvidenceError("STALE_PRETRADE_QUOTE")
    mid = []
    spread = []
    for q in bars:
        bid, ask, m = (float(q[key]) for key in ("bid", "ask", "mid"))
        if not (all(math.isfinite(v) and v > 0 for v in (bid, ask, m))
                and bid <= m <= ask):
            raise EvidenceError("INVALID_PRETRADE_QUOTE")
        mid.append(m)
        spread.append(1e4 * (ask - bid) / m)
    side = {"BUY": 1.0, "SELL": -1.0}.get(row["action"])
    if side is None:
        raise EvidenceError("ACTION_SCOPE")
    ret = [1e4 * (mid[-1] / mid[-1 - k] - 1.0) for k in (1, 3, 6, 12, 23)]
    volatility = float(np.std(np.diff(np.log(mid)) * 1e4))
    phase = (ts // 1000 % 86400) / 86400 * 2 * math.pi
    x = ret + [volatility, spread[-1], float(np.mean(spread)), side] + [side * t for t in ret]
    x += [math.sin(phase), math.cos(phase)]
    if not all(math.isfinite(v) for v in x):
        raise EvidenceError("NONFINITE_PRETRADE_FEATURE")
    return x


def three_way_split(rows, maturities):
    """Training and selection labels must mature before the next partition."""
    n = len(rows)
    if n < 120 or len(maturities) != n:
        raise EvidenceError("INSUFFICIENT_REAL_OUTCOMES")
    a, b = int(n * .6), int(n * .8)
    t1, t2 = rows[a]["input_started_at_ms"], rows[b]["input_started_at_ms"]
    tr = [i for i in range(a) if maturities[i] + EMBARGO_MS < t1]
    va = [i for i in range(a, b) if maturities[i] + EMBARGO_MS < t2]
    te = list(range(b, n))
    if min(len(tr), len(va), len(te)) < 20:
        raise EvidenceError("PURGED_TRAIN_VALIDATION_INSUFFICIENT")
    assert max(maturities[i] for i in tr) + EMBARGO_MS < t1
    assert max(maturities[i] for i in va) + EMBARGO_MS < t2
    return tr, va, te, (t1, t2)


def fit_huber_ridge(x, y, alpha):
    """Fixed Huber delta $25; train-only scaling, no test tuning."""
    mu = x.mean(0)
    sd = np.maximum(x.std(0), 1e-8)
    z = np.concatenate([np.ones((len(x), 1)), np.clip((x-mu)/sd, -6, 6)], axis=1)
    reg = np.eye(z.shape[1]) * alpha
    reg[0, 0] = 0
    coeff = np.zeros(z.shape[1], dtype=float)
    for _ in range(12):
        residual = y - z @ coeff
        weights = np.minimum(1.0, 25.0 / np.maximum(np.abs(residual), 1e-9))
        coeff = np.linalg.solve(z.T @ (weights[:, None] * z) + reg, z.T @ (weights * y))
    if not np.isfinite(coeff).all():
        raise EvidenceError("NONFINITE_RIDGE")
    return mu, sd, coeff


def predict(model, x):
    mu, sd, coeff = model
    z = np.concatenate([np.ones((len(x), 1)), np.clip((x-mu)/sd, -6, 6)], axis=1)
    return z @ coeff


def stats(pred, actual):
    err = pred - actual
    return {"count": int(len(actual)),
            "mae_usd": round(float(np.mean(np.abs(err))), 6),
            "rmse_usd": round(float(np.sqrt(np.mean(err * err))), 6),
            "positive_label_accuracy": round(float(np.mean((pred > 0) == (actual > 0))), 6),
            "observed_net_sum_usd": round(float(sum(actual)), 6)}


def run(dataset_file, status_file):
    from hepta_decision_attribution import read_private

    db = Path(dataset_file).read_bytes()
    if len(db) > 32 * 1024**2:
        raise EvidenceError("DATASET_SIZE_BOUND")
    data = json.loads(db)
    if (data["schema"] != "heptatrader.current100k-value-dataset.v1"
            or data["trade_authority"] is not False
            or data["synthetic_rewards"] != 0
            or data["old25k_rewards"] != 0
            or data["fingerprint"] != digest({k: v for k, v in data.items() if k != "fingerprint"})):
        raise EvidenceError("DATASET_INTEGRITY_OR_REAL_SCOPE")
    sb = Path(status_file).read_bytes()
    state = json.loads(sb)
    if (hashlib.sha256(db).hexdigest() != state["dataset_sha256"]
            or state["row_count"] != len(data["rows"])
            or state.get("model_promoted") is not False):
        raise EvidenceError("CONSUMER_SNAPSHOT_CHANGED")
    source = state["source_ledger_prefix"]
    size = source["bytes"]
    if type(size) is not int or not 0 < size <= MAX_PREFIX_BYTES:
        raise EvidenceError("LEDGER_PREFIX_BOUND")
    raw = read_private(source["path"], MAX_PREFIX_BYTES, prefix_bytes=size)
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise EvidenceError("LEDGER_PREFIX_HASH")
    execution_at = {}
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get("event") == "broker_execution" and type(event.get("ts_ms")) is int:
            eid = event.get("broker_execution_id")
            if eid:
                execution_at[eid] = min(execution_at.get(eid, event["ts_ms"]), event["ts_ms"])
    rows = sorted(data["rows"], key=lambda r: r["input_started_at_ms"])
    if len(set(r["episode_id"] for r in rows)) != len(rows):
        raise EvidenceError("DUPLICATE_EXECUTION_EPISODE")
    if len({r["behavior_scope"]["producer_manifest_sha256"] for r in rows}) != 1:
        raise EvidenceError("MIXED_PRODUCER_BEHAVIOR")
    x = np.asarray([features(r) for r in rows], dtype=float)
    y = np.asarray([float(r["net_usd"]) for r in rows], dtype=float)
    if not np.isfinite(y).all():
        raise EvidenceError("NONFINITE_NET_OUTCOME")
    last_fill = []
    for r in rows:
        reward = r["reward"]
        if (reward.get("fees_complete") is not True
                or reward.get("settlement_complete") is not True
                or reward.get("learning_eligible") is not True
                or reward.get("laya_on_policy") is not False
                or reward.get("reward_id") != r["reward_id"]
                or Decimal(reward["realized_net_usd"]) != Decimal(r["net_usd"])
                or Decimal(reward["actual_commissions_usd"]) != Decimal(r["actual_commissions_usd"])
                or not reward.get("executions")):
            raise EvidenceError("UNSETTLED_OR_UNBOUND_NET_REWARD")
        times = [execution_at.get(e["execution_id"]) for e in r["reward"]["executions"]]
        if any(t is None for t in times):
            raise EvidenceError("MISSING_ORIGINAL_EXECUTION_TIMESTAMP")
        maturity = max(times)
        if maturity < r["input_started_at_ms"]:
            raise EvidenceError("EXECUTION_PRECEDES_REQUEST")
        last_fill.append(maturity)
    tr, va, te, cutoffs = three_way_split(rows, last_fill)
    candidates = []
    for alpha in ALPHAS:
        model = fit_huber_ridge(x[tr], y[tr], alpha)
        v = stats(predict(model, x[va]), y[va])
        candidates.append((v["mae_usd"], -alpha, model, alpha))
    selected = min(candidates, key=lambda k: k[:2])
    model = selected[2]
    v = stats(predict(model, x[va]), y[va])
    result = stats(predict(model, x[te]), y[te])
    baseline = stats(np.full(len(te), float(np.median(y[tr]))), y[te])
    zero = stats(np.zeros(len(te), dtype=float), y[te])
    coeff = model[2].astype("<f8").tobytes()
    return {"schema": "heptatrader.original-net-oos-research.v1",
            "status": "OFFLINE_OBSERVATIONAL_NOT_CAUSAL",
            "source": {"dataset_sha256": hashlib.sha256(db).hexdigest(),
                       "consumer_status_sha256": hashlib.sha256(sb).hexdigest(),
                       "original_ledger_prefix_sha256": source["sha256"], "original_prefix_bytes": size},
            "real_completed_unique_episodes": len(rows),
            "train_rows_after_maturity_purge": len(tr),
            "validation_rows_after_maturity_purge": len(va),
            "test_rows": len(te), "time_boundaries_ms": list(cutoffs),
            "train_feature_count": int(x.shape[1]), "fixed_huber_delta_usd": 25,
            "selected_ridge_alpha_from_validation": selected[3],
            "validation": v, "test_model": result, "test_train_median_baseline": baseline,
            "test_zero_prediction_baseline": zero,
            "model_coeff_sha256": hashlib.sha256(coeff).hexdigest(),
            "model_beats_train_median_mae": result["mae_usd"] < baseline["mae_usd"],
            "model_beats_zero_prediction_mae": result["mae_usd"] < zero["mae_usd"],
            "not_on_policy_reward": True, "no_counterfactual_for_untraded_actions": True,
            "revised_historical_bars_not_used_as_execution_revenue": True,
            "no_order_authority": True, "orders_sent": 0, "model_promoted": False,
            "future_prospective_alpha_claim": False}


def main():
    ap = argparse.ArgumentParser()
    for key in ("dataset", "status", "output"):
        ap.add_argument("--" + key, type=Path, required=True)
    args = ap.parse_args()
    out = run(args.dataset, args.status)
    path = args.output
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(out, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
