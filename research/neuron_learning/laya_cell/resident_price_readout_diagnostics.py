"""Training-only geometry and constrained effective-readout controls."""
from __future__ import annotations
import math
import torch


def _checked(logits, labels=None):
    if not isinstance(logits, torch.Tensor) or logits.ndim != 2 or min(logits.shape) < 2:
        raise ValueError("nonempty multiclass matrix with at least two rows required")
    z = logits.detach().double()
    if not bool(torch.isfinite(z).all()):
        raise ValueError("finite logits required")
    if labels is None:
        return z, None
    y = torch.as_tensor(labels, device=z.device)
    if y.shape != z.shape[:1] or y.dtype not in (torch.int32, torch.int64):
        raise ValueError("integer class labels required")
    if not bool(((y >= 0) & (y < z.shape[1])).all()):
        raise ValueError("class index out of range")
    return z, y.long()


def option_metrics(logits, labels):
    """G is descriptive pairing gain, not a permutation-test significance."""
    z, y = _checked(logits, labels)
    centered = z-z.mean(-1, keepdim=True)
    mean = centered.mean(0)
    residual = centered-mean
    lse = torch.logsumexp(z, -1)
    nll = (lse-z.gather(1, y[:, None]).squeeze(1)).mean()
    p = z.softmax(-1)
    truth = torch.nn.functional.one_hot(y, z.shape[1]).to(z)
    gain = residual.gather(1, y[:, None]).mean()
    frequencies = truth.mean(0)
    shuffled_expectation = lse.mean()-(z.mean(0)*frequencies).sum()
    bias_energy = z.shape[0]*mean.square().sum()
    total_energy = centered.square().sum()
    return {
        "rows": z.shape[0], "classes": z.shape[1], "training_only": True,
        "nll": float(nll), "brier_class_sum": float((p-truth).square().sum(-1).mean()),
        "correct": int((z.argmax(-1)==y).sum()),
        "prediction_counts": torch.bincount(z.argmax(-1), minlength=z.shape[1]).tolist(),
        "target_counts": torch.bincount(y, minlength=z.shape[1]).tolist(),
        "mean_probability": p.mean(0).tolist(),
        "centered_option_mean": mean.tolist(),
        "residual_option_std": residual.std(0, unbiased=False).tolist(),
        "fixed_option_energy_fraction": float(bias_energy/total_energy) if float(total_energy)>0 else 0.,
        "conditional_pairing_gain": float(gain),
        "analytic_random_pair_nll": float(shuffled_expectation),
        "pairing_identity_residual": float(shuffled_expectation-nll-gain),
        "pairing_gain_is_not_independent_or_prospective_evidence": True,
    }


def frozen_option_offset(logits, prior, *, split):
    """Remove fixed option means and add a frozen training-pool log prior."""
    if split != "train":
        raise ValueError("training-only offset required")
    z, _ = _checked(logits)
    p = torch.as_tensor(prior, dtype=torch.float64, device=z.device)
    if p.shape != z.shape[1:] or not bool(torch.isfinite(p).all() and (p>0).all()):
        raise ValueError("positive finite prior for every class required")
    if abs(float(p.sum())-1.) > 1e-10:
        raise ValueError("prior must sum to one")
    option_mean = (z-z.mean(-1, keepdim=True)).mean(0)
    offset = p.log()-option_mean
    offset = offset-offset.mean()
    return offset, {
        "method": "REMOVE_TRAIN_OPTION_MEAN_ADD_FROZEN_TRAIN_POOL_LOG_PRIOR",
        "split": split, "rows": z.shape[0],
        "prior": p.tolist(), "original_centered_option_mean": option_mean.tolist(),
        "offset": offset.tolist(), "gauge": "SUM_ZERO",
        "fit_once_before_optimization": True, "individual_labels_used_in_offset_fit": False,
        "mean_probability_match_not_guaranteed": True,
        "extra_parameters_outside_original_adapter_profile": True,
    }


