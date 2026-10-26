"""Real selected-Laya inference callback for an admitted, confined worker.

This is a model worker, not a scheduler or a trading policy. The owner controller
revalidates CURRENT after this callback and before releasing its output.
"""
from __future__ import annotations

import time
from pathlib import Path

from .integrity import ContractError, digest, read_json
from .resident_owner import validate_payload


def predict_payload(config, raw_payload, request):
    # Validate all selected bytes/profiles before importing a GPU dependency.
    payload, adapter = validate_payload(raw_payload)
    candidate = payload['candidate']; plan = payload['plan']
    if (request['period_sha256'] != config['period_sha256'] or plan['scope'] != config['period_sha256']
            or request['horizon_sec'] != 300 or int(time.time() * 1000) >= request['expires_at_ms']):
        raise ContractError('LAYA_SELECTED_REQUEST_SCOPE_OR_EXPIRY')
    base_manifest = read_json(Path(config['base_directory']) / 'base-manifest.json')
    if base_manifest != payload['base_manifest']:
        raise ContractError('LAYA_SELECTED_BASE_CHANGED')
    import torch
    from safetensors.torch import load
    from .model import load_verified_laya
    from .resident_model import ResidentDecisionCell
    from .resident_predict import _prediction_batches
    from .resident_data import forecast_input
    from .resident_source_policy import declared_profile, read_for_future_plan
    from .dataset import verify_source_prefixes
    from . import resident_price_input as price_input
    from . import resident_price_calibration as price_calibration
    from . import resident_training_v2 as action_policy

    expected_source = declared_profile(config, plan)
    model, tokenizer, base_id = load_verified_laya(Path(config['base_directory']), base_manifest)
    if base_id != candidate['base_id']:
        raise ContractError('LAYA_SELECTED_BASE_IDENTITY')
    cell = ResidentDecisionCell(model, base_id)
    if cell.profile != candidate['profile']:
        raise ContractError('LAYA_SELECTED_MODEL_PROFILE_CHANGED')
    cell.restore_trainable(load(adapter)); cell.eval()
    now = int(time.time() * 1000)
    if now >= request['expires_at_ms']:
        raise ContractError('LAYA_SELECTED_REQUEST_EXPIRED_DURING_LOAD')
    quotes, prefixes, source = read_for_future_plan(config, plan, now)
    if source != expected_source:
        raise ContractError('LAYA_SELECTED_SOURCE_CHANGED')
    sample, row = forecast_input(config, quotes, int(time.time() * 1000))
    verify_source_prefixes({'source_prefixes': prefixes, 'scope': config['period_sha256'] + ':EUR.USD'})
    if candidate['created_at_ms'] >= sample['anchor_ms'] or candidate['trained_until_ms'] > sample['anchor_ms']:
        raise ContractError('LAYA_SELECTED_MODEL_LOOKAHEAD')
    raw_batch, _, price_batch = _prediction_batches(tokenizer, row, price_input.check_bindings(candidate))
    with torch.no_grad():
        logits, action_logits = cell(**price_batch)
        if price_batch is not raw_batch:
            action_logits = cell(**raw_batch)[1]
        raw = logits.softmax(-1)[0].cpu().tolist()
        prices = price_calibration.apply(candidate['price_calibration'], raw)
        action = action_policy.action_inference(action_logits, candidate['action_training_profile'])
    cell.assert_frozen()
    return {'schema': 'heptatrader.laya-selected-price-output.v1',
        'request_id': request['request_id'], 'request_digest': digest(request),
        'candidate_id': candidate['candidate_id'], 'base_id': base_id,
        'input_id': digest(row['state']), 'sample_id': sample['sample_id'],
        'source_prefixes_digest': digest(prefixes), 'source_read_profile': source,
        'anchor_ms': sample['anchor_ms'], 'created_at_ms': int(time.time() * 1000),
        'trained_until_ms': candidate['trained_until_ms'], 'horizon_sec': 300,
        'price_probs': prices, 'raw_price_probs': raw, 'action_correct_probs': action['action_correct_probs'],
        'action_output_profile': candidate['action_output_profile'], 'price_output_profile': candidate['price_output_profile'],
        'action_head_is_trade_permission': False, 'broker_orders_sent': 0}
