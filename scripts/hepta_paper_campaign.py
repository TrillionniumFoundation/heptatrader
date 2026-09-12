#!/usr/bin/env python3
"""Durable, single-artifact PAPER campaign continuation.

A process crash leaves a running attempt on disk. Re-entry never blindly sends
again. Successful stages can be reused only after their stored evidence is
reverified against the same campaign, executable, harness and policy.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid

from hepta_evidence_io import EvidenceError, canonical_bytes, load_json, write_json, sha256_file
from verify_ib_paper_rollout import load_campaign, verify

STAGES=('canary','pilot','extended')


class CampaignStore:
    def __init__(self, root: Path, campaign_path: Path, binary: Path, harness: Path):
        self.root=root
        self.campaign=load_campaign(campaign_path)
        self.binary,self.harness=binary,harness
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        metadata=root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink() or metadata.st_uid!=os.getuid() or metadata.st_mode & 0o022:
            raise EvidenceError('campaign store must be private, owned and non-symlinked')
        self.identity=root/'campaign.json'
        try:
            write_json(self.identity,self.campaign)
        except FileExistsError:
            if load_json(self.identity)!=self.campaign:
                raise EvidenceError('campaign identity drift; this store cannot change artifacts')
        self.state_path=root/'state.json'

    @contextmanager
    def locked(self):
        fd=os.open(self.root/'campaign.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            metadata=os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1 or metadata.st_uid!=os.getuid():
                raise EvidenceError('unsafe campaign lock')
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EvidenceError('another process owns this campaign') from exc
            yield
        finally:
            os.close(fd)

    def state(self):
        if not self.state_path.exists(): return dict(schema='heptatrader.paper-campaign-state.v1', completed={}, active=None)
        value=load_json(self.state_path)
        if not isinstance(value,dict) or set(value)!={'schema','completed','active'} or value['schema']!='heptatrader.paper-campaign-state.v1':
            raise EvidenceError('invalid campaign state')
        if not isinstance(value['completed'],dict) or not set(value['completed']).issubset(STAGES):
            raise EvidenceError('invalid campaign stages')
        return value

    def reverify(self, stage, record):
        if not isinstance(record,dict) or set(record)!={'attempt','receipt_sha256'}:
            raise EvidenceError('invalid completed-stage record')
        attempt=record['attempt']
        if not isinstance(attempt,str) or not attempt.startswith(stage+'-') or '/' in attempt or '..' in attempt:
            raise EvidenceError('invalid attempt path')
        evidence=self.root/'attempts'/attempt
        receipt_path=evidence/'rollout-verification.json'
        if sha256_file(receipt_path)!=record['receipt_sha256']:
            raise EvidenceError('saved stage receipt changed')
        expected=verify(evidence/'rollout-result.json',evidence,self.campaign['binding']['candidate_sha'],
                        self.binary,self.harness,stage,campaign_path=self.identity)
        if load_json(receipt_path)!=expected:
            raise EvidenceError('saved receipt disagrees with current evidence')
        return expected

    def run(self, stage, operation):
        if stage not in STAGES: raise EvidenceError('unknown progressive stage')
        with self.locked():
            state=self.state()
            # Also bind a resumed attempt to unchanged executable bytes.
            if sha256_file(self.binary)!=self.campaign['binding']['binary_sha256'] or sha256_file(self.harness)!=self.campaign['binding']['harness_sha256']:
                raise EvidenceError('candidate or harness bytes changed')
            if state['active'] is not None:
                raise EvidenceError('unresolved/failed attempt exists; reconcile it before any new mutation')
            previous_end = 0
            for previous in STAGES[:STAGES.index(stage)]:
                if previous not in state['completed']: raise EvidenceError('preceding stage has not passed: '+previous)
                previous_receipt=self.reverify(previous,state['completed'][previous])
                if previous_receipt['start_at_ms'] < previous_end:
                    raise EvidenceError('completed stages overlap in time')
                previous_end=previous_receipt['end_at_ms']
            if stage in state['completed']:
                receipt=self.reverify(stage,state['completed'][stage])
                if receipt['start_at_ms'] < previous_end:
                    raise EvidenceError('completed stages overlap in time')
                return receipt
            attempt=stage+'-'+uuid.uuid4().hex
            evidence=self.root/'attempts'/attempt
            evidence.parent.mkdir(exist_ok=True,mode=0o700)
            state['active']=dict(stage=stage,attempt=attempt,status='running')
            write_json(self.state_path,state,replace=True)  # before any possible external send
            try:
                operation(evidence)
                receipt=verify(evidence/'rollout-result.json',evidence,self.campaign['binding']['candidate_sha'],
                               self.binary,self.harness,stage,campaign_path=self.identity)
                if receipt['start_at_ms'] < previous_end:
                    raise EvidenceError('stage evidence predates previous terminal stage')
                write_json(evidence/'rollout-verification.json',receipt)
                state['completed'][stage]=dict(attempt=attempt,receipt_sha256=sha256_file(evidence/'rollout-verification.json'))
                state['active']=None
                write_json(self.state_path,state,replace=True)
                return receipt
            except BaseException:
                # Preserve the evidence and active identity, including on SIGINT.
                state['completed'].pop(stage, None)
                state['active']=dict(stage=stage,attempt=attempt,status='failed_or_interrupted')
                try:
                    write_json(self.state_path,state,replace=True)
                except OSError:
                    # The pre-send running record was already durable. Preserve
                    # the original failure; inability to update it cannot reopen
                    # admission or turn a written receipt into completed state.
                    pass
                raise

    def status(self):
        with self.locked():
            state=self.state()
            previous_end=0
            missing=False
            for stage in STAGES:
                if stage not in state['completed']:
                    missing=True; continue
                if missing: raise EvidenceError('completed campaign stages are not contiguous')
                receipt=self.reverify(stage,state['completed'][stage])
                if receipt['start_at_ms'] < previous_end: raise EvidenceError('completed stages overlap in time')
                previous_end=receipt['end_at_ms']
            return state


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--harness',type=Path,required=True)
    parser.add_argument('--stage',choices=STAGES)
    parser.add_argument('--artifact-dir',type=Path)
    parser.add_argument('--status',action='store_true')
    args=parser.parse_args(argv)
    try:
        store=CampaignStore(args.store,args.campaign,args.binary,args.harness)
        if args.status:
            print(canonical_bytes(store.status()).decode(),end=''); return 0
        if not args.stage or args.artifact_dir is None: raise EvidenceError('stage and artifact-dir are required')
        def operation(evidence):
            env=dict(os.environ,HEPTA_ROLLOUT_CAMPAIGN=str(store.identity.resolve()))
            subprocess.run(['bash',str(Path(__file__).with_name('run_ib_paper_artifact_rollout.sh')),
                            str(args.artifact_dir),store.campaign['binding']['candidate_sha'],str(evidence),args.stage],
                           check=True,env=env)
        store.run(args.stage,operation)
        return 0
    except (EvidenceError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print('[CAMPAIGN] FAIL: '+str(exc),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
