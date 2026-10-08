"""Sandboxed entrypoint for isolated paired-price predictor/evaluator."""
from __future__ import annotations
import argparse,importlib,os,sys
from pathlib import Path
from laya_cell.integrity import ContractError,read_json,sha_file
from laya_cell.resident_store import atomic
from .common import require,check_plan,check_candidate,utc_ms

def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--mode',choices=('predictor','evaluator'),required=True);a=p.parse_args()
 os.umask(0o077);c=read_json(a.config);root=Path(c['shadow_root'])
 require(c['automatic_trading'] is False and sha_file(Path(c['period_path']))==c['period_sha256'],'ISOLATED_PAPER_ONLY')
 plan=check_plan(read_json(root/'plan.json'))
 candidate=check_candidate(c['candidate_directory'])
 require(plan['candidate_id']==candidate['candidate_id'],'CANDIDATE_PLAN_CHANGED')
 for name,expected in c['runtime_sha256'].items():
  require(sha_file(Path(c['runtime_directory'])/name)==expected,'RUNTIME_DIGEST_CHANGED')
 reads=[c['runtime_directory'],c['base_directory'],c['candidate_directory'],c['fusion_candidate_directory'],
  c['feed_directory'],c['period_path'],c['seed_dataset'],str(Path(c['python']).parent.parent),str(a.config)]
 from sandbox import enter
 proof=enter({'output_dir':str(root),'period_path':c['period_path'],'legacy_capture':c['seed_dataset'],
   'oms_path':c['oms_path'],'export_dir':c['export_directory'],'read_only_paths':reads})
 proof.update(role=a.mode,broker_orders_sent=0,trading_authority=False)
 atomic(root/('sandbox-'+a.mode+'.json'),proof)
 module={'predictor':'predict','evaluator':'evaluate'}[a.mode]
 sys.argv=['paired_price.'+module,'--config',str(a.config)]
 importlib.import_module('.'+module,'paired_price').main()
if __name__=='__main__':main()
