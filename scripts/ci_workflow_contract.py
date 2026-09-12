#!/usr/bin/env python3
"""Parse executable workflow nodes. Comments and job titles are not evidence."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import sys
from typing import Any
import yaml

ROOT = Path(__file__).resolve().parents[1]


class WorkflowLoader(yaml.SafeLoader):
    pass


# YAML 1.1's implicit 'on' -> True coercion is not GitHub's workflow semantics.
WorkflowLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in values if tag != 'tag:yaml.org,2002:bool']
    for k, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WorkflowLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(true|false)$', re.I), list('tTfF'))


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f'duplicate workflow key: {key}')
        result[key] = loader.construct_object(value_node)
    return result


WorkflowLoader.add_constructor('tag:yaml.org,2002:map', mapping)


def load_workflow(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding='utf-8')
    if len(text.encode()) > 512*1024:
        raise ValueError('workflow exceeds source-contract size bound')
    value = yaml.load(text, Loader=WorkflowLoader)
    if not isinstance(value, dict) or not isinstance(value.get('jobs'), dict):
        raise ValueError(f'{path}: workflow has no jobs')
    return value


def run_steps(workflow: dict) -> list[dict]:
    return [step for job in workflow['jobs'].values() for step in job.get('steps', [])
            if isinstance(step, dict) and 'run' in step]


def validate(root: Path = ROOT) -> list[str]:
    errors=[]
    try:
        required = load_workflow(root/'.github/workflows/canonical-full-suite.yml')
        periodic = load_workflow(root/'.github/workflows/resilience-periodic.yml')
        for event in ('pull_request', 'push', 'merge_group'):
            if event not in required.get('on', {}): errors.append('required sanitizer workflow missing '+event)
        if 'pull_request' in periodic.get('on', {}):
            errors.append('periodic lane duplicates required PR sanitizer execution')
        if 'schedule' not in periodic.get('on', {}): errors.append('periodic resilience must remain scheduled')
        for key, compiler in [('reliability-gcc','g++'),('reliability-clang','clang++')]:
            job=required['jobs'].get(key,{})
            if job.get('name') != f'canonical-full-suite-reliability ({compiler})':
                errors.append('real sanitizer must retain required server-side context: '+compiler)
            if job.get('if') is not None or job.get('continue-on-error'):
                errors.append('required sanitizer job may not suppress failure or skip: '+compiler)
            steps=[s for s in job.get('steps',[]) if s.get('run') == f'bash scripts/run_runtime_resilience.sh {compiler}']
            if len(steps)!=1 or steps[0].get('if') is not None or steps[0].get('continue-on-error'):
                errors.append('required context lacks a failure-propagating sanitizer invocation: '+compiler)
        for value in (required, periodic):
            if value.get('permissions') != {'contents':'read'}:
                errors.append('resilience must be read-only and secret-free')
    except (OSError, ValueError, yaml.YAMLError, TypeError) as exc:
        errors.append(str(exc))
    return errors


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--root',type=Path,default=ROOT)
    errors=validate(parser.parse_args().root)
    for error in errors: print('[CI-CONTRACT] '+error,file=sys.stderr)
    if not errors: print('[CI-CONTRACT] PASS executable job relationships')
    return bool(errors)


if __name__=='__main__': raise SystemExit(main())
