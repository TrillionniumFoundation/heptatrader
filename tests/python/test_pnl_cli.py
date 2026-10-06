from pathlib import Path
import json, os, subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[2]

class PnlCliTests(unittest.TestCase):
    def test_dispatch_prefers_immutable_observer_without_trading_commands(self):
        with tempfile.TemporaryDirectory() as t:
            home=Path(t); selected=home/'.local/share/heptatrader/pnl-current'; selected.mkdir(parents=True)
            (selected/'hepta_pnl_report.py').write_text('import json,sys;print(json.dumps({"args":sys.argv[1:]}))\n')
            env=dict(os.environ,HOME=str(home))
            r=subprocess.run(['bash',str(ROOT/'scripts/heptatrader-user'),'pnl','--format','json'],env=env,text=True,capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(r.stdout),{'args':['--format','json']})

    def test_missing_pnl_install_is_unknown_not_zero_or_trade(self):
        with tempfile.TemporaryDirectory() as t:
            r=subprocess.run(['bash',str(ROOT/'scripts/heptatrader-user'),'pnl'],env=dict(os.environ,HOME=t),text=True,capture_output=True)
            self.assertEqual(r.returncode,69)
            self.assertIn('PnL UNKNOWN',r.stderr)
            self.assertNotIn('+0.00',r.stdout)

    def test_observer_unit_cannot_activate_trading_and_cannot_write_the_ledger(self):
        text=(ROOT/'systemd/user/heptatrader-pnl.service').read_text()
        self.assertNotIn('Wants=',text);self.assertNotIn('Requires=',text)
        self.assertNotIn('PartOf=heptatrader.target',text)
        self.assertIn('RestrictAddressFamilies=AF_UNIX',text)
        self.assertIn('ProtectHome=read-only',text)
        paths=[l for l in text.splitlines() if l.startswith('ReadWritePaths=')]
        self.assertEqual(paths,['ReadWritePaths=%h/.local/state/heptatrader/pnl'])
        self.assertIn('hepta_pnl_report.py --watch',text)
