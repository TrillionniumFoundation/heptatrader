"""Full read-only SHADOW evidence flow on a disposable root fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import shadow_pipeline_fixture as fixture

@unittest.skipUnless(os.geteuid()==0 and os.environ.get('HEPTA_ISOLATED_PROCESS_TESTS')=='1','requires disposable Linux root and explicit process opt-in')
class ShadowPipelineIntegrationTests(unittest.TestCase):
    def test_capture_to_nonempty_history_decision_final_audit_and_sealed_replay(self):
        interlock=Path('/var/lib/hepta')
        # mkdir is atomic and refuses existing host state; do not delete it
        # unless this invocation created it and its inode is unchanged.
        interlock.mkdir(mode=0o755)
        identity=interlock.stat()
        try:
            with tempfile.TemporaryDirectory(prefix='hepta-shadow-') as directory:
                base=Path(directory);base.chmod(0o755)
                fixture.prepare(base)
                result=subprocess.run([sys.executable,str(Path(fixture.__file__)),str(base)],user=fixture.UID,group=fixture.GID,extra_groups=[],
                    env={'PATH':'/usr/bin:/bin','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=240)
                self.assertEqual(result.returncode,0,result.stderr+result.stdout[-4000:])
                receipt=json.loads(result.stdout)
                self.assertEqual(receipt['samples'],1208)
                if os.environ.get('HEPTA_PROCESS_EVIDENCE_DIR'):
                    output=Path(os.environ['HEPTA_PROCESS_EVIDENCE_DIR'])/'shadow-pipeline.json'
                    output.parent.mkdir(parents=True,exist_ok=True)
                    with output.open('x') as stream: json.dump(receipt,stream,sort_keys=True,indent=2)
                print('SHADOW pipeline: 1208 samples; filled/cost-sensitive sealed replay; recovery and immutable retry passed',flush=True)
        finally:
            now=interlock.stat()
            if (now.st_dev,now.st_ino)==(identity.st_dev,identity.st_ino): shutil.rmtree(interlock)

if __name__=='__main__': unittest.main()
