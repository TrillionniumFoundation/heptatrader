from __future__ import annotations
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from ci_workflow_contract import load_workflow, validate


class ResilienceWorkflowTests(unittest.TestCase):
    def test_required_contexts_execute_real_compilers(self):
        self.assertEqual(validate(ROOT),[])

    def fixture(self,path):
        target=path/'.github/workflows'; target.mkdir(parents=True)
        for name in ('canonical-full-suite.yml','resilience-periodic.yml'):
            shutil.copy2(ROOT/'.github/workflows'/name,target/name)
        return target/'canonical-full-suite.yml'

    def test_commands_in_comments_cannot_satisfy_required_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); f=self.fixture(root)
            text=f.read_text().replace('run: bash scripts/run_runtime_resilience.sh g++',
                                      "run: echo success\n        # bash scripts/run_runtime_resilience.sh g++")
            f.write_text(text)
            self.assertTrue(any('failure-propagating' in e for e in validate(root)))

    def test_continue_on_error_and_skipped_jobs_are_rejected(self):
        for injected in ('    continue-on-error: true\n',"    if: false\n"):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); f=self.fixture(root)
                f.write_text(f.read_text().replace('  reliability-gcc:\n','  reliability-gcc:\n'+injected))
                self.assertTrue(validate(root))

    def test_nightly_lane_does_not_duplicate_pr_work(self):
        periodic=load_workflow(ROOT/'.github/workflows/resilience-periodic.yml')
        self.assertNotIn('pull_request',periodic['on'])
        self.assertIn('schedule',periodic['on'])

    def test_new_gateway_session_and_unknown_paths_cannot_be_documentation_only(self):
        # Exercise the actual shell selector in temporary Git histories. A fake
        # cmake marks whether a build was requested; no compiler work is needed.
        for name,expect_build in [('docs/a.md',False),('HeptaTrade/tool_host/a.cpp',True),
                                 ('HeptaTrade/client/a.cpp',True),('new-runtime/a',True)]:
            with self.subTest(path=name), tempfile.TemporaryDirectory() as d:
                root=Path(d); subprocess.run(['git','init','-q',str(root)],check=True)
                def git(*args):
                    return subprocess.check_output(['git','-C',str(root),'-c','user.name=Test',
                                                     '-c','user.email=test@example.invalid',*args],text=True).strip()
                (root/'seed').write_text('seed'); git('add','.'); git('commit','-qm','seed'); base=git('rev-parse','HEAD')
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('change')
                git('add','.'); git('commit','-qm','change')
                bins=root/'bin'; bins.mkdir(); fake=bins/'cmake'
                fake.write_text('#!/bin/sh\necho build >> "'+str(root/'built')+'"\nexit 99\n'); fake.chmod(0o755)
                import os
                env=dict(os.environ,PATH=str(bins)+':'+os.environ['PATH'],HEPTA_CI_BASE_SHA=base)
                proc=subprocess.run(['bash',str(ROOT/'scripts/run_runtime_resilience.sh'),'g++'],cwd=root,env=env,
                                    capture_output=True,timeout=10)
                self.assertEqual((root/'built').exists(),expect_build,proc.stderr)
                self.assertEqual(proc.returncode,99 if expect_build else 0)


if __name__=='__main__': unittest.main()
