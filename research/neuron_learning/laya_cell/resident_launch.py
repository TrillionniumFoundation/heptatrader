"""Enter inherited unprivileged confinement before importing torch or the worker.

Only the scheduler checks the old service; children cannot contact its bus or any
trading endpoint. Landlock confines files and seccomp denies new network sockets.
"""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from .integrity import read_json,sha_file,ContractError
from .resident import config_check,bootstrap_still_off
from .resident_store import atomic


def main():
 p=argparse.ArgumentParser();p.add_argument('target',choices=['train','predict']);p.add_argument('--config',type=Path,required=True)
 p.add_argument('--plan',type=Path);p.add_argument('--slot',type=int);a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);base=dict(c);base['schema']='heptatrader.laya-resident-config.v1';config_check(base);bootstrap_still_off()
 if c.get('require_worker_sandbox') is not True:raise ContractError('SANDBOX_REQUIRED')
 if a.target=='train':
  output=Path(c['output_directory']).parent
 else:
  if a.plan is None or a.slot is None:raise ContractError('PREDICT_SLOT_REQUIRED')
  output=a.plan.parent
 output.mkdir(mode=0o700,parents=True,exist_ok=True)
 readonly=[c['base_directory'],str(Path(c['python']).parent.parent),c['baseline_directory'],c['feed_directory'],
  c['initial_parent_directory'],c['native_binary'],c['seed_dataset'],c['runtime_directory'],str(a.config),
  str(Path.home()/'.local/state/heptatrader/paper-driver/cost-observations.jsonl')]
 readonly.extend(c.get('completed_history_sources',[]))
 if a.target=='train':readonly.extend([c['dataset'],c['parent_directory']])
 else:
  plan=read_json(a.plan);readonly.extend([str(a.plan),plan['candidate_directory'],plan['parent_directory']])
 from . import resident_training_v5 as training_v5
 from . import resident_training_v6 as training_v6
 if a.target=='train' and (training_v5.enabled(c.get('objective')) or training_v6.enabled(c.get('objective'))):
  # Original closed evidence is read-only. Broker sockets/network and every
  # write path retain the same confinement as previous worker versions.
  readonly.extend([c['closed_action_source'],c['evaluation_directory']])
 sandbox_config={'output_dir':str(output),'period_path':c['period_path'],'legacy_capture':str(Path.home()/'.local/state/heptatrader/paper-driver/cost-observations.jsonl'),
  'oms_path':c['oms_path'],'export_dir':c['export_directory'],'read_only_paths':readonly}
 from sandbox import enter
 receipt=enter(sandbox_config);receipt['target']=a.target
 atomic(output/('sandbox-train.json' if a.target=='train' else f'sandbox-predict-{a.slot:03}.json'),receipt)
 import torch
 torch.set_num_threads(2)
 if a.target=='train':
  from .resident_train import run_job
  print(json.dumps(run_job(c,old_learner_verified=True),indent=2))
 else:
  from .resident_predict import predict
  predict(c,a.plan,a.slot)
if __name__=='__main__':main()
