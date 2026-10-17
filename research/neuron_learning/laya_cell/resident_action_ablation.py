"""Read-only branch gradient diagnostics for a fixed historical ablation."""
import math
import statistics
import torch


def branch_gradient_diagnostics(price_loss, action_loss, named_parameters, weight,
                                *, max_grad_norm=1.0, norm_floor=1e-12):
    """Preserve .grad, parameters, and the original backward graph."""
    weight, max_grad_norm = float(weight), float(max_grad_norm)
    if not math.isfinite(weight) or weight < 0:
        raise ValueError("weight must be finite and nonnegative")
    if not math.isfinite(max_grad_norm) or max_grad_norm <= 0:
        raise ValueError("max_grad_norm must be finite and positive")
    if not math.isfinite(norm_floor) or norm_floor <= 0:
        raise ValueError("norm_floor must be finite and positive")
    pairs = [(n, p) for n, p in named_parameters if p.requires_grad]
    if not pairs or len({id(p) for _, p in pairs}) != len(pairs):
        raise ValueError("Each trainable parameter must appear exactly once")
    params = tuple(p for _, p in pairs)

    def gradients(loss):
        if loss is None:
            return (None,) * len(params)
        if not isinstance(loss, torch.Tensor) or loss.numel() != 1:
            raise ValueError("Each loss must be a scalar tensor or None")
        if loss.is_complex() or not bool(torch.isfinite(loss.detach()).all()):
            raise FloatingPointError("Loss must be finite and real")
        if not loss.requires_grad:
            return (None,) * len(params)
        return torch.autograd.grad(loss, params, retain_graph=True,
                                   create_graph=False, allow_unused=True)

    def dense_double(g, parameter):
        if g is None:
            return torch.zeros_like(parameter, dtype=torch.float64)
        if g.layout != torch.strided or g.is_complex():
            raise ValueError("Only dense real gradients are supported")
        g = g.detach().to(dtype=torch.float64)
        if not bool(torch.isfinite(g).all()):
            raise FloatingPointError("Non-finite branch gradient")
        return g

    pg, ag = gradients(price_loss), gradients(action_loss)
    stats, graph_shared, overlaps, overlap_tensors, connections = [], 0, 0, 0, []
    for (name, parameter), p_grad, a_grad in zip(pairs, pg, ag):
        p, a = dense_double(p_grad, parameter), dense_double(a_grad, parameter)
        p2, a2 = float(p.square().sum()), float(a.square().sum())
        dot = float((p * a).sum())
        total2 = float((p + weight * a).square().sum())
        shared = p_grad is not None and a_grad is not None
        overlap = int(((p != 0) & (a != 0)).sum())
        graph_shared += int(shared); overlaps += overlap
        overlap_tensors += int(overlap > 0)
        connections.append({"name": name, "price": p_grad is not None,
                            "action": a_grad is not None, "overlap_numel": overlap,
                            "price_l2": math.sqrt(p2), "action_l2": math.sqrt(a2)})
        stats.append((p2, a2, dot, total2, p2 if shared else 0.,
                      a2 if shared else 0., dot if shared else 0.))
    if not all(math.isfinite(x) for row in stats for x in row):
        raise FloatingPointError("Non-finite gradient statistic")
    p2, a2, dot, total2, sp2, sa2, sdot = map(math.fsum, zip(*stats))
    if not all(math.isfinite(x) for x in (p2, a2, dot, total2, sp2, sa2, sdot)):
        raise FloatingPointError("Non-finite aggregate gradient statistic")
    pn, an, total, sp, sa = map(math.sqrt, (p2, a2, total2, sp2, sa2))

    def cosine(d, n1, n2):
        return None if min(n1, n2) <= norm_floor else max(-1., min(1., d / n1 / n2))

    clip = min(1., max_grad_norm / (total + 1e-6))
    return {"schema": "laya.branch-gradients.v1", "finite": True,
            "weight": weight, "trainable_tensors": len(pairs),
            "trainable_numel": sum(p.numel() for _, p in pairs),
            "price_l2": pn, "action_l2": an, "weighted_action_l2": weight * an,
            "global_dot": dot, "global_cosine": cosine(dot, pn, an),
            "graph_shared_tensors": graph_shared, "overlap_tensors": overlap_tensors,
            "overlap_numel": overlaps, "shared_price_l2": sp, "shared_action_l2": sa,
            "shared_cosine": cosine(sdot, sp, sa), "combined_l2": total,
            "max_grad_norm": max_grad_norm, "clip_factor": clip,
            "clipped_price_contribution_l2": clip * pn,
            "clipped_weighted_action_contribution_l2": clip * weight * an,
            "connections": connections}


def freeze_weight(records, *, provenance, target_ratio=1., norm_floor=1e-12):
    """Freeze from TRAIN diagnostics at initialization before holdout scoring."""
    if provenance.get("split") != "train":
        raise ValueError("Weight calibration requires split='train'")
    if not math.isfinite(target_ratio) or target_ratio <= 0:
        raise ValueError("target_ratio must be finite and positive")
    if not math.isfinite(norm_floor) or norm_floor <= 0:
        raise ValueError("norm_floor must be finite and positive")
    rows, usable, excluded = list(records), [], []
    for index, row in enumerate(rows):
        values = [float(row[k]) for k in
                  ("price_l2", "action_l2", "shared_price_l2", "shared_action_l2")]
        if row.get("finite") is not True or not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("Invalid calibration diagnostic")
        if min(values[:2]) <= norm_floor: excluded.append(index)
        else: usable.append(row)
    if not usable:
        raise ValueError("Uncalibratable: no batch has two nonzero branch norms")
    shared = all(r["graph_shared_tensors"] > 0 and
                 min(r["shared_price_l2"], r["shared_action_l2"]) > norm_floor for r in usable)
    pk, ak = (("shared_price_l2", "shared_action_l2") if shared else ("price_l2", "action_l2"))
    ratios = [float(r[ak]) / float(r[pk]) for r in usable]
    if not all(math.isfinite(r) and r > 0 for r in ratios):
        raise ValueError("Invalid calibration ratio")
    median_ratio = statistics.median(ratios); weight = target_ratio / median_ratio
    if not math.isfinite(weight) or weight <= 0:
        raise ValueError("Calibration produced an invalid weight")
    return {"schema": "laya.frozen-action-weight.v1", "action_weight": weight,
            "basis": "shared_parameter_gradient_norm" if shared else "global_clip_budget_heuristic",
            "target_ratio": target_ratio, "unweighted_ratio_median": median_ratio,
            "weighted_ratio_median": weight * median_ratio, "records": len(rows),
            "usable_records": len(usable), "excluded_indices": excluded,
            "norm_floor": norm_floor, "provenance": dict(provenance)}
