"""Real Capture/gate/filesystem, fixture actor/native data; never sends orders."""
from contextlib import ExitStack, redirect_stderr
import copy, hashlib, importlib.util, io, json, os, shutil, sys, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(Path(__file__).resolve().parent))
import audit_price_bootstrap_gate as audit
import hepta_price_bootstrap_gate as gate
import hepta_decision_attribution as attribution
from test_pnl_accounting import period,send,fill


class PriceAuditTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        box=tempfile.TemporaryDirectory();self.addCleanup(box.cleanup);self.root=Path(box.name)
        spec=importlib.util.spec_from_file_location('audit_fixture_driver',ROOT/'scripts/heptatrader_paper_driver.py')
        self.driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.driver)
        self.period=self.root/'period.json';self.private(self.period,attribution.canonical(period())+'\n')
        runtime={}
        for name in ('hepta_price_bootstrap_gate.py','hepta_decision_attribution.py','heptatrader_paper_driver.py',
                     'heptatrader_attributed_driver.py','hepta_native_reader.py','hepta_verified_loader.py'):
            raw=(ROOT/'scripts'/name).read_bytes();self.private(self.root/name,raw);runtime[name]=audit.sha(raw)
        self.manifest={'schema':'heptatrader.attribution-producer.v1','instrument':'EUR.USD','execution_domain':'PAPER',
            'runtime_loader':'verified-bytes.v1','period_path':str(self.period),'period_sha256':audit.sha(self.period.read_bytes()),
            'runtime_sha256':runtime,'journal_directory':str(self.root/'journal'),
            'price_bootstrap_gate':{'schema':gate.SELECTION_SCHEMA,'profile':gate.PROFILE,'receipt_root':str(self.root/'price')}}
        self.manifest_path=self.root/'manifest.json';self.private(self.manifest_path,attribution.canonical(self.manifest)+'\n')
        self.producer=audit.sha(self.manifest_path.read_bytes());self.calls=[]
        self.response={'action':'BUY','confidence':.75,'rationale':'fixture original actor'}
        def actor(*args,**kwargs):
            return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'outputs':[{'text':json.dumps(self.response)}]}))
        def native(binary,socket,token,outbox,operation,extra):
            self.calls.append(operation)
            if operation.startswith('prepare-'):return {'ok':True,'command_id':'fixture-command-'+str(len(self.calls))}
            if operation=='submit':return {'ok':True,'transport_ok':True,'application_status':'ok','result':{'order_id':123}}
            raise AssertionError('unexpected fixture transport '+operation)
        self.driver.run=actor;self.driver.paper_cmd=native
        self.gate=gate.PriceBootstrapGate(self.driver,self.manifest,self.producer);self.addCleanup(self.gate.install())
        self.journal=attribution.Journal(self.root/'journal',self.manifest,self.producer)
        self.capture=attribution.Capture(self.journal,self.driver,self.manifest);self.addCleanup(self.capture.install())
        self.stack.enter_context(redirect_stderr(io.StringIO()))
        now=gate.wall_ms();self.base_observed=now-1000
        self.history=[{'observed_at_ms':now-200,'bid':1.1,'ask':1.1002,'mid':(1.1+1.1002)/2},
                      {'observed_at_ms':now-100,'bid':1.1,'ask':1.1002,'mid':(1.1+1.1002)/2}]
        self.oms=self.root/'oms.jsonl';self.base=b'{"fixture_old_prefix":true}\n';self.private(self.oms,self.base)
        self.state_path=self.root/'state.json'

    def private(self,path,raw):
        path.write_bytes(raw.encode() if isinstance(raw,str) else raw);path.chmod(0o600)

    def decision(self):
        worker=self.driver.ModelWorker('/fixture/actor',self.root,self.history,0)
        worker._thread.join(5);self.assertTrue(worker.done());return worker.result()

    def prepare(self):
        decision=self.decision();state=self.driver.load_state(self.state_path);state['last_decision']=decision
        self.driver.prepare_send(state,self.state_path,'fixture','fixture','fixture','fixture','OPEN',self.history[-1],'BUY')
        self.journal.close();self.folder=self.gate.entries[id(decision)]['folder']
        self.binding=json.loads((self.folder/'command-binding.json').read_bytes())
        return state

    def native_rows(self,state,*,include_fill=True):
        prepared=self.binding['prepared_command']
        captured=max(row['captured_at_ms'] for row in self.journal_rows())
        s=send(state['pending']['command_id'],'EUR.USD','BUY',at=max(captured,prepared['created_at_ms'])+2,oid=123)
        return [s,fill(s,qty=1,price=1.1001,at=s['ts_ms']+2)] if include_fill else [s]

    def oms_rows(self,rows,partial=b''):
        self.oms.write_bytes(self.base+b''.join((attribution.canonical(row)+'\n').encode() for row in rows)+partial)

    def journal_rows(self):
        return [json.loads(line) for path in sorted((self.root/'journal').glob('*.jsonl')) for line in path.read_bytes().splitlines()]

    def run_audit(self,**kwargs):
        self.journal.close()
        return audit.audit(self.manifest_path,self.producer,self.oms,len(self.base),audit.sha(self.base),self.base_observed,
            initial_writer=self.journal.id,checked_at_ms=gate.wall_ms()+1000,**kwargs)

    def test_hold_waits_without_reading_native_prefix_or_writing(self):
        self.response.update(action='HOLD',confidence=.78);self.decision();self.journal.close()
        before={str(path):(path.stat().st_mode,path.stat().st_mtime_ns,path.read_bytes()) for path in self.root.rglob('*') if path.is_file()}
        with patch.object(audit,'read_oms_range',side_effect=AssertionError('ordinary observation rehashed OMS')):out=self.run_audit()
        after={str(path):(path.stat().st_mode,path.stat().st_mtime_ns,path.read_bytes()) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(out['status'],'WAITING');self.assertEqual(out['counts']['decision_actions'],{'HOLD':1})
        self.assertIsNone(out['counts']['observed_acceptance_fraction']);self.assertIsNone(out['counts']['observed_native_open_commands'])
        self.assertFalse(out['scope']['native_prefix_parsed']);self.assertEqual(before,after);self.assertEqual(self.calls,[])

    def test_low_confidence_waits_without_missing_prediction_claim(self):
        self.response['confidence']=.4;self.decision();out=self.run_audit()
        self.assertEqual(out['status'],'WAITING');self.assertEqual(out['cases'],[])

    def test_real_gate_original_journal_native_fill_chain(self):
        state=self.prepare();self.oms_rows(self.native_rows(state));out=self.run_audit()
        self.assertEqual(out['status'],'VERIFIED_OBSERVED',out)
        case=out['cases'][0];self.assertEqual(case['native']['command_id'],state['pending']['command_id'])
        self.assertEqual(out['counts']['verified_open_first_fill_chains'],1)
        self.assertGreater(out['native']['bytes_after_anchor_observed'],0)
        self.assertEqual(out['native']['unverified_bytes_after_anchor'],0)
        self.assertEqual(set(case['pins']),{'input.json','prediction.json','seal.json','admission.json','command-binding.json'})
        self.assertLess(case['times']['admission_fsync_completed_at_ms'],case['times']['original_prepared_created_at_ms'])
        self.assertLess(case['times']['admission_fsync_completed_at_ms'],case['native']['first_fill_at_ms'])
        self.assertFalse(out['value_coverage_credit']);self.assertFalse(case['native']['ib_server_execution_time_relation_proven'])

    def test_native_duplicate_execution_replay_does_not_add_denominator(self):
        state=self.prepare();rows=self.native_rows(state);self.oms_rows(rows*3);out=self.run_audit()
        self.assertEqual(out['status'],'VERIFIED_OBSERVED',out)
        self.assertEqual(out['counts']['observed_native_open_commands'],1)
        self.assertEqual(out['cases'][0]['native']['execution_ids'],['e1.1'])

    def test_native_send_waits_for_first_fill(self):
        state=self.prepare();self.oms_rows(self.native_rows(state,include_fill=False));out=self.run_audit()
        self.assertEqual(out['status'],'PARTIAL');self.assertEqual(out['cases'][0]['status'],'WAITING_NATIVE_FIRST_FILL')

    def test_private_seal_does_not_pretend_native_send(self):
        self.prepare();out=self.run_audit()
        self.assertEqual(out['status'],'PARTIAL');self.assertEqual(out['cases'][0]['status'],'WAITING_NATIVE_SEND')

    def test_partial_capture_is_separate_from_missing_open_receipt(self):
        self.decision();out=self.run_audit()
        self.assertEqual(out['status'],'PARTIAL');self.assertEqual(out['cases'][0]['status'],'PARTIAL_PRIVATE_RECEIPT')
        self.assertFalse(out['scope']['native_prefix_parsed'])

    def test_missing_seal_on_real_bound_open_fails(self):
        state=self.prepare();self.oms_rows(self.native_rows(state));(self.folder/'seal.json').unlink();out=self.run_audit()
        self.assertEqual(out['status'],'FAILED');self.assertEqual(out['cases'][0]['status'],'MISSING_PREOPEN_SEAL')

    def test_old_producer_private_folder_not_borrowed(self):
        self.response['action']='HOLD';self.decision();old=self.root/'price'/('f'*64)/('a'*32)
        old.mkdir(parents=True);(old/'command-binding.json').write_text('{}');out=self.run_audit()
        self.assertEqual(out['status'],'WAITING');self.assertEqual(out['cases'],[])

    def test_changed_private_producer_or_hash_refused(self):
        state=self.prepare();self.oms_rows(self.native_rows(state));path=self.folder/'prediction.json'
        value=json.loads(path.read_bytes());value['producer_manifest_sha256']='f'*64
        path.chmod(0o600);path.write_text(attribution.canonical(value)+'\n');path.chmod(0o400)
        out=self.run_audit();self.assertEqual(out['status'],'FAILED');self.assertEqual(out['cases'][0]['status'],'INVALID')

    def test_late_native_send_refused(self):
        state=self.prepare();rows=self.native_rows(state)
        rows[0]['ts_ms']=self.binding['prepared_command']['created_at_ms']-1
        self.oms_rows(rows);out=self.run_audit()
        self.assertEqual(out['status'],'FAILED');self.assertEqual(out['cases'][0]['status'],'LATE')

    def test_native_side_binding_conflict_refused(self):
        state=self.prepare();rows=self.native_rows(state);rows[1]['side']='SELL';self.oms_rows(rows)
        out=self.run_audit();self.assertEqual(out['status'],'FAILED');self.assertIn('BINDING_CONFLICT',out['cases'][0]['reason'])

    def test_native_prefix_changed_refused(self):
        self.prepare();self.oms.write_bytes(self.base.replace(b'true',b'null'))
        with self.assertRaisesRegex(audit.AuditError,'OMS_BASE_CHANGED'):self.run_audit()

    def test_native_partial_line_never_is_a_fill(self):
        state=self.prepare();self.oms_rows(self.native_rows(state,include_fill=False),partial=b'{"event":"broker_execution"')
        out=self.run_audit();self.assertEqual(out['cases'][0]['status'],'WAITING_NATIVE_FIRST_FILL')
        self.assertGreater(out['native']['pinned_prefix']['trailing_uncommitted_bytes'],0)

    def test_native_close_not_counted_as_new_open(self):
        self.response['action']='HOLD';self.decision()
        self.oms_rows([dict(send('fixture-close','EUR.USD'),event='flatten_sent')]);out=self.run_audit()
        self.assertEqual(out['status'],'WAITING');self.assertFalse(out['scope']['native_prefix_parsed'])
        self.assertGreater(out['native']['unverified_bytes_after_anchor'],0)

    def test_restart_same_manifest_writer_discovered_and_explicit_foreign_rejected(self):
        self.response['action']='HOLD';self.decision();self.journal.close()
        restarted=attribution.Journal(self.root/'journal',self.manifest,self.producer);restarted.close()
        foreign=attribution.Journal(self.root/'journal',self.manifest,'f'*64);foreign.close()
        out=self.run_audit();self.assertEqual(set(out['journal']['writer_ids']),{self.journal.id,restarted.id})
        self.assertEqual(out['status'],'WAITING')
        path=next((self.root/'journal').glob(foreign.id+'-*.jsonl'))
        with self.assertRaisesRegex(audit.AuditError,'FOREIGN_PRODUCER_JOURNAL'):self.run_audit(journal_files=[path])

    def test_missing_initial_writer_is_not_silently_replaced(self):
        self.response['action']='HOLD';self.decision();self.journal.close()
        with self.assertRaisesRegex(audit.AuditError,'INITIAL_WRITER_NOT_FOUND'):
            audit.journal_snapshot(self.manifest,self.producer,gate.wall_ms()+1000,initial_writer='f'*32)

    def test_clock_order_and_true_post_fsync_are_required(self):
        self.prepare();values={};pins={}
        for name in ('input.json','prediction.json','seal.json','admission.json','command-binding.json'):
            values[name],pins[name]=audit.record(self.folder/name)
        binding=copy.deepcopy(values['command-binding.json'])
        binding['prepared_command']['price_bootstrap_forecast']['admission_durable_completed_at_ms']=binding['prepared_command']['created_at_ms']+1
        with self.assertRaisesRegex(audit.AuditError,'LATE_OR_REVERSED'):
            audit.check_timing(values['input.json'],values['prediction.json'],values['seal.json'],values['admission.json'],binding,pins)

    def test_missing_asynchronous_binding_cannot_be_inferred_from_private_receipt(self):
        state=self.prepare();self.oms_rows(self.native_rows(state));path=next((self.root/'journal').glob('*.jsonl'))
        rows=[row for row in self.journal_rows() if row['kind']!='COMMAND_BINDING'];previous='0'*64
        for index,row in enumerate(rows):
            row.update(sequence=index,previous=previous);row['sha256']=attribution.digest({k:v for k,v in row.items() if k!='sha256'});previous=row['sha256']
        path.write_text(''.join(attribution.canonical(row)+'\n' for row in rows));out=self.run_audit()
        self.assertEqual(out['status'],'PARTIAL');self.assertEqual(out['cases'][0]['status'],'MISSING_JOURNAL_BINDING')
        self.assertEqual(out['counts']['verified_open_first_fill_chains'],0)

    def test_real_local_pre_submit_recovery_is_not_broker_rejection_or_fill(self):
        decision=self.decision();entry=self.gate.entries[id(decision)]
        state=self.driver.load_state(self.state_path);state['last_decision']=decision
        with patch.object(self.driver,'now_ms',return_value=entry['sealed_at_ms']-1):
            with self.assertRaisesRegex(gate.GateError,'PREPARED_TIME_NOT_AFTER'):
                self.driver.prepare_send(state,self.state_path,'fixture','fixture','fixture','fixture','OPEN',self.history[-1],'BUY')
        def native(cli,tool,*args):
            if tool=='system.get_health':return {'execution_mode':'PAPER','gateway_ready':True,'remote_execution_ready':True,
                'execution_authoritative_health':{'source':'IB','authoritative':True,'owner_fenced':False,
                    'owner_recovery_only':False,'mutation_ready':True},'execution_service_epoch':'fixture','execution_service_fencing_generation':1}
            if tool=='orders.list':return {'source':'IB','authoritative':True,'global_active_orders_complete':True,
                'owner_projection_complete':True,'active_order_ids':[],'recent_orders':[]}
            if tool=='portfolio.list_positions':return {'source':'IB','authoritative':True,'positions':[{'instrument':'EUR.USD','quantity':0}]}
            raise AssertionError(tool)
        with patch.object(self.driver,'native',side_effect=native):snap=self.driver.snapshot('fixture',require_quote=False)
        self.driver.inspect_pending(state,self.state_path,'fixture','fixture','fixture','fixture',snap)
        self.assertIsNone(state['pending']);self.assertEqual(self.calls,['prepare-open'])
        out=self.run_audit();self.assertEqual(out['status'],'PARTIAL')
        self.assertEqual(out['cases'][0]['status'],'LOCAL_PRE_SUBMIT_NO_SEND_RECORDED')
        self.assertFalse(out['cases'][0]['fresh_snapshot_independently_replayed'])

    def test_unmatched_native_open_cannot_inflate_verified_fraction(self):
        state=self.prepare();rows=self.native_rows(state)
        rows.append(send('unmapped-open','EUR.USD','BUY',at=rows[-1]['ts_ms']+1,oid=124))
        self.oms_rows(rows);out=self.run_audit()
        self.assertEqual(out['status'],'FAILED');self.assertEqual(out['native']['unmatched_open_command_ids'],['unmapped-open'])
        self.assertEqual(out['counts']['observed_acceptance_fraction'],.5)

    def test_original_send_attempt_cannot_precede_private_command_binding(self):
        state=self.prepare();rows=self.native_rows(state)
        rows.insert(0,dict(rows[0],event='place_send_attempt',ts_ms=self.binding['prepared_command']['created_at_ms']-1))
        self.oms_rows(rows);out=self.run_audit()
        self.assertEqual(out['status'],'FAILED');self.assertEqual(out['cases'][0]['reason'],'LATE_NATIVE_SEND_ATTEMPT')

    def test_one_request_cannot_bind_a_second_open_even_without_second_fill(self):
        state=self.prepare();self.oms_rows(self.native_rows(state));path=next((self.root/'journal').glob('*.jsonl'))
        rows=self.journal_rows();extra=copy.deepcopy(next(row for row in rows if row['kind']=='COMMAND_BINDING'))
        extra['data']['command']['command_id']='other-prepared-command'
        extra.update(sequence=len(rows),previous=rows[-1]['sha256'])
        extra['sha256']=attribution.digest({k:v for k,v in extra.items() if k!='sha256'})
        with path.open('a') as stream:stream.write(attribution.canonical(extra)+'\n')
        out=self.run_audit();self.assertEqual(out['status'],'FAILED')
        self.assertEqual(out['cases'][0]['reason'],'REQUEST_REUSED_FOR_MULTIPLE_OPEN_COMMANDS')


if __name__=='__main__':unittest.main()
