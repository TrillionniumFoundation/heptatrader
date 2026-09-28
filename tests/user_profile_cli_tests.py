from pathlib import Path
import os, subprocess, sys, tempfile, unittest
BINARY=Path(sys.argv.pop(1)).resolve()
class ProfileInputTests(unittest.TestCase):
    def invoke(self,path):
        return subprocess.run([str(BINARY),'--env',str(path)],capture_output=True,text=True,timeout=3)
    def test_bad_inputs_cannot_emit_a_profile(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'input'
            for content in ('','x'*65537,'a=b\na=c\n','not-a-field\n'):
                p.write_text(content);p.chmod(0o600)
                result=self.invoke(p)
                self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,'')
            link=root/'link';link.symlink_to(p)
            self.assertNotEqual(self.invoke(link).returncode,0)
            p.unlink();os.mkfifo(p,0o600)
            self.assertNotEqual(self.invoke(p).returncode,0)
if __name__=='__main__': unittest.main()
