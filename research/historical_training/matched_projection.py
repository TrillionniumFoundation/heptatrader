"""Projection specific to the predeclared 64-coordinate matched experiment.
The existing current100k 256-coordinate API keeps its own contract unchanged.
"""
from __future__ import annotations
from .common import EvidenceError


def validate_projection(width,destination):
    if type(width) is not int or width not in (768,1024):raise EvidenceError('MATCHED_ENCODER_WIDTH')
    if type(destination) is not int or destination!=64:raise EvidenceError('MATCHED_FIXED64_REQUIRED')


def project_fixed64(features,seed,destination=64):
    import torch
    from torch.nn import functional as F
    if features.ndim!=2:raise EvidenceError('MATCHED_FEATURE_RANK')
    validate_projection(int(features.shape[1]),destination)
    if features.device.type!='cpu' or type(seed) is not int or not torch.isfinite(features).all():
        raise EvidenceError('MATCHED_CPU_FINITE_SEEDED_INPUT_REQUIRED')
    generator=torch.Generator(device='cpu').manual_seed(seed)
    matrix=torch.randn(features.shape[1],64,generator=generator,dtype=torch.float32)
    matrix=F.normalize(matrix,dim=0)
    projected=F.normalize(features.float()@matrix,dim=-1)
    if projected.shape!=(features.shape[0],64) or not torch.isfinite(projected).all():
        raise EvidenceError('MATCHED_PROJECTION_INVALID')
    return projected.contiguous()
