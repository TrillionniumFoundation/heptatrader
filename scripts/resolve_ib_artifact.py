#!/usr/bin/env python3
"""Resolve an exact prior Actions artifact, never a moving branch or latest run."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import sys
import urllib.request
from hepta_evidence_io import EvidenceError, loads

REPOSITORY='TrillionniumFoundation/heptatrader'


def resolve(artifact_id: str, candidate_sha: str, fetch):
    if not re.fullmatch(r'[1-9][0-9]{0,19}',artifact_id) or not re.fullmatch(r'[0-9a-f]{40}',candidate_sha):
        raise EvidenceError('exact artifact ID and candidate SHA required')
    artifact=fetch(f'/repos/{REPOSITORY}/actions/artifacts/{artifact_id}')
    if str(artifact.get('id'))!=artifact_id or artifact.get('expired') is not False:
        raise EvidenceError('artifact identity invalid or expired')
    provenance=artifact.get('workflow_run',{})
    if provenance.get('head_sha')!=candidate_sha or provenance.get('head_branch')!='main':
        raise EvidenceError('artifact is not from the selected main-source revision')
    run_id=provenance.get('id')
    if type(run_id) is not int or run_id<=0: raise EvidenceError('artifact has no source workflow')
    run=fetch(f'/repos/{REPOSITORY}/actions/runs/{run_id}')
    if (run.get('event')!='workflow_dispatch' or run.get('head_sha')!=candidate_sha or
        run.get('path')!='.github/workflows/ib-paper-qualification.yml' or
        run.get('actor',{}).get('id')!=102159240 or run.get('triggering_actor',{}).get('login')!='ProfHepta'):
        raise EvidenceError('artifact does not originate from owner-dispatched PAPER builder')
    jobs=fetch(f'/repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100').get('jobs',[])
    if not any(job.get('name')=='ib-paper-candidate-artifact-build' and job.get('conclusion')=='success' for job in jobs):
        raise EvidenceError('source run has no successful immutable candidate build')
    # A campaign may fail AFTER a successful build. This does not erase that
    # immutable artifact, and resuming never depends on current main movement.
    return {'artifact_id':int(artifact_id),'run_id':run_id,'candidate_sha':candidate_sha}


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact-id',required=True)
    parser.add_argument('--candidate-sha',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    def fetch(path):
        token=os.environ.get('GITHUB_TOKEN','')
        if not token: raise EvidenceError('Actions read token is unavailable')
        request=urllib.request.Request('https://api.github.com'+path,
            headers={'Accept':'application/vnd.github+json','Authorization':'Bearer '+token,
                     'X-GitHub-Api-Version':'2022-11-28'})
        with urllib.request.urlopen(request,timeout=20) as response:
            payload=response.read(4*1024*1024+1)
            if len(payload)>4*1024*1024:raise EvidenceError('oversized artifact metadata')
            return loads(payload)
    try:
        identity=resolve(args.artifact_id,args.candidate_sha,fetch)
        with args.output.open('a',encoding='utf-8') as stream:
            for key,value in identity.items():stream.write(f'{key}={value}\n')
        return 0
    except (OSError,ValueError) as exc:
        print('[ARTIFACT] FAIL: '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
