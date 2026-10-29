"""Real bounded-tail reads of synthetic original records; no provider or GPU calls."""
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from historical_training.tests import test_value_priority_admission as fixtures
from historical_training import value_priority_admission as priority
from historical_training.common import EvidenceError, canonical, digest


class CompletedOriginalResponseTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.PriorityTest('test_original_flat_request_blocks_new_evaluator')
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.policy={**self.fixture.policy,'schema':priority.ACTUAL_POLICY_SCHEMA}
        self.now=self.fixture.now;self.path=self.fixture.journal_file
        self.producer='a'*32;self.reset()

    def write(self):
        self.path.write_bytes(b''.join(canonical(row)+b'\n' for row in self.records))

    def append(self,kind,data,captured,written):
        row={'schema':'heptatrader.exact-decision-attribution.v1','producer_id':self.producer,
             'sequence':len(self.records),'previous':self.records[-1]['sha256'] if self.records else '0'*64,
             'dropped_total':0,'captured_at_ms':captured,'written_at_ms':written,'kind':kind,'data':data}
        row['sha256']=digest(row);self.records.append(row);self.write();return row

    def request(self,started,rid='b'*32):
        return self.append('REQUEST',{'request_id':rid,'started_at_ms':started,'position_at_request':0.,
            'instrument':'EUR.USD','period_sha256':'fixedperiod','model_id':'synthetic',
            'prompt':'fixture','prompt_sha256':hashlib.sha256(b'fixture').hexdigest()},started+1,started+2)

    def response(self,request,code=1):
        available=request['data']['started_at_ms']+5000
        return self.append('RESPONSE',{'request_id':request['data']['request_id'],
            'request_sha256':digest(request['data']),'returncode':code,'stdout':'synthetic provider response',
            'available_at_ms':available},available+1,available+2)

    def reset(self,code=None):
        self.records=[];t=self.now-10000
        self.append('HEADER',{'manifest_sha256':json.loads(self.fixture.plan.read_bytes())['producer_registration']['sha256'],
            'instrument':'EUR.USD','period_sha256':'fixedperiod','execution_domain':'PAPER',
            'source_kind':'PINNED_DRIVER_CALLSITE','trading_authority':False},t-1,t-1)
        self.original=self.request(t)
        if code is not None:self.response(self.original,code)

    def rehash(self,row):
        row['sha256']=digest({k:v for k,v in row.items() if k!='sha256'});self.write()

    def held(self,role='evaluator'):
        result=priority.decision(self.policy,role,self.now)
        self.assertTrue(result[0]);self.assertEqual(result[1],'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST')
        self.assertEqual(result[2]['completed_failed_original_reservations_released'],0)

    def test_exact_completed_failure_releases_both_background_roles(self):
        for code in (1,-9):
            self.reset(code)
            for role in ('trainer','evaluator'):
                denied,reason,proof=priority.decision(self.policy,role,self.now)
                self.assertFalse(denied);self.assertEqual(reason,'VALUE_PRIORITY_NO_OBSERVED_PENDING_REQUEST')
                self.assertEqual(proof['pending_reservation_count'],0)
                self.assertEqual(proof['completed_failed_original_reservations_released'],1)
                self.assertTrue(proof['original_shared_flock_unmodified'])

    def test_missing_or_successful_response_keeps_the_original_reservation(self):
        for code in (None,0):
            self.reset(code)
            for role in ('trainer','evaluator'):self.held(role)

    def test_bad_response_identity_hash_sequence_or_schema_cannot_release(self):
        changes=(('data','request_id','c'*32),('data','request_sha256','f'*64),
                 ('record','producer_id','c'*32),('record','previous','f'*64),
                 ('record','sequence',5),('record','sequence',True),
                 ('record','schema','other'),('record','dropped_total',1),
                 ('record','dropped_total',False))
        for scope,key,value in changes:
            with self.subTest(scope=scope,key=key,value=value):
                self.reset(1);row=self.records[-1]
                (row['data'] if scope=='data' else row)[key]=value;self.rehash(row);self.held()
        self.reset(1);self.records[-1]['sha256']='f'*64;self.write();self.held()

    def test_future_or_impossible_response_times_cannot_release(self):
        changes=(('data','available_at_ms',self.now+1),('data','available_at_ms',self.now-10001),
                 ('data','available_at_ms',True),('record','captured_at_ms',self.now-6000),
                 ('record','written_at_ms',self.now-6000),('record','written_at_ms',self.now+1),
                 ('record','written_at_ms',True))
        for scope,key,value in changes:
            with self.subTest(scope=scope,key=key,value=value):
                self.reset(1);row=self.records[-1]
                (row['data'] if scope=='data' else row)[key]=value;self.rehash(row);self.held()

    def test_noninteger_or_incomplete_response_is_not_a_completed_failure(self):
        for code in (True,False,1.0,'1'):
            with self.subTest(code=code):self.reset(code);self.held()
        for key in ('returncode','available_at_ms','request_sha256','stdout'):
            with self.subTest(missing=key):
                self.reset(1);row=self.records[-1];del row['data'][key];self.rehash(row);self.held()

    def test_request_payload_scope_or_hash_must_match_the_failure(self):
        for key,value in (('period_sha256','other'),('instrument','BTC.USD'),('prompt_sha256','f'*64)):
            with self.subTest(key=key):
                self.reset();self.original['data'][key]=value;self.rehash(self.original)
                self.response(self.original);self.held()
        self.reset(1);self.original['data']['prompt']='altered';self.write();self.held()

    def test_duplicate_or_conflicting_request_or_response_keeps_reservation(self):
        for kind in ('duplicate-response','success-response','bad-response','duplicate-request','changed-request'):
            with self.subTest(kind=kind):
                self.reset(1)
                if kind=='success-response':self.response(self.original,0)
                else:
                    row=copy.deepcopy(self.original if 'request' in kind else self.records[-1])
                    if kind=='changed-request':row['data']['prompt']='different payload';self.rehash(row)
                    if kind=='bad-response':row['sha256']='f'*64
                    self.records.append(row);self.write()
                self.held()

    def test_malformed_complete_tail_line_blocks_early_release(self):
        for raw in (b'{broken}\n',b'{"kind":"RESPONSE","kind":"RESPONSE"}\n',
                    b'{"bad":NaN}\n',b'{"kind":"RESPONSE","data":null}\n'):
            with self.subTest(raw=raw):
                self.reset(1)
                with self.path.open('ab') as stream:stream.write(raw)
                self.held()

    def test_nonobject_source_cannot_become_an_empty_queue_admission(self):
        for raw in (b'null\n',b'[]\n',b'"record"\n'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaisesRegex(EvidenceError,'VALUE_PRIORITY_RECORD_SHAPE'):
                    priority.decision(self.policy,'evaluator',self.now)

    def test_new_request_cannot_borrow_the_previous_failure(self):
        self.reset(1);fresh=self.request(self.now-1000,'c'*32)
        denied,reason,proof=priority.decision(self.policy,'evaluator',self.now)
        self.assertTrue(denied);self.assertEqual(reason,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST')
        self.assertEqual(proof['pending_reservation_count'],1)
        self.assertEqual(proof['completed_failed_original_reservations_released'],1)
        self.assertEqual(proof['reservation_expires_ms'],fresh['data']['started_at_ms']+priority.VALUE_DEADLINE_MS)

    def test_cross_segment_or_nonadjacent_pair_does_not_release(self):
        self.reset(1);response=self.records.pop();self.write()
        other=self.path.with_name(self.producer+'-0001.jsonl')
        other.write_bytes(canonical(response)+b'\n');other.chmod(0o600);self.held();other.unlink()
        self.reset();self.append('UNBOUND_DECISION',{},self.now-7000,self.now-7000)
        self.response(self.original);self.held()

    def test_v1_keeps_its_original_reservation_and_read_bound_is_unchanged(self):
        self.reset(1);v1={**self.policy,'schema':priority.POLICY_SCHEMA}
        denied,reason,proof=priority.decision(v1,'evaluator',self.now)
        self.assertTrue(denied);self.assertEqual(reason,'VALUE_PRIORITY_FRESH_FLAT_ORIGINAL_REQUEST')
        self.assertNotIn('completed_failed_original_reservations_released',proof)
        with patch.object(priority,'_private_file',wraps=priority._private_file) as read:
            priority.decision(self.policy,'evaluator',self.now)
        tails=[call for call in read.call_args_list if call.kwargs.get('tail')]
        self.assertEqual(len(tails),1);self.assertEqual(tails[0].args[1],priority.MAX_FILE_BYTES)


if __name__=='__main__':unittest.main()
