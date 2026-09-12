#!/usr/bin/env python3
"""Reviewable PAPER-V4 orchestration; privileged custody stays in a host driver.

The driver must implement the versioned protocol in ib-paper-host-driver.md.
It may expose only the canonical Gateway/Execution path, not an alternate order
API. This program neither logs in to a broker nor opens/disarms a trading session.
There is deliberately no synthetic-evidence or auto-retry mutation CLI mode.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import uuid

from hepta_evidence_io import EvidenceError, canonical_bytes, load_json, read_bytes, write_json, sha256_file
from verify_ib_paper_rollout import (load_campaign, verify, _barrier, _stage_policy, POLICY,
                                     require, validate_binding)


class PinnedDriver:
    def __init__(self,path:Path,digest:str,root:Path,timeout:int=120):
        metadata=path.lstat()
        require(not path.is_symlink() and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink==1,
                'host driver must be a regular single-link file')
        require(metadata.st_uid==0 and not metadata.st_mode & 0o022,
                'host driver must be root-owned and non-writable by callers')
        require(sha256_file(path)==digest,'host driver digest mismatch')
        self.path,self.digest,self.root,self.timeout=path,digest,root,timeout

    def call(self,operation,payload):
        # Every call has an immutable correlation ID and bounded I/O. A timeout
        # raises; it never repeats an order or substitutes a fresh command ID.
        require(sha256_file(self.path)==self.digest,'host driver changed')
        request_id=uuid.uuid4().hex
        request=dict(schema='heptatrader.paper-host-request.v1',request_id=request_id,
                     operation=operation,payload=payload)
        # Typed requests/responses are non-secret evidence. Keep them even when
        # the host process times out or returns an invalid response.
        root=self.root/'driver-calls'/request_id
        root.mkdir(parents=True,mode=0o700)
        request_path=root/'request.json'; response_path=root/'response.json'
        write_json(request_path,request)
        completed=subprocess.run([str(self.path),'--request',str(request_path),'--response',str(response_path)],
                                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':str(root)},timeout=self.timeout)
        require(completed.returncode==0,'host driver failed; outcome may be uncertain')
        response=load_json(response_path)
        require(isinstance(response,dict) and set(response)=={'schema','request_id','operation','result'},
                'invalid driver response')
        require(response['schema']=='heptatrader.paper-host-response.v1' and
                response['request_id']==request_id and response['operation']==operation,'driver response correlation mismatch')
        return response['result']



def write_evidence(root,binding,stage,cycles,journal,callbacks):
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    snapshots=dict(schema='heptatrader.rollout-snapshots.v1',binding=binding,cycles=cycles)
    write_json(root/'snapshot.json',snapshots,replace=True)
    for name,rows in [('journal.jsonl',journal),('callbacks.jsonl',callbacks)]:
        # Immutable per-stage publication occurs on successful verification; in
        # flight, replace complete snapshots atomically instead of half records.
        path=root/name
        payload=b''.join(canonical_bytes(row) for row in rows)
        fd,tmp=tempfile.mkstemp(dir=root,prefix='.records-')
        with os.fdopen(fd,'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
    evidence=[]
    for kind,name in [('authoritative-snapshot','snapshot.json'),('oms-journal','journal.jsonl'),('broker-callbacks','callbacks.jsonl')]:
        path=root/name
        evidence.append(dict(kind=kind,path=name,sha256=sha256_file(path),size=path.stat().st_size))
    result=dict(schema='heptatrader.ib-paper-rollout-result.v2',binding=binding,stage=stage,account_mode='PAPER',
                profile_order_mode='EXTERNAL_P1_CANARY_LMT_DAY',mutation_cycles=len(cycles),
                successful_round_trips=len(cycles),final_active_orders=0,final_uncertain_commands=0,
                final_position_quantity=0,authoritative_reconciliation_complete=True,live_authorized=False,evidence=evidence)
    write_json(root/'rollout-result.json',result,replace=True)


def run(driver,campaign_path,binary,harness,stage,evidence):
    campaign=load_campaign(campaign_path); binding=campaign['binding']
    require(sha256_file(binary)==binding['binary_sha256'],'candidate digest mismatch')
    require(sha256_file(harness)==binding['harness_sha256'],'harness digest mismatch')
    require(driver.digest==binding['driver_sha256'],'driver identity differs from admitted campaign')
    policy=_stage_policy(load_json(POLICY),stage)
    inspection=driver.call('inspect',dict(binding=binding,binary=str(binary)))
    require(isinstance(inspection,dict) and inspection.get('binding')==binding and
            inspection.get('account_mode')=='PAPER' and inspection.get('profile_order_mode')=='EXTERNAL_P1_CANARY_LMT_DAY' and
            inspection.get('network_isolated') is True and inspection.get('credential_isolated') is True,
            'target host/profile/account is not the admitted PAPER boundary')
    evidence.mkdir(parents=True,exist_ok=True,mode=0o700)
    cycles=[]; journal=[]; callbacks=[]
    for index in range(policy['min_mutation_cycles']):
        cid=f'{stage}-{index}-{uuid.uuid4().hex}'
        before=driver.call('barrier',dict(binding=binding))
        _barrier(before,binding,campaign['created_at_ms'])
        cycle=dict(cycle_id=cid,before=before,after=None)
        cycle_journal=[]; cycle_callbacks=[]
        for operation in ('place','flatten'):
            command=f'{cid}-{operation}'
            intent=dict(binding=binding,cycle_id=cid,command_id=command,instrument=binding['instrument'])
            if operation=='place': intent.update(side='BUY',quantity=1,order_type='LMT',tif='DAY')
            # This local intent is diagnostic, not a substitute for the durable
            # EXECUTION journal, which the driver must return independently.
            write_json(evidence/(command+'.intent.json'),dict(operation=operation,request=intent))
            admitted=driver.call(operation,intent)
            require(isinstance(admitted,dict) and admitted.get('command_id')==command and
                    admitted.get('status')=='accepted','mutation not proven accepted; stop without resending')
            terminal=driver.call('await_terminal',dict(binding=binding,cycle_id=cid,command_id=command))
            require(isinstance(terminal,dict) and terminal.get('command_id')==command and
                    terminal.get('status')=='terminal','terminal outcome unresolved; no subsequent mutation')
            require(isinstance(terminal.get('journal'),list) and isinstance(terminal.get('callbacks'),list),
                    'driver omitted durable/Broker evidence')
            cycle_journal.extend(terminal['journal']); cycle_callbacks.extend(terminal['callbacks'])
            # Keep every completed leg even if a later operation fails.
            write_json(evidence/(command+'.observations.json'),terminal)
        cycle['after']=driver.call('barrier',dict(binding=binding))
        _barrier(cycle['after'],binding,campaign['created_at_ms'])
        # Falsify this individual cycle BEFORE authorizing another one. The
        # canary policy is exactly one cycle with the same instantaneous limits.
        with tempfile.TemporaryDirectory(prefix='.cycle-check-',dir=evidence) as tmp:
            check=Path(tmp)
            write_evidence(check,binding,'canary',[cycle],cycle_journal,cycle_callbacks)
            verify(check/'rollout-result.json',check,binding['candidate_sha'],binary,harness,'canary',campaign_path=campaign_path)
        cycles.append(cycle); journal.extend(cycle_journal); callbacks.extend(cycle_callbacks)
        write_evidence(evidence,binding,stage,cycles,journal,callbacks)
    return verify(evidence/'rollout-result.json',evidence,binding['candidate_sha'],binary,harness,stage,campaign_path=campaign_path)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--execution-binary',type=Path,required=True)
    parser.add_argument('--expected-binary-sha256',required=True)
    parser.add_argument('--expected-git-sha',required=True)
    parser.add_argument('--rollout-stage',choices=('canary','pilot','extended'),required=True)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--driver',type=Path,required=True)
    parser.add_argument('--driver-sha256',required=True)
    parser.add_argument('--evidence-dir',type=Path,required=True)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--max-mutation-cycles',type=int,required=True)
    parser.add_argument('--max-order-quantity',type=float,required=True)
    parser.add_argument('--max-order-notional',type=float,required=True)
    parser.add_argument('--max-active-orders',type=int,required=True)
    parser.add_argument('--max-gross-position',type=float,required=True)
    parser.add_argument('--profile-order-mode',choices=('EXTERNAL_P1_CANARY_LMT_DAY',),required=True)
    parser.add_argument('--require-flat-between-cycles',action='store_true',required=True)
    parser.add_argument('--candidate-environment',choices=('cleared',),required=True)
    parser.add_argument('--candidate-network-policy',choices=('broker-proxy-only',),required=True)
    parser.add_argument('--credential-delivery',choices=('harness-only',),required=True)
    parser.add_argument('--mode',choices=('p1-progressive-rollout',),required=True)
    args=parser.parse_args(argv)
    try:
        require(os.environ.get('HEPTA_QUALIFICATION_MUTATIONS')=='1','explicit PAPER opt-in required')
        campaign=load_campaign(args.campaign)
        require(campaign['binding']['candidate_sha']==args.expected_git_sha and
                campaign['binding']['binary_sha256']==args.expected_binary_sha256,'candidate manifest mismatch')
        require(args.max_order_quantity==args.max_gross_position==1 and args.max_order_notional==5000 and
                args.max_active_orders==1 and args.max_mutation_cycles=={'canary':1,'pilot':3,'extended':10}[args.rollout_stage],
                'P1 envelope or stage count mismatch')
        require(args.result==args.evidence_dir/'rollout-result.json','result must be inside this evidence root')
        require(campaign['binding']['driver_sha256']==args.driver_sha256,'driver identity mismatch')
        args.evidence_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        driver=PinnedDriver(args.driver,args.driver_sha256,args.evidence_dir)
        run(driver,args.campaign,args.execution_binary,Path(__file__).resolve(),args.rollout_stage,args.evidence_dir)
        return 0
    except (EvidenceError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print('[PAPER-HARNESS] FAIL: '+str(exc),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
