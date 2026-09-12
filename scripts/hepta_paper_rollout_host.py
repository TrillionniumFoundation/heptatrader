#!/usr/bin/env python3
"""Run/resume a root-admitted campaign on this host without rebuilding artifacts."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from hepta_evidence_io import EvidenceError, canonical_bytes, sha256_file, read_bytes
from hepta_paper_campaign import CampaignStore, STAGES
from verify_ib_paper_rollout import load_campaign


def trusted_host_file(path):
    path=Path(path)
    if not path.is_absolute():raise EvidenceError('host control file must be absolute')
    # A root-owned leaf beneath a runner-writable directory is not trusted.
    for parent in (path,)+tuple(path.parents):
        info=parent.lstat()
        if parent.is_symlink() or info.st_uid!=0 or info.st_mode & 0o022:
            raise EvidenceError('unsafe host control namespace: '+str(parent))
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise EvidenceError('host control must be a regular single-link file')
    read_bytes(path)


def execute(artifact,archive,campaign_path,state_root,driver,stage,export):
    if not state_root.is_absolute():raise EvidenceError('absolute persistent state root required')
    trusted_host_file(campaign_path);trusted_host_file(driver)
    campaign=load_campaign(campaign_path);binding=campaign['binding']
    binary=artifact/'hepta-ib-executiond';harness=Path(__file__).with_name('hepta_ib_paper_harness.py')
    if sha256_file(archive)!=binding['artifact_sha256']:raise EvidenceError('archive differs from root-admitted artifact')
    if sha256_file(driver)!=binding['driver_sha256']:raise EvidenceError('driver differs from root-admitted identity')
    key=hashlib.sha256(canonical_bytes(binding)).hexdigest()
    store=CampaignStore(state_root/key,campaign_path,binary,harness)
    highest='extended' if stage=='certify' else stage
    try:
        for current in STAGES[:STAGES.index(highest)+1]:
            def operation(evidence,current=current):
                env=dict(os.environ,HEPTA_IB_PAPER_QUALIFIER=str(harness.resolve()),
                         HEPTA_IB_PAPER_QUALIFIER_SHA256=binding['harness_sha256'],
                         HEPTA_ROLLOUT_CAMPAIGN=str(store.identity.resolve()),
                         HEPTA_ROLLOUT_DRIVER=str(driver),HEPTA_ROLLOUT_DRIVER_SHA256=binding['driver_sha256'],
                         HEPTA_QUALIFICATION_MUTATIONS='1')
                subprocess.run(['bash',str(Path(__file__).with_name('run_ib_paper_artifact_rollout.sh')),
                                str(artifact),binding['candidate_sha'],str(evidence),current],env=env,check=True)
            store.run(current,operation)
        # Full V5 is intentionally a separate external experiment and credential.
        # Fixed destination refuses accidental re-execution of an uncertain run.
        if stage=='certify':
            evidence=store.root/'certification'
            subprocess.run(['bash',str(Path(__file__).with_name('run_ib_paper_artifact_qualification.sh')),
                            str(artifact),binding['candidate_sha'],str(evidence)],check=True)
            subprocess.run([sys.executable,str(Path(__file__).with_name('verify_ib_paper_qualification.py')),
                            '--result',str(evidence/'qualification-result.json'),'--evidence-root',str(evidence),
                            '--expected-git-sha',binding['candidate_sha'],'--expected-binary',str(binary),
                            '--expected-harness',os.environ['HEPTA_IB_PAPER_QUALIFIER'],
                            '--receipt',str(evidence/'qualification-verification.json')],check=True)
    finally:
        # Export only the portable controller's non-secret evidence tree. Root
        # driver contract forbids credential/token values in typed responses.
        if export.exists():raise EvidenceError('evidence export already exists')
        shutil.copytree(store.root,export,symlinks=True)


def main(argv=None):
    parser=argparse.ArgumentParser()
    for key in ('artifact-dir','archive','campaign','state-root','driver','export'):
        parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--stage',choices=(*STAGES,'certify'),required=True)
    args=parser.parse_args(argv)
    try:
        if os.environ.get('HEPTA_QUALIFICATION_MUTATIONS')!='1':raise EvidenceError('explicit PAPER opt-in required')
        execute(args.artifact_dir,args.archive,args.campaign,args.state_root,args.driver,args.stage,args.export)
        return 0
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('[PAPER-HOST] FAIL: '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
