"""Constrain each independent forward role before importing torch or signing.

Exactly one local witness key is readable per role; other role keys and trading
bearers are explicitly denied. These are local witnesses, not Agentd credentials.
"""
from __future__ import annotations
import argparse,importlib,os,sys
from pathlib import Path
from .integrity import read_json,sha_file,ContractError
_original_sha_file=sha_file
def sha_file(path):return _original_sha_file(Path(path))
from .resident_store import atomic


def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();os.umask(0o077)
 c=read_json(a.config);role=c['signing_role'];root=Path(c['forward_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
 if role not in ('registrar','predictor','evaluator','selector'):raise ContractError('UNKNOWN_FORWARD_ROLE')
 for name,expected in c['forward_runtime_sha256'].items():
  if Path(name).is_absolute() or '..' in Path(name).parts or sha_file(Path(c['forward_runtime'])/name)!=expected:
   raise ContractError('FORWARD_RUNTIME_CHANGED')
 if sha_file(c['period_path'])!=c['period_sha256']:raise ContractError('PERIOD_CHANGED')
 reads=[c['forward_runtime'],c['base_directory'],str(Path(c['python']).parent.parent),c['value_output_root'],
        c['witness_trust_path'],c['role_key_path'],str(a.config),c['canonical_verifier_binary']]
 for regs in c['decision_attribution_registrations'].values():
  for reg in regs:
   reads.append(reg['manifest']);m=read_json(Path(reg['manifest']));reads.append(m['journal_directory'])
 if c.get('canonical_owner_registration'):
  reads.append(c['canonical_owner_registration']['path'])
  # Only independently registered owner output directories, never arbitrary data-supplied paths.
  reads.extend(c.get('canonical_owner_readonly_paths',[]))
 from sandbox import enter
 receipt=enter({'output_dir':str(root),'period_path':c['period_path'],'legacy_capture':c['seed_dataset'],
  'oms_path':c['oms_path'],'export_dir':c['export_directory'],'read_only_paths':reads})
 denied={}
 for other,path in c['role_key_paths'].items():
  if other==role:continue
  try:fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC);os.close(fd)
  except PermissionError:denied[other]=True
  else:raise ContractError('OTHER_ROLE_KEY_READABLE:'+other)
 receipt.update(role=role,other_role_keys_denied=denied,canonical_owner_key_access=False)
 atomic(root/('sandbox-'+role+'.json'),receipt)
 module={'registrar':'financial_forward_register','predictor':'financial_forward_predict','evaluator':'financial_forward_evaluate','selector':'financial_forward_select'}[role]
 sys.argv=[module,'--config',str(a.config)];importlib.import_module('.'+module,'laya_cell').main()
if __name__=='__main__':main()
