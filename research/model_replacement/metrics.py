"""Seven-class proper scores; calibration fits a separate chronological block."""
import numpy as np
from .data import need

FLOOR = 1e-12

def probabilities(p):
    p = np.asarray(p, dtype=np.float64)
    need(p.ndim == 2 and len(p) and p.shape[1] == 7, 'SEVEN_PROBABILITIES_REQUIRED')
    need(np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all()
         and np.allclose(p.sum(1), 1, rtol=0, atol=1e-8), 'PROBABILITY_SIMPLEX')
    return p

def targets(y, n):
    y = np.asarray(y)
    need(y.shape == (n,) and np.issubdtype(y.dtype, np.integer)
         and (y >= 0).all() and (y < 7).all(), 'SEVEN_CLASS_TARGETS')
    return y

def softmax(z):
    z = np.asarray(z, dtype=np.float64)
    e = np.exp(z-z.max(1, keepdims=True))
    return e/e.sum(1, keepdims=True)

def score(p, y):
    p = probabilities(p); y = targets(y, len(p)); truth = np.eye(7)[y]
    hit = p[np.arange(len(y)), y]; zero = int((hit == 0).sum())
    return {'rows': len(y), 'mean_brier': float(((p-truth)**2).sum(1).mean()),
            'mean_nll': None if zero else float(-np.log(hit).mean()),
            'nll_is_infinite': bool(zero), 'zero_target_probability_rows': zero,
            'mean_clipped_nll': float(-np.log(np.maximum(hit, FLOOR)).mean()),
            'clipping_floor': FLOOR, 'accuracy': float((p.argmax(1) == y).mean()),
            'class_counts': np.bincount(y, minlength=7).tolist(),
            'mean_probability_by_class': p.mean(0).tolist(),
            'brier_by_class': ((p-truth)**2).mean(0).tolist(),
            'mean_ranked_probability_score': float(((p.cumsum(1)[:,:-1]-truth.cumsum(1)[:,:-1])**2).sum(1).mean()/6)}

def apply_temperature(p, temperature):
    need(np.isfinite(temperature) and .25 <= temperature <= 4, 'TEMPERATURE_BOUND')
    return softmax(np.log(np.maximum(probabilities(p), FLOOR))/temperature)

def fit_temperature(p, y):
    p = probabilities(p); y = targets(y, len(p)); best = (float('inf'), 0, 1.)
    grid = sorted(set([1.]+np.clip(np.exp(np.linspace(np.log(.25), np.log(4), 81)), .25, 4).tolist()))
    for t in grid:
        q = apply_temperature(p, t)
        key = (float(-np.log(q[np.arange(len(y)),y]).mean()), abs(np.log(t)), t)
        if key < best: best = key
    return {'kind':'BOUNDED_SINGLE_TEMPERATURE', 'temperature':best[2], 'fit_rows':len(y),
            'class_counts':np.bincount(y,minlength=7).tolist(), 'calibration_nll':best[0],
            'objective':'CALIBRATION_PARTITION_NLL', 'test_used':False, 'probability_floor':FLOOR}
