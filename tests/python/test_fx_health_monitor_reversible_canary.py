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
                      +(" --verified-sealed-cache --refresh-seconds 15" if candidate else ""),
    }

class MonitorCanaryTests(unittest.TestCase):
    def test_default_runtime_verified_without_override(self):
        m.status_assert(show(),candidate=False)
    def test_candidate_requires_optin(self):
        m.status_assert(show(candidate=True),candidate=True)
    def test_candidate_cpu_and_freshness_cannot_be_conflated(self):
        bad=show(candidate=True)
        bad["CPUQuotaPerSecUSec"]="250ms"
        with self.assertRaisesRegex(m.Stop,"UNEXPECTED_MONITOR_RESOURCE_BUDGET"):
            m.status_assert(bad,candidate=True)
    def test_no_family_widening(self):
        x=show(candidate=True);x["RestrictAddressFamilies"]="AF_UNIX AF_INET"
        # Production canary must never allow an additional family.
        with self.assertRaisesRegex(m.Stop,"MONITOR_SANDBOX_CHANGED"):
            m.status_assert(x,candidate=True)
    def test_no_model_tool_runner_accepted(self):
        x=show(candidate=True)
        x["ExecStart"]="python3 -m heptatrader.trader --verified-sealed-cache --refresh-seconds 15"
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
            self.assertIn("--sandbox --verified-sealed-cache --refresh-seconds 15",text)
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
    def test_future_campaign_blocks_cutover(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(m.Stop,"FUTURE_WINDOW_PENDING"):
                m.frozen_terminal({"frozen":Path(t)},now_ms=m.FINAL_UTC_MS-1)

    def test_missing_terminal_record_does_not_pass(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(m.Stop,"NOT_TERMINAL"):
                m.frozen_terminal({"frozen":Path(t)},now_ms=m.FINAL_UTC_MS+1)

    def test_all_twenty_four_original_slots_required(self):
        with tempfile.TemporaryDirectory() as t:
            r=Path(t); campaign="exact-original"
            (r/"plan.json").write_text(json.dumps({"campaign_id":campaign}))
            (r/"complete.json").write_text(json.dumps({"campaign_id":campaign}))
            result={"campaign_id":campaign,"planned":24,"finalized":24,
                    "observed":10,"censored":14,"phase":"COMPLETE_PENDING_REVIEW",
                    "automatic_promotion":False,"trade_authority":False,
                    "slots":[{"state":"OBSERVED"}]*10+[{"state":"CENSORED"}]*14}
            path=r/"status.json"; path.write_text(json.dumps(result))
            self.assertEqual(m.frozen_terminal({"frozen":r},m.FINAL_UTC_MS+1)["observed"],10)
            result["slots"][-1]={"state":"PENDING"}
            path.write_text(json.dumps(result))
            with self.assertRaisesRegex(m.Stop,"TERMINAL_EVIDENCE_INVALID"):
                m.frozen_terminal({"frozen":r},m.FINAL_UTC_MS+1)

    def test_staged_candidate_has_only_two_reviewed_changes(self):
        with tempfile.TemporaryDirectory() as t:
            base=Path(t)/"base";candidate=Path(t)/"candidate"
            src=Path("learn/laya_cell/source_readiness.py")
            cache=Path("learn/laya_cell/verified_sealed_readiness_cache.py")
            for root in (base,candidate):
                (root/src).parent.mkdir(parents=True)
                (root/"integrity.py").write_bytes(b"unchanged")
            (base/src).write_bytes(b"old")
            (candidate/src).write_bytes(b"new")
            (candidate/cache).write_bytes(b"cache")
            original=m.sha
            def check(file):
                value=Path(file).read_bytes()
                special={b"old":m.BASE_SHA,b"new":m.NEW_SHA,b"cache":m.CACHE_SHA}
                return special[value] if value in special else original(file)
            with patch.object(m,"sha",side_effect=check):
                roots={"base":base,"candidate":candidate}
                self.assertEqual(len(m.candidate_inventory(roots)),64)
                (candidate/"integrity.py").write_bytes(b"modified")
                with self.assertRaisesRegex(m.Stop,"STAGED_RELEASE_FOOTPRINT_CHANGED"):
                    m.candidate_inventory(roots)

    def test_activation_refuses_open_original_campaign_without_service_calls(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            (root/"stage.json").write_text('{}')
            with patch.object(m,"paths",return_value={"frozen":root}), \
                 patch.object(m,"frozen_terminal",side_effect=m.Stop("FUTURE_WINDOW_PENDING")), \
                 patch.object(m,"cmd",side_effect=AssertionError("no service command allowed")):
                with self.assertRaisesRegex(m.Stop,"FUTURE_WINDOW_PENDING"):
                    m.activate(root)

    def test_unhealthy_release_reverts_exact_own_candidate(self):
        with patch.object(m,"activate",return_value={"state":"activated"}), \
             patch.object(m,"audit_live",side_effect=m.Stop("MONITOR_SLA_BREACHED_BETWEEN_PUBLICATIONS")), \
             patch.object(m,"restore",return_value={"state":"restored"}) as rollback:
            with self.assertRaisesRegex(m.Stop,"MONITOR_SLA_BREACHED"):
                m.cutover(Path('/isolated'),timeout_seconds=1)
            rollback.assert_called_once()

    def test_twenty_four_true_cadence_events_and_false_ready_refusal(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p={"dropin":root/"override.conf","health":root/"status.json"}
            p["dropin"].write_text('exact candidate')
            control=show(candidate=True,pid="99")
            protected={"FX":"10","BTC":"11"}
            activated={"activated_at_ms":1792060000000,
                       "override_sha256":m.sha(p["dropin"]),
                       "new_service_show":control,"protected_pids":protected}
            (root/"activation.json").write_text(json.dumps(activated))
            events=[]
            for i in range(24):
                at=1792060000000+i*15000
                events.append(dict(schema="heptatrader.source-readiness-cadence-lab.v1",
                    cycle=i+1,source_asof_ms=at,published_ms=at+4000,
                    published_source_age_ms=4000,state="HOLD_NEW_FUSION_WINDOW",
                    usable=17,trade_authority=False,orders_sent=0))
            original=dict(schema="heptatrader.quote-readiness-status.v1",
                period_sha256=m.PERIOD_SHA,trading_authority=False,
                automatic_trial_registration=False,orders_sent=0,
                probes=[{"status":"USABLE"}]*17+[{'status':'PATH_GAP'}]*7,
                counts={"USABLE":17,"PATH_GAP":7},usable=17,
                latest_quote_age_ms=8000,state="HOLD_NEW_FUSION_WINDOW",
                reasons=['LATEST_AUTHORITATIVE_QUOTE_NOT_FRESH'])
            p["health"].write_text(json.dumps(original))
            with patch.object(m,"paths",return_value=p), \
                 patch.object(m,"service_show",return_value=control), \
                 patch.object(m,"trading_pids",return_value=protected), \
                 patch.object(m,"cmd",return_value='\n'.join(json.dumps(x) for x in events)):
                good=m.audit_live(root)
                self.assertEqual(good["cycles"],24)
                self.assertEqual(good["max_before_next_publish_ms"],19000)
                self.assertTrue(good["original_source_HOLD_preserved"])
                original.update(probes=[{'status':'USABLE'}]*24,counts={'USABLE':24},
                                usable=24,state='READY_FOR_REVIEW_NOT_AUTHORIZATION',
                                reasons=[],latest_quote_age_ms=6000)
                p["health"].write_text(json.dumps(original))
                with self.assertRaisesRegex(m.Stop,"FALSE_SOURCE_READY_REJECTED"):
                    m.audit_live(root)
                original['latest_quote_age_ms']=300
                p["health"].write_text(json.dumps(original))
                events[0]['state']='READY_FOR_REVIEW_NOT_AUTHORIZATION'
                with patch.object(m,"cmd",return_value='\n'.join(json.dumps(x) for x in events)):
                    with self.assertRaisesRegex(m.Stop,"MONITOR_FALSE_READY_AT_PUBLICATION"):
                        m.audit_live(root)
                events[0]['state']='HOLD_NEW_FUSION_WINDOW'
                events[5]['published_ms']+=17000
                events[5]['published_source_age_ms']+=17000
                with patch.object(m,"cmd",return_value='\n'.join(json.dumps(x) for x in events)):
                    with self.assertRaisesRegex(m.Stop,"MONITOR_SLA_BREACHED_BETWEEN_PUBLICATIONS"):
                        m.audit_live(root)

if __name__=="__main__":unittest.main()