def unpermute_slots(slot_logits, order):
    if not isinstance(slot_logits, torch.Tensor) or slot_logits.ndim != 2:
        raise ValueError("slot logit matrix required")
    order = list(order)
    if any(type(v) is not int for v in order) or sorted(order) != list(range(slot_logits.shape[1])):
        raise ValueError("complete slot-to-canonical permutation required")
    inverse = torch.tensor(order, device=slot_logits.device).argsort()
    return slot_logits.index_select(-1, inverse)


def solve_shared_readout_ball(features, baseline_logits, labels, radius, *,
                              maximum_iterations=512, gap_tolerance=1e-7, deadline=None):
    """Projected gradient with a convex first-order optimality certificate.

    Only the final shared scalar readout is optimized; features and any
    external class offset stay fixed. A conservative global Lipschitz bound
    supplies the step size. FP64 is a research solver, not the resident dtype.
    """
    z, y = _checked(baseline_logits, labels)
    h = features.detach().to(device=z.device, dtype=torch.float64)
    if h.ndim != 3 or h.shape[:2] != z.shape or h.shape[2] == 0 or not bool(torch.isfinite(h).all()):
        raise ValueError("finite N by K by D features required")
    if not math.isfinite(radius) or radius <= 0 or not math.isfinite(gap_tolerance) or gap_tolerance <= 0:
        raise ValueError("positive finite radius and tolerance required")
    if type(maximum_iterations) is not int or maximum_iterations <= 0:
        raise ValueError("positive iteration limit required")
    h = h-h.mean(1, keepdim=True)
    z = z-z.mean(1, keepdim=True)
    flat = h.reshape(-1, h.shape[-1])
    truth = torch.nn.functional.one_hot(y, z.shape[-1]).to(z)
    lipschitz = float(.5*h.square().sum((1, 2)).mean())
    delta = torch.zeros(h.shape[-1], dtype=torch.float64, device=h.device)
    history = []
    with torch.no_grad():
        for iteration in range(maximum_iterations+1):
            if deadline is not None:
                deadline()
            values = z+(flat@delta).reshape(z.shape)
            loss = (torch.logsumexp(values, -1)-values.gather(1, y[:, None]).squeeze(1)).mean()
            gradient = flat.T@(values.softmax(-1)-truth).reshape(-1)/z.shape[0]
            gnorm = float(gradient.norm())
            gap = max(0., float(gradient@delta)+radius*gnorm)
            if iteration == 0:
                initial_loss = float(loss)
                initial_gradient_norm = gnorm
            history.append({"iteration": iteration, "nll": float(loss), "gap": gap,
                            "delta_l2": float(delta.norm()), "gradient_l2": gnorm})
            if gap <= gap_tolerance or iteration == maximum_iterations:
                break
            if lipschitz <= 0:
                raise RuntimeError("nonzero gradient with constant features")
            candidate = delta-gradient/lipschitz
            norm = float(candidate.norm())
            delta = candidate*(min(1., radius/norm) if norm else 1.)
    return delta, {
        "method": "PROJECTED_GRADIENT_SHARED_FINAL_VECTOR",
        "dtype": "float64", "training_only": True,
        "radius": radius, "maximum_iterations": maximum_iterations,
        "gap_tolerance": gap_tolerance, "iterations": iteration,
        "converged": gap <= gap_tolerance, "nll": float(loss),
        "initial_nll": initial_loss, "initial_gradient_l2": initial_gradient_norm,
        "initial_nll_improvement_upper_bound": radius*initial_gradient_norm,
        "nll_lower_bound_from_certificate": float(loss)-gap,
        "convex_gap": gap, "final_gradient_l2": gnorm,
        "delta_l2": float(delta.norm()), "lipschitz_upper_bound": lipschitz,
        "history": history, "common_feature_component_removed": True,
    }


