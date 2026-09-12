#!/usr/bin/env python3
"""Check executable workflow structure; comments and display names are not proof."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import sys
from ci_workflow_contract import load_workflow

def run_steps(job):
    return [step["run"] for step in job.get("steps",[]) if isinstance(step.get("run"),str)]

ROOT=Path(__file__).resolve().parents[1]
WORKFLOW=Path('.github/workflows/ib-paper-qualification.yml')
OWNER_GATE="github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && github.repository == 'TrillionniumFoundation/heptatrader' && github.actor == 'ProfHepta' && github.actor_id == 102159240 && github.triggering_actor == 'ProfHepta'"
BUILD_GATE=OWNER_GATE+" && inputs.rollout_stage == 'build' && inputs.candidate_sha == github.sha"
CAMPAIGN_GATE=OWNER_GATE+" && inputs.rollout_stage != 'build' && inputs.mutation_mode == true && inputs.artifact_id != ''"


def validate(root=ROOT):
    root=Path(root);errors=[]
    try:w=load_workflow(root/WORKFLOW)
    except (OSError,ValueError) as exc:return [str(exc)]
    if set(w.get('on',{}))!={'workflow_dispatch'}:errors.append('PAPER must be explicitly owner-dispatched')
    if w.get('permissions')!={'actions':'read','contents':'read'}:errors.append('unexpected workflow credentials')
    if w.get('concurrency',{}).get('cancel-in-progress') is not False:errors.append('running PAPER campaign must not auto-cancel')
    jobs=w.get('jobs',{})
    if set(jobs)!={'build-candidate','resolve-artifact','campaign'}:return errors+['missing build/resolve/campaign separation']
    for name,job in jobs.items():
        gate=BUILD_GATE if name=='build-candidate' else CAMPAIGN_GATE
        if job.get('if')!=gate:errors.append(name+': owner/immutable input gate changed')
        if job.get('continue-on-error'):errors.append(name+': job failure may not be ignored')
        for step in job.get('steps',[]):
            if step.get('continue-on-error'):errors.append(name+': step failure may not be ignored')
            action=step.get('uses')
            if action and re.fullmatch(r'[^@\s]+@[0-9a-f]{40}',action) is None:errors.append(name+': unpinned action')
            if action and action.startswith('actions/checkout@'):
                options=step.get('with',{})
                if options.get('persist-credentials') is not False or options.get('ref')!='${{ github.sha }}':
                    errors.append(name+': candidate input may not select trusted checkout or inherit credentials')
    build=jobs['build-candidate'];resolver=jobs['resolve-artifact'];campaign=jobs['campaign']
    if 'environment' in build or 'environment' in resolver:errors.append('no-secret build/metadata jobs cannot request mutation environment')
    if campaign.get('environment')!='ib-paper' or campaign.get('needs')!='resolve-artifact':errors.append('mutation environment or artifact admission dependency missing')
    if resolver.get('runs-on')!='ubuntu-24.04':errors.append('artifact resolver must not allocate privileged host')
    build_runs=run_steps(build)
    actual_build=[s for s in build_runs if any(line.strip().startswith('trusted/scripts/build_ib_candidate_artifact.sh ') for line in s.splitlines())]
    if len(actual_build)!=1:errors.append('candidate must be built exactly once in the build-only job')
    for name in ('resolve-artifact','campaign'):
        if any('build_ib_candidate_artifact.sh' in line and not line.lstrip().startswith('#') for s in run_steps(jobs[name]) for line in s.splitlines()):errors.append(name+': reuse must never rebuild candidate')
    source_checkouts=[s for s in build.get('steps',[]) if s.get('with',{}).get('path')=='candidate']
    if len(source_checkouts)!=1:errors.append('exactly one candidate source checkout required')
    downloads=[s for s in campaign.get('steps',[]) if str(s.get('uses','')).startswith('actions/download-artifact@')]
    if len(downloads)!=1 or downloads[0].get('with',{}).get('artifact-ids')!='${{ needs.resolve-artifact.outputs.artifact_id }}' or downloads[0].get('with',{}).get('run-id')!='${{ needs.resolve-artifact.outputs.run_id }}':errors.append('campaign must reuse exact historical artifact ID/run')
    runs=run_steps(campaign)
    steps=campaign.get('steps',[])
    verify=[i for i,s in enumerate(steps) if any(line.strip().startswith('python3 trusted/scripts/verify_ib_candidate_artifact.py verify') for line in s.get('run','').splitlines())]
    probe=[i for i,s in enumerate(steps) if any(line.strip().startswith('"$probe" ') for line in s.get('run','').splitlines())]
    execute=[i for i,s in enumerate(steps) if any(line.strip().startswith('python3 trusted/scripts/hepta_paper_rollout_host.py ') for line in s.get('run','').splitlines())]
    if not(len(verify)==len(probe)==len(execute)==1 and verify[0]<probe[0]<execute[0]):errors.append('same-host verify/preflight/campaign ordering missing')
    for indices in (verify,probe,execute):
        if len(indices)==1 and 'if' in steps[indices[0]]:errors.append('mandatory host step may not be skipped')
    if campaign.get('env',{}).get('HEPTA_QUALIFICATION_MUTATIONS')!='1':errors.append('missing explicit bounded mutation input')
    uploads=[s for s in steps if str(s.get('uses','')).startswith('actions/upload-artifact@')]
    if len(uploads)!=1 or uploads[0].get('if')!='always()':errors.append('failure evidence upload must always run')
    return errors


def self_test():
    errors=validate()
    if errors:raise ValueError('\n'.join(errors))


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--self-test',action='store_true');args=parser.parse_args(argv)
    errors=validate(args.root)
    for error in errors:print('[QUALIFICATION] '+error,file=sys.stderr)
    if errors:return 1
    print('[QUALIFICATION] PASS executable workflow contract');return 0

if __name__=='__main__':raise SystemExit(main())
