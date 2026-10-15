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
    def test_failed_receipt_fsync_does_not_leave_truncated_writeonce_file(self):
        with tempfile.TemporaryDirectory() as t:
            target=Path(t)/"activation.json"
            with patch.object(m.os,"fsync",side_effect=OSError("EIO_FLUSH_INJECTED")):
                with self.assertRaisesRegex(OSError,"EIO_FLUSH_INJECTED"):
                    m.write_once(target,{"schema":"incomplete"})
            self.assertFalse(target.exists(),"failed write-once receipt blocks safe recovery")

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
            anchors=[1791457800+i*7200 for i in range(24)]
            (r/"plan.json").write_text(json.dumps({"campaign_id":campaign,
                        "anchors":anchors,"trade_authority":False,"automatic_promotion":False}))
            assessment={"automatic_promotion":False,"economic_qualification":False}
            (r/"complete.json").write_text(json.dumps({"campaign_id":campaign,
                                                        "assessment":assessment}))
            slots=[]
            for i,anchor in enumerate(anchors):
                state='OBSERVED' if i<10 else 'CENSORED'
                row={"schema":"heptatrader.frozen-window-final.v1",
                     "anchor":anchor,"campaign_id":campaign,"state":state,
                     "broker_order_authority":False,
                     "reason":None if state=='OBSERVED' else 'ORIGINAL_NO_RETRY'}
                d=r/'slots'/str(anchor);d.mkdir(parents=True)
                f=d/'final.json';f.write_text(json.dumps(row,sort_keys=True));f.chmod(0o400)
                slots.append({"anchor":anchor,"state":state,
                              "receipt_sha256":hashlib.sha256(f.read_bytes()).hexdigest()})
            result={"campaign_id":campaign,"planned":24,"finalized":24,
                    "observed":10,"censored":14,"phase":"COMPLETE_PENDING_REVIEW",
                    "automatic_promotion":False,"trade_authority":False,
                    "candidate_unchanged":True,"old_smoke_excluded":True,
                    "assessment":assessment,"slots":slots}
            path=r/"status.json";path.write_text(json.dumps(result))
            self.assertEqual(m.frozen_terminal({"frozen":r},m.FINAL_UTC_MS+1)["observed"],10)
            last_slot=dict(result["slots"][-1])
            result["slots"][-1]={"anchor":anchors[-1],"state":"PENDING"}
            path.write_text(json.dumps(result))
            with self.assertRaisesRegex(m.Stop,"TERMINAL_EVIDENCE_INVALID"):
                m.frozen_terminal({"frozen":r},m.FINAL_UTC_MS+1)
            result["slots"][-1]=last_slot
            old_receipt=result["slots"][4]["receipt_sha256"]
            result["slots"][4]["receipt_sha256"]='0'*64
            path.write_text(json.dumps(result))
            with self.assertRaisesRegex(m.Stop,"SLOT_RECEIPT_CHANGED"):
                m.frozen_terminal({"frozen":r},m.FINAL_UTC_MS+1)
            result["slots"][4]["receipt_sha256"]=old_receipt
            path.write_text(json.dumps(result))
            (r/'slots'/str(anchors[7])/'final.json').unlink()
            with self.assertRaisesRegex(m.Stop,"SLOT_RECEIPT_CHANGED"):
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
                at_ms=m.time.time_ns()//1000000,
                source_prefixes=[],source_prefix_digest=hashlib.sha256(b'[]').hexdigest(),
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
                # A 25th REAL publisher breach after 24 green cycles must not
                # be hidden by slicing the original 24 successful rows.
                late=events[-1].copy()
                late.update(cycle=25,
                            source_asof_ms=late['source_asof_ms']+15000,
                            published_ms=late['published_ms']+42000,
                            published_source_age_ms=31000)
                with patch.object(m,"cmd",return_value='\n'.join(json.dumps(x) for x in events+[late])):
                    with self.assertRaisesRegex(m.Stop,"MONITOR_SOURCE_AT_PUBLICATION_STALE_OR_UNTRUSTED"):
                        m.audit_live(root)
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
                # Simulate a genuine 35+ second publication outage without
                # breaking monotonic clocks or forging any per-cycle age.
                for row in events[6:]:
                    row['source_asof_ms']+=20000
                    row['published_ms']+=20000
                with patch.object(m,"cmd",return_value='\n'.join(json.dumps(x) for x in events)):
                    with self.assertRaisesRegex(m.Stop,"MONITOR_SLA_BREACHED_BETWEEN_PUBLICATIONS"):
                        m.audit_live(root)

    def test_activation_receipt_io_failure_restores_original_after_restart(self):
        # After candidate service successfully restarts and verifies, its
        # write-once activation receipt can still fail (e.g. ENOSPC).
        # Previously that left the candidate live without an audit identity.
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);p={"base":root/"base","candidate":root/"candidate",
                             "dropin":root/"candidate.conf",
                             "config":root/"config.json","health":root/"status.json"}
            baseline=show(candidate=False,pid="10011")
            activated=show(candidate=True,pid="10012")
            release_bytes=m.override(p)
            unit_bytes='original systemd unit exactly'
            from hashlib import sha256
            stage={"original_campaign_plan_sha256":"originalplan",
                   "candidate_inventory_sha256":"originalcandidate",
                   "override_sha256":sha256(release_bytes.encode()).hexdigest(),
                   "original_service_show":baseline,
                   "original_service_cat_sha256":sha256(unit_bytes.encode()).hexdigest()}
            (root/"stage.json").write_text(json.dumps(stage))
            performed=[]
            def systemd(*args):
                performed.append(args)
                return unit_bytes if args[-1]==m.SERVICE and 'cat' in args else ''
            with patch.object(m,"paths",return_value=p), \
                 patch.object(m,"frozen_terminal",return_value={"plan_sha256":"originalplan"}), \
                 patch.object(m,"candidate_inventory",return_value="originalcandidate"), \
                 patch.object(m,"frozen_prereqs",return_value=baseline), \
                 patch.object(m,"service_show",return_value=activated), \
                 patch.object(m,"trading_pids",return_value={"execution":"pinned"}), \
                 patch.object(m,"cmd",side_effect=systemd), \
                 patch.object(m,"write_once",side_effect=OSError("ENOSPC_ACTIVATION_RECEIPT")):
                with self.assertRaisesRegex(OSError,"ENOSPC_ACTIVATION_RECEIPT"):
                    m.activate(root)
            self.assertFalse(p["dropin"].exists())
            self.assertFalse((root/"activation.json").exists())
            self.assertEqual(sum(x[-2:]==("restart",m.SERVICE) for x in performed),2)
            self.assertEqual(sum(x[-2:]==("daemon-reload",m.SERVICE) for x in performed),0)

    def test_sigterm_during_real_monitor_audit_rolls_back_and_resets_handler(self):
        import signal
        previous=signal.getsignal(signal.SIGTERM)
        def interrupt(_root,**_):
            signal.raise_signal(signal.SIGTERM)
        with patch.object(m,"activate",return_value={"state":"activated"}), \
             patch.object(m,"audit_live",side_effect=interrupt), \
             patch.object(m,"restore",return_value={"state":"restored"}) as rollback:
            with self.assertRaisesRegex(m.Stop,"MONITOR_CUTOVER_INTERRUPTED_SIGNAL"):
                m.cutover(Path('/isolated'),timeout_seconds=1)
            rollback.assert_called_once()
        self.assertEqual(signal.getsignal(signal.SIGTERM),previous)

if __name__=="__main__":unittest.main()