def joint_price_gap_bounds(logits, labels, normalized_inputs, hidden,
                           final_first_weights, first_radius, last_radius):
    """Conservative finite-update bound, including the two-layer cross term.

    Conditional on fixed input and LayerNorm. First-layer edits affect only
    the selected hidden coordinates. GELU(approximate='none') is 1.13-Lipschitz.
    This is an effective-weight real-arithmetic bound, not a bound on arbitrary
    ill-conditioned floating-point factorizations.
    """
    z, y = _checked(logits, labels)
    x = normalized_inputs.detach().to(z)
    h = hidden.detach().to(z)
    w = final_first_weights.detach().to(z)
    if x.ndim != 3 or h.ndim != 3 or x.shape[:2] != z.shape or h.shape[:2] != z.shape:
        raise ValueError("feature shapes must match logits")
    if not all(bool(torch.isfinite(v).all()) for v in (x, h, w)):
        raise ValueError("finite features required")
    if any(not math.isfinite(v) or v < 0 for v in (first_radius, last_radius)):
        raise ValueError("nonnegative finite radii required")
    pred = z.argmax(-1)
    index = torch.arange(z.shape[0], device=z.device)
    gap = z[index, pred]-z[index, y]
    last = last_radius*(h[index, pred]-h[index, y]).norm(dim=-1)
    first = (float(w.norm())+last_radius)*1.13*first_radius*(
        x[index, pred].norm(dim=-1)+x[index, y].norm(dim=-1))
    bound = last+first
    wrong = pred != y
    return {
        "conditional_on_frozen_input_and_LayerNorm": True,
        "arithmetic": "REAL_EFFECTIVE_WEIGHTS_WITH_RECORDED_FP32_REPLAY_ERRORS",
        "gelu_lipschitz_upper_bound": 1.13, "cross_term_included": True,
        "first_radius": first_radius, "last_radius": last_radius,
        "wrong": int(wrong.sum()), "wrong_unfixable": int((wrong & (gap>bound)).sum()),
        "minimum_unfixable_remaining_gap": float((gap-bound)[wrong & (gap>bound)].min())
            if bool((wrong & (gap>bound)).any()) else None,
        "records": [{"target": int(y[i]), "prediction": int(pred[i]), "gap": float(gap[i]),
                     "last_layer_bound": float(last[i]), "first_layer_and_cross_bound": float(first[i]),
                     "total_bound": float(bound[i])} for i in range(z.shape[0])],
    }


def action_gradient_parts(features, action_logits, is_act):
    x = features.detach().double()
    z, _ = _checked(action_logits)
    y = torch.as_tensor(is_act, device=z.device, dtype=torch.float64)
    if x.ndim != 2 or x.shape[0] != z.shape[0] or z.shape[1] != 2 or y.shape != z.shape[:1]:
        raise ValueError("binary gradient shapes")
    if not bool(torch.isfinite(x).all() and ((y==0)|(y==1)).all()):
        raise ValueError("finite binary gradient inputs")
    r = z.softmax(-1)[:, 0]-y
    mean = x.mean(0)
    common = r.mean()*mean
    conditional = (r[:, None]*(x-mean)).mean(0)
    total = (r[:, None]*x).mean(0)
    per_row_mean_norm = (r[:, None]*x).norm(dim=-1).mean()
    return {"gradient_l2": float(total.norm()), "common_feature_gradient_l2": float(common.norm()),
            "conditional_feature_gradient_l2": float(conditional.norm()),
            "decomposition_error_l2": float((total-common-conditional).norm()),
            "residual_mean": float(r.mean()),
            "cancellation_ratio": float(total.norm()/per_row_mean_norm) if float(per_row_mean_norm)>0 else 0.,
            "common_conditional_cosine": float((common@conditional)/(common.norm()*conditional.norm()))
                if float(common.norm()*conditional.norm())>0 else None}
