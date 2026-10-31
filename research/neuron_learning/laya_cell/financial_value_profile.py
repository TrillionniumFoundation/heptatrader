"""Explicit opt-in readout contract; legacy frozen heads keep their semantics."""
from .integrity import ContractError

CENTERED_RESIDUAL_PROFILE = {
    'schema': 'heptatrader.current100k-centered-value-readout.v1',
    'feature_width': 768,
    'feature_transform': 'FROZEN_TRAIN_MEAN_RMS_CENTERING_UNIT_BALL',
    'minimum_feature_rms': 1e-6,
    'baseline': 'FROZEN_TRAIN_MEAN_CLAMPED_TO_OUTPUT_BOUND',
    'optimizer': 'SGD', 'learning_rate': .05, 'steps': 16,
    'gradient_l2_bound': 1., 'residual_parameter_l2_bound': .1,
    'target_scale_usd': 100., 'max_abs_prediction_usd': 15.,
    'future_refit_allowed': False,
}


def check_readout_profile(value):
    if value is not None and value != CENTERED_RESIDUAL_PROFILE:
        raise ContractError('VALUE_READOUT_PROFILE_CHANGED')
    return value
