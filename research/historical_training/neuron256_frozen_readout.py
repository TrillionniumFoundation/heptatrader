"""Read-only CPU inference for the original archived 256-D graph checkpoint.
No optimizer or new head is created. The original 3-class encoder PROMPT is
retained because the stored experts were fitted to that representation; their
forecast output/target remains seven classes, not the old campaign's 3 labels.
"""
from pathlib import Path
from .common import EvidenceError,read,sha


def from_vectors(checkpoint,rows,raw,prior,rho,device="cpu"):
 import numpy as np
 import torch
 from torch.nn import functional as F
 from safetensors.torch import load_file
 from neuron_graph_v2.archive import ArchivedGraph
 if type(rho) not in (int,float) or not 0<=rho<=1:raise EvidenceError('RESIDUAL_RHO')
 torch.set_num_threads(2)
 z=load_file(str(checkpoint),device='cpu' if device=='cpu' else 'cuda:0')
 x=torch.tensor([r['features'] for r in rows],dtype=torch.float32,device=device)
 raw=torch.tensor(np.asarray(raw),dtype=torch.float32,device=device)
 if x.shape!=(len(rows),25) or raw.shape!=(len(rows),768) or not torch.isfinite(raw).all():raise EvidenceError('FROZEN_READOUT_INPUT')
 with torch.inference_mode():
  semantic=F.normalize(raw@z['semantic_projection'],dim=-1)
  normalized=[(x-z['mean0'])/z['std0'],(semantic[:,:64]-z['mean1'])/z['std1']]
  probs=torch.stack([F.linear(v,z[f'expert{i}.weight'],z[f'expert{i}.bias']).softmax(-1) for i,v in enumerate(normalized)],dim=1)
  embedded=torch.stack([F.normalize(F.pad(normalized[0],(0,231)),dim=-1),semantic],dim=1)
  graph=ArchivedGraph().to(device);graph.load_state_dict({k[6:]:v for k,v in z.items() if k.startswith('graph.')},strict=True);graph.requires_grad_(False).eval()
  gp=graph.archived(embedded,probs)['probabilities'].cpu().numpy().astype(np.float64)
  pp=np.broadcast_to(np.asarray(prior,dtype=np.float64),gp.shape)
  result={'candidate':((1-rho)*pp+rho*gp).tolist(),'graph':gp.tolist(),'prior':pp.tolist(),
          'numeric':probs[:,0].cpu().numpy().astype(np.float64).tolist(),'equal_mix':probs.mean(1).cpu().numpy().astype(np.float64).tolist()}
 return result


def encode_cpu(base,rows,asset):
 import numpy as np
 import torch
 from torch.nn import functional as F
 from safetensors.torch import load_file
 from laya.common import build_model
 from transformers import AutoTokenizer
 from .matched_data import text
 from .neuron256_cpu_custody import BACKEND
 # No silent fallback to a ROCm wheel's CPU kernels. The archived vectors
 # came from this exact frozen CPU backend, confirmed byte-for-byte on 4 rows.
 if (torch.__version__!=BACKEND or torch.version.hip is not None
     or torch.version.cuda is not None or torch.cuda.is_available()):
  raise EvidenceError('CANONICAL_CPU_RUNTIME_REQUIRED_NO_FALLBACK')
 base=Path(base)
 if sha(base/'model.safetensors')!=asset['model_file_sha256']:raise EvidenceError('FROZEN_ENCODER_BYTES')
 if sha(base/'tokenizer/tokenizer.json')!=asset['tokenizer_file_sha256']:raise EvidenceError('FROZEN_TOKENIZER_BYTES')
 torch.set_num_threads(2)
 model=build_model(read(base/'rl_agent_config.json'),encoder_dir=str(base/'encoder'),pretrained=False)
 model.load_state_dict(load_file(str(base/'model.safetensors'),device='cpu'),strict=True)
 model.requires_grad_(False).float().eval()
 tok=AutoTokenizer.from_pretrained(base/'tokenizer',local_files_only=True,trust_remote_code=False)
 texts=[text(row) for row in rows];vectors=[]
 for t in texts:
  b=tok([t],return_tensors='pt',padding=True,truncation=False)
  if int(b['attention_mask'].sum())>1024:raise EvidenceError('EXACT_PROMPT_NO_TRUNCATION')
  inputs={k:v for k,v in b.items() if k in ('input_ids','attention_mask')}
  with torch.inference_mode():
   hidden=model.encoder(**inputs).last_hidden_state;mask=inputs['attention_mask'].unsqueeze(-1).to(hidden.dtype)
   vectors.append(F.normalize((hidden*mask).sum(1)/mask.sum(1),dim=-1).numpy())
 return np.concatenate(vectors),texts
