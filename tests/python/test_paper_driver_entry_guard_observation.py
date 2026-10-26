from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import ExitStack
from unittest import mock
import copy,importlib.util,json,os,signal,subprocess,sys,unittest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("guard_observation_driver",ROOT/"scripts/heptatrader_paper_driver.py")
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)

class EntryGuardObservabilityTests(unittest.TestCase):
    NOW=1792288000000

    def run_loop(self,actions=("BUY",),loops=2,result=None,error=None,initial_guard=None):
        result=result or subprocess.CompletedProcess(["cli","paper-on"],75,
            json.dumps({"status":"HOLD_BLOCKED","reason":"BLOCKED_BY_PERSISTENT_MAINTENANCE_HOLD"}),"")
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            home=Path(tmp);state_path=home/".local/state/heptatrader/crypto-driver/state.json"
            for relative in [".local/bin/heptatrader",".local/share/heptatrader/current/bin/hepta-paper-command",
                ".local/share/heptatrader/current/bin/hepta-sessionctl",".npm-global/bin/openclaw",
                ".config/heptatrader/crypto.token"]:
                p=home/relative;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            def patch(*a,**kw):return stack.enter_context(mock.patch.object(*a,**kw))
            uid=os.geteuid()
            patch(driver.Path,"home",return_value=home)
            patch(driver.os,"geteuid",return_value=uid or 1000)
            # Root CI still executes the real loop and atomic writes; private
            # ownership policy itself is outside this observability test.
            if uid==0:patch(driver,"private_dir",side_effect=lambda p:p.mkdir(parents=True,exist_ok=True,mode=0o700))
            patch(driver,"now_ms",return_value=self.NOW);patch(driver,"INSTRUMENT","BTC.USD")
            # This fixture exercises actual entry checks; host maintenance is
            # covered separately by the inference-mode tests.
            patch(driver,"entry_inference_hold_reason",return_value=None)
            patch(driver,"DECISION_SEC",0)
            for key in ("QTY","QUANTITY_TOLERANCE","NATIVE_ENDPOINT","CLOSE_RECEIPT_ROOT"):
                patch(driver,key,getattr(driver,key))
            state=driver.load_state(state_path)
            state["history"]=[{"observed_at_ms":self.NOW-i,"bid":1.,"ask":1.1,"mid":1.05} for i in range(6,0,-1)]
            if initial_guard is not None:state["last_entry_guard"]=copy.deepcopy(initial_guard)
            patch(driver,"load_state",return_value=state);patch(driver,"crypto_roundtrip_qualified",return_value=True)
            snap={"position":0.,"active":[],"owner_health":{"mutation_ready":True}}
            patch(driver,"snapshot",side_effect=lambda *a,**kw:copy.deepcopy(snap))
            patch(driver,"maybe_renew",return_value=None)
            quote={"authoritative":True,"source":"IB","stale":False,"bid":1.,"ask":1.1,"observed_at_ms":self.NOW}
            native=patch(driver,"native",side_effect=lambda *a,**kw:dict(quote))
            action_iter=iter(actions);now=self.NOW
            class Worker:
                def __init__(self,openclaw,state_dir,history,position):
                    self.position=position;self.observed_at_ms=now
                    self.decision={"action":next(action_iter,"HOLD"),"confidence":1.}
                def done(self):return True
                def wait_for_completion(self,timeout):driver.time.sleep(timeout)
                def cancel(self,reason):pass
                def close(self):pass
                def result(self):return dict(self.decision)
            patch(driver,"ModelWorker",Worker)
            timeline=[];writes=[];atomic=driver.atomic_json
            def persist(path,value):
                atomic(path,value);saved=copy.deepcopy(value);writes.append(saved)
                timeline.append(("persist",saved.get("last_entry_guard")))
            patch(driver,"atomic_json",side_effect=persist)
            def run_guard(argv,timeout):
                timeline.append(("paper-on",list(argv)))
                if error is not None:raise error
                return result
            run=patch(driver,"run",side_effect=run_guard)
            send=patch(driver,"prepare_send",side_effect=lambda *a,**kw:timeline.append(("send",None)))
            handlers={}
            patch(driver.signal,"signal",side_effect=lambda sig,handler:handlers.update({sig:handler}))
            ticks=[0]
            def sleep(_):
                ticks[0]+=1
                if ticks[0]==loops:handlers[signal.SIGTERM](signal.SIGTERM,None)
                if ticks[0]>loops:raise AssertionError("loop did not stop")
            patch(driver.time,"sleep",side_effect=sleep);patch(sys,"argv",["driver","--instrument","BTC.USD"])
            self.assertEqual(driver.main(),0);self.assertEqual(ticks[0],loops)
            self.assertEqual(json.loads(state_path.read_text()),state)
            self.assertTrue(all(c.args[1]=="market.get_quote" for c in native.call_args_list))
            return dict(state=copy.deepcopy(state),writes=writes,timeline=timeline,run=run,send=send,
                        cli=str(home/".local/bin/heptatrader"))

    def test_structured_reason_fallback_and_bounds(self):
        cases=[({"status":"HOLD_BLOCKED","reason_code":"SPECIFIC","reason":"fallback"},"","HOLD_BLOCKED","SPECIFIC"),
            ({"state":"GUARD_DENIED"},"","GUARD_DENIED","GUARD_DENIED"),
            ("not JSON"," native\nerror ","ERROR","native error"),
            ({"status":"S"*120,"reason":"R"*900},"","S"*64,"R"*256)]
        for payload,stderr,status,reason in cases:
            with self.subTest(status=status[:10]),TemporaryDirectory() as tmp:
                p=Path(tmp)/"state.json";state={"unrelated":"retained"}
                result=subprocess.CompletedProcess([],75,json.dumps(payload) if isinstance(payload,dict) else payload,stderr)
                with mock.patch.object(driver,"now_ms",return_value=self.NOW):
                    o=driver.record_entry_guard(state,p,result,"BUY"+"X"*30)
                self.assertEqual(o["status"],status);self.assertEqual(o["reason"],reason)
                self.assertEqual(o["decision_action"],"BUY"+"X"*13)
                self.assertEqual(o["checked_at_ms"],self.NOW)
                self.assertEqual(json.loads(p.read_text()),state)

    def test_failed_actual_check_persists_without_order(self):
        for err in (None,subprocess.TimeoutExpired("heptatrader paper-on",10)):
            with self.subTest(timeout=err is not None):
                r=self.run_loop(error=err);o=r["state"]["last_entry_guard"]
                r["run"].assert_called_once_with([r["cli"],"paper-on"],10);r["send"].assert_not_called()
                self.assertTrue(any(s.get("last_entry_guard")==o for s in r["writes"]))
                self.assertEqual(o["scope"],"LAST_ACTUAL_ENTRY_GUARD_CHECK")
                if err is None:
                    self.assertEqual(o["returncode"],75)
                    self.assertIn(o["reason"],r["state"]["last_error"])
                else:
                    self.assertIsNone(o["returncode"]);self.assertEqual(o["status"],"CALL_ERROR")
                    self.assertIn("timed out",o["reason"]);self.assertIn("timed out",r["state"]["last_error"])

    def test_real_hold_and_sell_preserve_last_failed_check(self):
        for action in ("HOLD","SELL"):
            with self.subTest(action=action):
                r=self.run_loop(actions=("BUY",action),loops=4);state=r["state"]
                r["run"].assert_called_once_with([r["cli"],"paper-on"],10);r["send"].assert_not_called()
                self.assertEqual(state["last_decision"]["action"],action);self.assertEqual(state["last_error"],"")
                obs=[s["last_entry_guard"] for s in r["writes"] if "last_entry_guard" in s]
                self.assertTrue(obs);self.assertTrue(all(x==obs[0] for x in obs))
                self.assertEqual(state["last_entry_guard"],obs[0]);self.assertEqual(obs[0]["checked_at_ms"],self.NOW)

    def test_success_replaces_old_check_before_original_open(self):
        old={"checked_at_ms":self.NOW-1000,"returncode":75,"status":"HOLD_BLOCKED","reason":"OLD",
            "decision_action":"BUY","scope":"LAST_ACTUAL_ENTRY_GUARD_CHECK"}
        r=self.run_loop(result=subprocess.CompletedProcess([],0,"",""),initial_guard=old)
        o=r["state"]["last_entry_guard"]
        r["run"].assert_called_once_with([r["cli"],"paper-on"],10);r["send"].assert_called_once()
        self.assertEqual(o["returncode"],0);self.assertEqual(o["status"],"OK");self.assertEqual(o["reason"],"")
        self.assertEqual(o["checked_at_ms"],self.NOW)
        args=r["send"].call_args.args;self.assertEqual(args[6],"OPEN");self.assertEqual(args[8],"BUY")
        i=next(i for i,e in enumerate(r["timeline"]) if e[0]=="send")
        self.assertEqual(r["timeline"][i-1],("persist",o))

if __name__=="__main__":unittest.main()
