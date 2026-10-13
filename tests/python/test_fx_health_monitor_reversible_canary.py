"""Adversarial monitor-only canary rollback tests: never touch real systemd."""
import hashlib,json,os
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch

from train_v2 import fx_health_monitor_reversible_canary as m

def show(*,candidate=False, pid="11"):
    p=m.NEW_RELEASE if candidate else m.BASE_RELEASE
    path="/tmp/"+p
    return {
        "LoadState":"loaded","ActiveState":"active","SubState":"running",
        "MainPID":pid,"CPUQuotaPerSecUSec":"500ms" if candidate else "250ms",
        "MemoryMax":"402653184","WorkingDirectory":path,
        "Environment":"PYTHONPATH="+path+"/learn:"+path+"/scripts",
        "RestrictAddressFamilies":"AF_UNIX",
        "NoNewPrivileges":"yes","Restart":"on-failure",
        "ExecStart":"/usr/bin/python3 -B -m laya_cell.source_readiness --sandbox"
                      +(" --refresh-seconds 15" if candidate else ""),
    }

class MonitorCanaryTests(unittest.TestCase):
    def test_default_runtime_verified_without_override(self):
        m.status_assert(show(),candidate=False)
    def test_candidate_requires_optin(self):
        m.status_assert(show(candidate=True),candidate=True)
    def test_candidate_cpu_and_freshness_cannot_be_conflated(self):
        bad=show(candidate=True)
        bad["CPUQuotaPerSecUSec"]="250ms"
        with self.assertRaisesRegex(m.Stop,"UNEXPECTED_MONITOR_CPU_BUDGET"):
            m.status_assert(bad,candidate=True)
    def test_no_family_widening(self):
        x=show(candidate=True);x["RestrictAddressFamilies"]="AF_UNIX AF_INET"
        # Production canary must never allow an additional family.
        with self.assertRaisesRegex(m.Stop,"MONITOR_SANDBOX_CHANGED"):
            m.status_assert(x,candidate=True)
    def test_no_model_tool_runner_accepted(self):
        x=show(candidate=True)
        x["ExecStart"]="python3 -m heptatrader.trader --refresh-seconds 15"
        with self.assertRaisesRegex(m.Stop,"MONITOR_ARGUMENTS_CHANGED"):
            m.status_assert(x,candidate=True)
    def test_inactive_service_rejected(self):
        x=show();x["ActiveState"]="failed"
        with self.assertRaisesRegex(m.Stop,"SOURCE_MONITOR_NOT_ACTIVE"):
            m.status_assert(x,candidate=False)
    def test_no_privilege_escape(self):
        x=show(candidate=True);x["NoNewPrivileges"]="no"
        with self.assertRaisesRegex(m.Stop,"MONITOR_SANDBOX_CHANGED"):
            m.status_assert(x,candidate=True)
    def test_override_no_broker_or_trader(self):
        with tempfile.TemporaryDirectory() as t:
            p={k:Path(t)/k for k in ("config","health","candidate")}
            text=m.override(p)
            self.assertIn("--sandbox --refresh-seconds 15",text)
            self.assertIn("CPUQuota=50%",text)
            self.assertNotIn("--max-cycles",text)
            self.assertNotIn("heptatrader-execution",text)
            self.assertNotIn("ibgateway",text)
    def test_manifest_writeonce(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"receipt.json"
            m.write_once(p,{"trade_authority":False})
            self.assertEqual(json.loads(p.read_text()),{"trade_authority":False})
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                m.write_once(p,{"trade_authority":True})
    def test_restore_must_refuse_unrecognised_override(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"proof";root.mkdir()
            (root/"stage.json").write_text(json.dumps({"override_sha256":"f"*64}))
            name=Path(t)/"canary.conf";name.write_text("dangerous")
            p={"base":Path(t),"dropin":name}
            with patch.object(m,"paths",return_value=p):
                with self.assertRaisesRegex(m.Stop,"UNKNOWN_OVERRIDE_CANNOT_REMOVE"):
                    m.restore(root)
            self.assertTrue(name.exists())
    def test_original_status_path_identity_not_guessed(self):
        p=m.paths()
        self.assertEqual(p["health"].parent.name,
            "61995fcedc2993d7ac33d2ed86c9613068c5a3ecd0d1c377e612639cd5a0958f")
        self.assertEqual(p["health"].name,"status.json")
    def test_wrong_output_path_refused_before_restart(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)
            (base/"status.json").write_text("{}")
            (base/"status.json").chmod(0o600)
            p={"base":base,"health":base/"status.json",
               "config":base/"config.json"}
            s=show()
            s["ExecStart"]="python -m laya_cell.source_readiness --config /another/conf.json --output /another/status.json --sandbox"
            with patch.object(m,"sha",return_value=m.BASE_SHA),patch.object(m,"config_guard"):
                with patch.object(m,"service_show",return_value=s):
                    with self.assertRaisesRegex(m.Stop,
                        "SOURCE_MONITOR_CURRENT_INPUT_OUTPUT_PATH_MISMATCH"):
                        m.frozen_prereqs(p)
if __name__=="__main__":unittest.main()
