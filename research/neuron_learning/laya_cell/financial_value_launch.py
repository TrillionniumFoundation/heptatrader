"""Enter unprivileged confinement before loading the GPU financial readout job."""
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from .integrity import read_json,ContractError
from .financial_value_service import validate_config
from .resident_store import atomic


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077)
 c=validate_config(read_json(a.config))
 if c.get('financial_worker_sandbox') is not True:raise ContractError('FINANCIAL_SANDBOX_REQUIRED')
 output=Path(c['job_output']).parent;output.mkdir(mode=0o700,parents=True,exist_ok=True)
 readonly=[c['value_runtime'],c['base_directory'],str(Path(c['python']).parent.parent),str(a.config),c['dataset_path'],c['native_binary']]
 for groups in c.get('decision_attribution_registrations',{}).values():
  for registration in groups:
   path=Path(registration['manifest']);readonly.append(str(path));readonly.append(read_json(path)['journal_directory'])
 for path in c.get('export_directories',{}).values():readonly.append(path)
 if c.get('export_directory'):readonly.append(c['export_directory'])
 from sandbox import enter
 result=enter({'output_dir':str(output),'period_path':c['period_path'],'legacy_capture':c['seed_dataset'],
   'oms_path':c['oms_path'],'export_dir':c['export_directory'],'read_only_paths':readonly})
 atomic(output/'sandbox.json',result)
 from .financial_value_train import run
 print(json.dumps(run(c),indent=2))
if __name__=='__main__':main()
