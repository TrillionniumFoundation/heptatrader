"""Offline proposal: prevent saturated, imbalanced auxiliary action loss
from dominating the primary future-price objective.

This module is NOT imported by the installed resident trainer. Research only.
An admission decision for a new on-device training objective needs new
out-of-sample evaluation, not automatic replacement of existing candidates.
"""
from __future__ import annotations
import torch
from .integrity import ContractError
from .resident_model import action_loss


def bounded_auxiliary_term(price_loss, logits, targets, whole_training_targets,
                           base_weight=.01, max_primary_fraction=.25,
                           min_per_class=4):
    """Return additive auxiliary term and per-batch diagnostic.

    The imbalance gate uses only labels already in the training sample.
    The cap is detached from the CE gradient: no gradient-shrinking feedback
    through the price objective. Both losses retain their original logits.
    """
    if (price_loss.ndim!=0 or not torch.isfinite(price_loss)
        or float(price_loss.detach())<0 or
        whole_training_targets.ndim!=1 or targets.ndim!=1 or logits.ndim!=2):
        raise ContractError('AUXILIARY_OBJECTIVE_SHAPE')
    if (type(min_per_class) is not int or not 2<=min_per_class<=32
        or not 0<base_weight<=.05 or not 0<max_primary_fraction<=.5):
        raise ContractError('AUXILIARY_OBJECTIVE_BOUNDS')
    if (not ((whole_training_targets==0)|(whole_training_targets==1)).all()
        or not ((targets==0)|(targets==1)).all()):
        raise ContractError('AUXILIARY_ACTION_CLASSES')
    counts=[int((whole_training_targets==i).sum()) for i in (0,1)]
    if min(counts)<min_per_class or len(targets)==0:
        return price_loss*0, {'admitted':False,'reason':'TRAIN_ACTION_CLASS_IMBALANCE',
                              'counts':counts,'effective_weight':0.}
    ce=action_loss(logits,targets)
    max_term=max_primary_fraction*price_loss.detach()
    effective=torch.minimum(torch.as_tensor(base_weight,device=ce.device,dtype=ce.dtype),
                            max_term/(ce.detach()+1e-12))
    contribution=effective*ce
    return contribution, {'admitted':True,'reason':'BOUNDED_AUXILIARY',
                          'counts':counts,'effective_weight':float(effective.detach()),
                          'price_loss':float(price_loss.detach()),
                          'raw_action_loss':float(ce.detach()),
                          'auxiliary_fraction':float(contribution.detach()/(price_loss.detach()+1e-12))}
