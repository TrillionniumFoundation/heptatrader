"""Explicit archived tensor training adapter for the unchanged 256-D graph.
This emits NO live NeuronOutput, availability timestamps, or execution authority.
Real input provenance/time separation must be checked by its experiment loader.
Only soft routing with two actual 7-class forecast experts is supported here.
"""
from __future__ import annotations
import math
import torch
from torch.nn import functional as F
from .graph import GraphMetaNeuron
from .contracts import ContractError,KINDS

class ArchivedGraph(GraphMetaNeuron):
    def __init__(self):
        super().__init__(mode='soft',top_k=2,message_rounds=2)

    def archived(self,embeddings,probabilities):
        if (embeddings.ndim!=3 or embeddings.shape[1:]!=(2,256)
            or probabilities.shape!=(embeddings.shape[0],2,7)
            or not 1<=embeddings.shape[0]<=2048):
            raise ContractError('ARCHIVED_TENSOR_SHAPE_OR_BOUND')
        if not torch.isfinite(embeddings).all() or not torch.isfinite(probabilities).all():
            raise ContractError('ARCHIVED_NONFINITE')
        if ((probabilities<0).any() or (probabilities>1).any()
            or not torch.allclose(probabilities.sum(-1),torch.ones_like(probabilities.sum(-1)),atol=1e-6)):
            raise ContractError('ARCHIVED_PROBABILITY_SIMPLEX')
        if not torch.allclose(torch.linalg.vector_norm(embeddings,dim=-1),torch.ones_like(embeddings[...,0]),atol=1e-4):
            raise ContractError('ARCHIVED_EMBEDDING_NORM')
        # Caller order is numeric-price, then multilingual-semantic; not routing rank.
        names=sorted(KINDS);k=torch.tensor([names.index('price'),names.index('semantic')],device=embeddings.device)
        uncertainty=-(probabilities.clamp_min(1e-30).log()*probabilities).sum(-1)/math.log(7)
        extra=torch.stack((uncertainty,torch.ones_like(uncertainty)),dim=-1)
        h=self.input(embeddings)+self.kinds(k)
        scores=self.router(torch.cat((h,extra),dim=-1)).squeeze(-1)
        z=h
        for norm in self.norms:
            attention=(self.query(z)@self.key(z).transpose(-1,-2)/math.sqrt(z.shape[-1])).softmax(-1)
            z=norm(z+attention@self.value(z))
        price_weights=(scores+self.weight_head(z).squeeze(-1)).softmax(-1)
        result=(price_weights.unsqueeze(-1)*probabilities).sum(-2)
        latent=(scores.softmax(-1).unsqueeze(-1)*z).sum(-2)
        embedding=F.normalize(self.to_output(latent),dim=-1)
        if not torch.isfinite(result).all() or not torch.isfinite(embedding).all():
            raise ContractError('ARCHIVED_NONFINITE_OUTPUT')
        return {'probabilities':result,'embedding':embedding,'forecast_weights':price_weights,
                'archive_training_only':True,'live_packet_emitted':False,'effect_authority':False}
