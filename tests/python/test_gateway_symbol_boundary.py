from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]


class GatewaySymbolTests(unittest.TestCase):
    def probe(self,privileged=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);binary=root/'gateway';binary.write_bytes(b'fixture')
            nm=root/'nm';nm.write_text('#!/bin/sh\n'+''.join(f"echo '0000 T ordinary_symbol_{i}'\n" for i in range(1300))+
                ("echo '0000 T ExecutionCoordinator::Send'\n" if privileged else ''))
            nm.chmod(0o500)
            return subprocess.run(['cmake','-DHEPTA_GATEWAY_BINARY='+str(binary),'-DHEPTA_NM_EXECUTABLE='+str(nm),
                                   '-P',str(ROOT/'cmake/verify_gateway_forbidden_symbols.cmake')],capture_output=True,text=True)
    def test_legitimate_symbol_growth_is_observed_not_blocked(self):
        result=self.probe();self.assertEqual(result.returncode,0,result.stderr);self.assertIn('1300',result.stdout)
    def test_privileged_linkage_is_still_blocked(self):
        result=self.probe(True);self.assertNotEqual(result.returncode,0);self.assertIn('privileged',result.stderr)

if __name__=='__main__':unittest.main()
