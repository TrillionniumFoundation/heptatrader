from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from hepta_research.gateway import LimitIntent, Outbox, StrategyGateway, HeptactlTransport, _loads


def envelope(tool,status="ok",payload=None):
    return dict(status=status,tool=tool,reason_code="",detail="",order_id=-1,payload=payload)

class FakeTransport:
    """Fault injection double, NOT a claim about a running Gateway."""
    def __init__(self):
        self.calls=[];self.scope_value="a"*64;self.fail=False;self.crash=False;self.on_place=None
        self.preview=dict(approved=True,single_use=True,command_id="execution-issued-12345",
                          preview_permit="fixture-only-permit",permit_expires_at_ms=5000,
                          service_epoch="fixture-epoch",service_fencing_generation=1)
    def scope(self):return self.scope_value
    def call(self,tool,call_id,fields):
        self.calls.append((tool,call_id,dict(fields)))
        if tool=="risk.preview_order":return envelope(tool,payload=dict(self.preview))
        if tool=="trade.place_order":
            if self.on_place:self.on_place()
            if self.crash:raise KeyboardInterrupt()
            if self.fail:raise TimeoutError()
        return envelope(tool)

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"outbox";self.outbox=Outbox(str(self.path))
        self.transport=FakeTransport();self.client=StrategyGateway(self.transport,self.outbox,lambda:1000)
        self.intent=LimitIntent("EUR.USD","EUR","CASH","IDEALPRO","USD","BUY","10","1.1002","1.1001",5000)
    def record(self):return next(self.path.glob("*.json"))
    def test_prepare_only_and_private_record(self):
        r=self.client.prepare("intent",self.intent)
        self.assertEqual([c[0] for c in self.transport.calls],["risk.preview_order"])
        self.assertEqual(r["command_id"],"execution-issued-12345")
        self.assertEqual(stat.S_IMODE(self.record().stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o700)
    def test_exact_id_fields_and_single_placement(self):
        self.client.prepare("intent",self.intent)
        def inspect():self.assertEqual(json.loads(self.record().read_text())["state"],"sending")
        self.transport.on_place=inspect
        self.client.submit("intent");self.client.submit("intent")
        calls=self.transport.calls
        self.assertEqual([c[0] for c in calls],["risk.preview_order","trade.place_order","execution.get_command_status"])
        self.assertEqual(calls[1][1],"execution-issued-12345")
        self.assertEqual({k:v for k,v in calls[1][2].items() if k!="preview_permit"},calls[0][2])
    def test_reprepare_cannot_replace_expiry_or_quantity(self):
        self.client.prepare("intent",self.intent);self.client.prepare("intent",self.intent)
        for changed in (replace(self.intent,expires_at_ms=5001),replace(self.intent,quantity="11")):
            with self.assertRaises(ValueError):self.client.prepare("intent",changed)
        self.assertEqual(len(self.transport.calls),1)
    def test_lost_response_after_restart_only_queries(self):
        self.client.prepare("intent",self.intent);self.transport.fail=True
        with self.assertRaises(RuntimeError):self.client.submit("intent")
        restarted=StrategyGateway(self.transport,Outbox(str(self.path)),lambda:1001)
        restarted.submit("intent")
        self.assertEqual([c[0] for c in self.transport.calls].count("trade.place_order"),1)
        self.assertEqual(self.transport.calls[-1][0],"execution.get_command_status")
    def test_crash_preserves_sending_state(self):
        self.client.prepare("intent",self.intent);self.transport.crash=True
        with self.assertRaises(KeyboardInterrupt):self.client.submit("intent")
        self.assertEqual(json.loads(self.record().read_text())["state"],"sending")
        self.client.submit("intent");self.assertEqual(self.transport.calls[-1][0],"execution.get_command_status")
    def test_persistence_failure_prevents_send(self):
        self.client.prepare("intent",self.intent)
        with patch.object(self.outbox,"save",side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):self.client.submit("intent")
        self.assertEqual(len(self.transport.calls),1)
    def test_receipt_persistence_failure_never_reissues(self):
        self.client.prepare("intent",self.intent);save=self.outbox.save
        def fail_after_send(fd,name,record):
            if record["state"]=="answered":raise OSError("disk")
            save(fd,name,record)
        with patch.object(self.outbox,"save",side_effect=fail_after_send):
            with self.assertRaises(OSError):self.client.submit("intent")
        self.client.submit("intent");self.assertEqual(self.transport.calls[-1][0],"execution.get_command_status")
    def test_expired_unsent_record_no_placement(self):
        self.client.prepare("intent",self.intent);self.client.clock_ms=lambda:5000
        with self.assertRaises(ValueError):self.client.submit("intent")
        self.assertEqual(len(self.transport.calls),1)
    def test_session_change_rejected(self):
        self.client.prepare("intent",self.intent);self.transport.scope_value="b"*64
        with self.assertRaises(ValueError):self.client.submit("intent")
        self.assertEqual(len(self.transport.calls),1)
    def test_mutated_intent_and_unknown_state_fail_closed(self):
        self.client.prepare("intent",self.intent);original=json.loads(self.record().read_text())
        for bad in (dict(original,state="other"),dict(original,fields={"quantity":"20"})):
            self.record().write_text(json.dumps(bad))
            with self.assertRaises(ValueError):self.client.submit("intent")
        self.assertEqual(len(self.transport.calls),1)
    def test_bad_preview_fails_before_record(self):
        for key,value in (("approved",False),("single_use",False),("preview_permit",""),
                          ("command_id","bad/id"),("permit_expires_at_ms",True),("service_fencing_generation",0)):
            saved=dict(self.transport.preview);self.transport.preview[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.client.prepare("intent",self.intent)
            self.transport.preview=saved
        self.assertFalse(list(self.path.glob("*.json")))
    def test_two_concurrent_submitters_one_placement(self):
        self.client.prepare("intent",self.intent)
        with ThreadPoolExecutor(2) as executor:list(executor.map(lambda _:self.client.submit("intent"),range(2)))
        self.assertEqual([c[0] for c in self.transport.calls].count("trade.place_order"),1)
    def test_symlink_and_hardlink_records_rejected(self):
        self.client.prepare("intent",self.intent);p=self.record();target=p.with_suffix(".copy")
        p.rename(target);p.symlink_to(target)
        with self.assertRaises(OSError):self.client.submit("intent")
        p.unlink();os.link(target,p)
        with self.assertRaises(ValueError):self.client.submit("intent")
    def test_bad_outbox_permissions(self):
        self.path.chmod(0o755)
        with self.assertRaises(ValueError):Outbox(str(self.path))
    def test_unknown_key_never_prepares_implicitly(self):
        with self.assertRaises(ValueError):self.client.submit("new")
        self.assertEqual(self.transport.calls,[])
    def test_reject_unsupported_contract_and_bad_numeric(self):
        for changed in (replace(self.intent,sec_type="FUT"),replace(self.intent,quantity="nan"),
                        replace(self.intent,side="FLATTEN"),replace(self.intent,expires_at_ms=True)):
            with self.assertRaises(ValueError):self.client.prepare("intent",changed)
        self.assertEqual(self.transport.calls,[])

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.token=self.root/"token";self.token.write_text("fixture-token-only");self.token.chmod(0o600)
        self.binary=self.root/"fake-heptactl"
    def transport(self,result,code=0):
        self.binary.write_text("#!/usr/bin/python3\nimport os,sys,json\nassert 'HEPTA_TOOL_SESSION_TOKEN' not in os.environ\nassert 'BROKER_PASSWORD' not in os.environ\nprint("+repr(json.dumps(result))+")\nsys.exit("+str(code)+")\n")
        self.binary.chmod(0o755)
        return HeptactlTransport(str(self.binary),str(self.root/"tools.sock"),str(self.token))
    def test_real_subprocess_uses_clean_environment(self):
        t=self.transport(envelope("risk.preview_order"))
        with patch.dict(os.environ,{"HEPTA_TOOL_SESSION_TOKEN":"must-not-inherit","BROKER_PASSWORD":"must-not-inherit"}):
            self.assertEqual(t.call("risk.preview_order","test-id",{})["status"],"ok")
    def test_duplicate_uses_existing_cli_exit_code(self):
        t=self.transport(envelope("trade.place_order","duplicate"),7)
        self.assertEqual(t.call("trade.place_order","test-id",{})["status"],"duplicate")
    def test_wrong_tool_and_status(self):
        for result in (envelope("another.tool"),envelope("risk.preview_order","unknown")):
            with self.subTest(result=result),self.assertRaises(ValueError):self.transport(result).call("risk.preview_order","test-id",{})
    def test_invalid_json(self):
        for data in (b'{"a":1,"a":2}',b'{"a":NaN}',b'[]'):
            with self.subTest(data=data),self.assertRaises(ValueError):_loads(data)
    def test_fifo_token_rejected_without_waiting_for_writer(self):
        t=self.transport(envelope("risk.preview_order"))
        self.token.unlink(); os.mkfifo(self.token, 0o600)
        with self.assertRaises(ValueError): t.scope()
    def test_token_scope_binds_session_and_permissions(self):
        t=self.transport(envelope("risk.preview_order"));first=t.scope()
        self.token.write_text("different-fixture-token");self.assertNotEqual(t.scope(),first)
        self.token.chmod(0o644)
        with self.assertRaises(ValueError):t.scope()
