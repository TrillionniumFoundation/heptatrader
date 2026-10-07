"""Actual 890M kernel/autograd check using explicitly SYNTHETIC node fixtures.

This does not train market models or claim trading/evaluation improvement.
"""
from pathlib import Path
import argparse,hashlib,json,time,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import torch
from neuron_graph_v2.graph import GraphMetaNeuron
from neuron_graph_v2.tests.test_neuron256 import graph_nodes,T


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if not torch.cuda.is_available() or not torch.version.hip:raise RuntimeError('ROCM_GPU_REQUIRED')
 arch=str(torch.cuda.get_device_properties(0).gcnArchName)
 if not arch.startswith('gfx1150'):raise RuntimeError('POCKET4_890M_REQUIRED')
 torch.set_num_threads(1);torch.manual_seed(20261008)
 cases=[]
 for mode in ('soft','top_k'):
  model=GraphMetaNeuron(mode,top_k=3).to('cuda');optimizer=torch.optim.SGD(model.parameters(),lr=.001)
  initial={n:p.detach().cpu().clone() for n,p in model.named_parameters()}
  torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();start=time.monotonic()
  for _ in range(3):
   optimizer.zero_grad(set_to_none=True);r=model(graph_nodes(),T+300)
   loss=-r['probabilities'][3].log()+.01*r['embedding'][0]
   loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);optimizer.step()
  torch.cuda.synchronize();elapsed=time.monotonic()-start
  changed=sum(int(torch.count_nonzero(p.detach().cpu()-initial[n])) for n,p in model.named_parameters())
  if not changed or len(r['embedding'])!=256 or not torch.isfinite(loss):raise RuntimeError('GPU_AUTOGRAD_CHECK_FAILED')
  cases.append({'mode':mode,'synthetic_fixture_only':True,'steps':3,'trainable_parameters':sum(p.numel() for p in model.parameters()),
    'changed_coefficients':changed,'selected_nodes':r['selected_node_ids'],'forecast_nodes':r['forecast_node_ids'],
    'embedding_dim':r['embedding'].numel(),'graph_rounds':len(r['graph_attention']),
    'elapsed_seconds':elapsed,'peak_allocated_gpu_bytes':torch.cuda.max_memory_allocated(),
    'market_model_trained':False,'efficacy_evidence':False,'effect_authority':False})
 report={'schema':'heptatrader.neuron256.gpu-kernel-proof.v1','gpu_arch':arch,'torch':torch.__version__,'hip':torch.version.hip,
  'cases':cases,'orders_sent':0,'services_changed':False,'candidate_selected':False}
 a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open('x') as f:f.write(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
