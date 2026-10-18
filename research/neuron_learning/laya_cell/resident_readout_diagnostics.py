"""Small, detached readout diagnostics; no model mutation or model selection."""
from __future__ import annotations
import math
import torch


def tensor_summary(value):
    x = value.detach().to(device="cpu", dtype=torch.float64)
    if x.numel() == 0 or not bool(torch.isfinite(x).all()):
        raise ValueError("FINITE_NONEMPTY_DIAGNOSTIC_TENSOR_REQUIRED")
    flat = x.reshape(-1)
    return {"shape": list(value.shape), "dtype": str(value.dtype),
            "minimum": float(flat.min()), "maximum": float(flat.max()),
            "mean": float(flat.mean()), "std": float(flat.std(unbiased=False)),
            "l2": float(flat.norm()),
            "quantiles_0_10_50_90_99_100": torch.quantile(
                flat, torch.tensor([0., .1, .5, .9, .99, 1.], dtype=flat.dtype)).tolist()}


def action_margin_parts(hidden, weight, bias, a, b, columns):
    """Reconstruct the two-logit margin from the SAME captured hidden.

    Use detached CPU FP64 for the reference calculation. Its difference from the
    actual FP32 output is reported, not silently treated as an exact identity.
    """
    values = [hidden, weight, bias, a, b]
    if any(not bool(torch.isfinite(t).all()) for t in values):
        raise ValueError("NONFINITE_READOUT")
    h, w, bias, a, b = [t.detach().to(device="cpu", dtype=torch.float64) for t in values]
    cols = torch.as_tensor(columns, dtype=torch.long, device="cpu")
    if (h.ndim != 2 or w.shape != (2, h.shape[1]) or bias.shape != (2,) or
            a.ndim != 2 or b.shape != (2, a.shape[0]) or
            cols.ndim != 1 or cols.numel() != a.shape[1] or
            len(set(cols.tolist())) != cols.numel() or
            bool(((cols < 0) | (cols >= h.shape[1])).any())):
        raise ValueError("READOUT_SHAPE_OR_MASK")
    base_by_column = h * (w[0] - w[1])
    adapted_base = base_by_column[:, cols].sum(-1)
    remainder = base_by_column.sum(-1) - adapted_base
    delta = b @ a
    adapter = h[:, cols] @ (delta[0] - delta[1])
    bias_part = torch.full_like(adapter, float(bias[0] - bias[1]))
    reconstruction = adapted_base + remainder + bias_part + adapter
    return {"base_adapted_columns": adapted_base,
            "base_other_columns": remainder, "base_bias": bias_part,
            "adapter": adapter, "reconstructed_margin_fp64": reconstruction,
            "base_by_column": base_by_column, "effective_delta": delta}


def geometry(hidden, margin, target_prior):
    """Training-set expressibility only; this fit is never a candidate."""
    if not math.isfinite(target_prior) or not 0 < target_prior < 1:
        raise ValueError("INTERIOR_TRAIN_PRIOR_REQUIRED")
    h = hidden.detach().to(device="cpu", dtype=torch.float64)
    m = margin.detach().to(device="cpu", dtype=torch.float64)
    if h.ndim != 2 or m.shape != h.shape[:1] or not bool(torch.isfinite(h).all()) or not bool(torch.isfinite(m).all()):
        raise ValueError("GEOMETRY_SHAPE_OR_FINITE")
    singular = torch.linalg.svdvals(h)
    centered = h - h.mean(0, keepdim=True)
    centered_singular = torch.linalg.svdvals(centered)
    target = torch.full_like(m, math.log(target_prior / (1 - target_prior))) - m
    fit = torch.linalg.lstsq(h, target, driver="gelsd")
    residual = h @ fit.solution - target
    constant_fit = torch.linalg.lstsq(h, torch.ones_like(m), driver="gelsd")
    return {"training_only_not_candidate": True, "rows": h.shape[0],
            "columns": h.shape[1], "rank": int(fit.rank),
            "singular_values": singular.tolist(),
            "centered_singular_values": centered_singular.tolist(),
            "constant_offset_projection_rmse": float((h @ constant_fit.solution - 1).square().mean().sqrt()),
            "minimum_margin_weight_delta_l2": float(fit.solution.norm()),
            "minimum_symmetric_two_row_delta_l2": float(fit.solution.norm() / math.sqrt(2)),
            "prior_offset_fit_rmse": float(residual.square().mean().sqrt()),
            "target_prior": target_prior}


def probability_metrics(price_logits, action_logits, price_targets, action_targets):
    """Stable NLL from raw logits; the rows are training diagnostics only."""
    p = price_logits.detach().to(device="cpu", dtype=torch.float64)
    a = action_logits.detach().to(device="cpu", dtype=torch.float64)
    yp = torch.as_tensor(price_targets, dtype=torch.long)
    ya = torch.as_tensor(action_targets, dtype=torch.long)
    if (p.ndim != 2 or p.shape[1] != 7 or a.shape != (p.shape[0], 2) or
            yp.shape != p.shape[:1] or ya.shape != p.shape[:1] or
            not bool(torch.isfinite(p).all()) or not bool(torch.isfinite(a).all())):
        raise ValueError("METRIC_SHAPE_OR_FINITE")
    rows = torch.arange(len(p))
    pl, al = p.log_softmax(-1), a.log_softmax(-1)
    pp, ap = pl.exp(), al.exp()
    yph = torch.nn.functional.one_hot(yp, 7).double()
    yah = torch.nn.functional.one_hot(ya, 2).double()
    margin = a[:, 0] - a[:, 1]
    positives, negatives = margin[ya == 0], margin[ya == 1]
    auc = (float(((positives[:, None] > negatives).double() +
                  .5 * (positives[:, None] == negatives).double()).mean())
           if len(positives) and len(negatives) else None)
    return {"training_only": True, "rows": len(p),
            "price_nll": float(-pl[rows, yp].mean()),
            "price_brier": float((pp - yph).square().sum(-1).mean()),
            "price_correct": int((pp.argmax(-1) == yp).sum()),
            "price_prediction_counts": torch.bincount(pp.argmax(-1), minlength=7).tolist(),
            "price_target_counts": torch.bincount(yp, minlength=7).tolist(),
            "action_nll": float(-al[rows, ya].mean()),
            "action_brier": float((ap - yah).square().sum(-1).mean()),
            "ACT_targets": int((ya == 0).sum()),
            "ACT_at_half": int((ap[:, 0] >= .5).sum()),
            "ACT_at_five_sixths": int((ap[:, 0] >= 5/6).sum()),
            "raw_margin_auc_training_only": auc,
            "action_margin": tensor_summary(margin)}
