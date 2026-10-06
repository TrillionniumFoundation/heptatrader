"""Negative subprocess checks: impossible socket path; no Broker contact."""
from pathlib import Path
import json, os, subprocess, sys, tempfile, unittest
BINARY = Path(sys.argv.pop(1)).resolve()
class PaperCommandTests(unittest.TestCase):
    def call(self, *args):
        p = subprocess.run([str(BINARY), *args], capture_output=True, text=True, timeout=3)
        self.assertNotEqual(p.returncode, 0)
        value = json.loads(p.stdout)
        self.assertFalse(value["ok"])
        self.assertFalse(value["transport_ok"])
        self.assertEqual(value["application_outcome"],"none")
        self.assertEqual(value["application_status"],"")
        self.assertEqual(value["application_reason"],"")
        self.assertEqual(value["application_order_id"],-1)
        self.assertIsNone(value["result"])
        return value
    def test_unknown_and_duplicate_arguments(self):
        for args in ((), ("unknown",), ("prepare-open", "--live", "1"),
                     ("prepare-open", "--socket", "/dev/null/impossible", "--socket", "/dev/null/other")):
            with self.subTest(args=args): self.call(*args)
    def test_invalid_proposals_fail_before_any_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); token=root/"token"; token.write_text("synthetic-test-token"); token.chmod(0o600)
            common=["prepare-open","--socket","/dev/null/impossible","--token-file",str(token),"--directory",str(root),
                    "--call-id","paper-preview-001"]
            for side,price,expiry in (("HOLD","1.1","1900000000000"),("BUY","-1","1900000000000"),
                                      ("BUY","nan","1900000000000"),("BUY","1.1","0")):
                result=self.call(*common,"--side",side,"--price",price,"--expiry",expiry)
                self.assertEqual(result["command_id"],"")
                self.assertEqual(list(root.glob("*.hsr")),[])
    def test_crypto_canary_has_distinct_tiny_scope_before_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); token=root/"token"; token.write_text("synthetic-test-token"); token.chmod(0o600)
            common=["prepare-crypto-canary-open","--socket","/dev/null/impossible","--token-file",str(token),
                    "--directory",str(root),"--call-id","crypto-canary-preview-001",
                    "--price","86000","--expiry","1900000000000"]
            bad=self.call(*common,"--cash-quantity","150")
            self.assertEqual(bad["reason"],"RESEARCH_PAPER_CRYPTO_CANARY_CASH_SCOPE_INVALID")
            good=self.call(*common,"--cash-quantity","43")
            self.assertNotEqual(good["reason"],"RESEARCH_PAPER_CRYPTO_CANARY_CASH_SCOPE_INVALID")
            self.assertEqual(list(root.glob("*.hsr")),[])

    def test_missing_record_is_not_recreated_or_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); token=root/"token"; token.write_text("synthetic-test-token"); token.chmod(0o600)
            common=["--socket","/dev/null/impossible","--token-file",str(token),"--directory",str(root),
                    "--command-id","absent-command-001"]
            self.call("submit",*common)
            self.call("inspect",*common,"--call-id","status-query-001")
            self.assertEqual(list(root.glob("*.hsr")),[])
if __name__=="__main__": unittest.main()
