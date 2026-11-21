"""Synthetic transport probes; these are not financial outcomes or live quotes."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from laya_cell.shadow_source import (
    collect, native_quote_diagnostic, parse_native_quote_reply, read_source,
)

T = 1791330000000


def quote(at=T):
    return {'instrument':'EUR.USD','source':'IB','authoritative':True,'stale':False,
            'bid':'1.12500','ask':'1.12502','observed_at_ms':at,'stale_after_ms':at+5000,
            'subscription_id':'SYNTHETIC-UNIT-FIXTURE','subscription_state':'active'}


class NativeQuoteDiagnosticsTests(unittest.TestCase):
    def reject(self, code, stdout, stderr, reason, received=T+1):
        with self.assertRaisesRegex(ValueError, '^'+reason+'$'):
            parse_native_quote_reply(code, stdout, received, 'EUR.USD', stderr=stderr)

    def test_native_pre_envelope_failure_keeps_exact_code(self):
        self.reject(4, '', 'DISCOVERY_LIST_REJECTED\n', 'DISCOVERY_LIST_REJECTED')

    def test_verified_launcher_error_json_is_diagnostic_only(self):
        error=json.dumps({'status':'error','reason_code':'BINARY_SHA_MISMATCH'})
        self.reject(1, '', error, 'BINARY_SHA_MISMATCH')
        d=native_quote_diagnostic(1,'',error)
        self.assertEqual(d['reason_origin'],'STDERR_DIAGNOSTIC')
        self.assertTrue(d['diagnostic_only_not_quote_authority'])

    def test_stdout_error_envelope_precedes_stderr(self):
        self.reject(1,json.dumps({'status':'error','reason_code':'UPSTREAM_UNAVAILABLE'}),
                    'DISCOVERY_LIST_REJECTED\n','UPSTREAM_UNAVAILABLE')

    def test_nonzero_exit_never_accepts_quote_shaped_stdout(self):
        self.reject(1,json.dumps({'status':'ok','payload':quote()}),'','QUOTE_TOOL_EXIT_NONZERO')

    def test_stderr_quote_is_never_a_quote_source(self):
        valid=json.dumps({'status':'ok','payload':quote()})
        self.reject(1,'',valid,'QUOTE_TOOL_EXIT_NONZERO')
        self.reject(0,'',valid,'QUOTE_TOOL_JSON_INVALID')

    def test_unknown_private_stderr_is_not_logged(self):
        secret='/private/path/token=do-not-persist\nTraceback: error'
        self.reject(7,'garbled',secret,'QUOTE_TOOL_EXIT_NONZERO')
        d=native_quote_diagnostic(7,'garbled',secret)
        self.assertNotIn(secret,json.dumps(d))
        self.assertEqual(d['stderr_chars'],len(secret))
        self.assertEqual(d['stderr_text_sha256'],hashlib.sha256(secret.encode()).hexdigest())

    def test_signal_and_invalid_exit_type_remain_failures(self):
        self.reject(-9,'','','QUOTE_TOOL_SIGNALLED')
        self.reject(True,'','','QUOTE_TOOL_RESPONSE_TYPE')

    def test_ambiguous_or_unbounded_diagnostic_cannot_supply_a_code(self):
        for raw in ('A\nB\n','{"status":"error","reason_code":"A","reason_code":"B"}',
                    '{"status":"error","reason_code":NaN}', 'X'*4097, '['*1100+']'*1100):
            with self.subTest(raw=raw[:60]):
                self.reject(1,'',raw,'QUOTE_TOOL_EXIT_NONZERO')

    def test_zero_exit_malformed_envelope_remains_json_error(self):
        self.reject(0,'not-json','DISCOVERY_LIST_REJECTED\n','QUOTE_TOOL_JSON_INVALID')

    def test_fresh_quote_is_accepted_without_promoting_stderr_authority(self):
        q=quote();raw=json.dumps({'status':'ok','payload':q})
        self.assertEqual(parse_native_quote_reply(0,raw,T+1,'EUR.USD',stderr='warning'),q)

    def test_quote_authority_and_expiry_checks_still_apply(self):
        for key,value,reason in [('stale',True,'QUOTE_AUTHORITY'),('authoritative',False,'QUOTE_AUTHORITY'),
                                 ('subscription_state','inactive','QUOTE_SUBSCRIPTION_NOT_ACTIVE'),
                                 ('bid',True,'QUOTE_NUMERIC')]:
            with self.subTest(key=key):
                self.reject(0,json.dumps({'status':'ok','payload':{**quote(),key:value}}),'',reason)
        self.reject(0,json.dumps({'status':'ok','payload':quote()}),'','QUOTE_STALE_OR_FUTURE',T+5000)

    def test_collector_records_failure_provenance_and_does_not_reuse_it_after_timeout(self):
        clock=[0.0];calls=[]
        with tempfile.TemporaryDirectory() as td:
            home=Path(td);period=home/'.config/heptatrader/pnl-period.json'
            period.parent.mkdir(parents=True);period.write_text('{"execution_domain":"PAPER"}')
            def run(argv,**kwargs):
                calls.append(argv)
                self.assertEqual(argv[-3:],['call','market.get_quote','instrument=EUR.USD'])
                clock[0]+=2
                if len(calls)==1:
                    return subprocess.CompletedProcess(argv,4,'','DISCOVERY_LIST_REJECTED\n')
                if len(calls)==2:
                    return subprocess.CompletedProcess(argv,0,json.dumps({'status':'ok','payload':quote(T+int(clock[0]*1000))}),'')
                raise subprocess.TimeoutExpired(argv,8)
            with patch('laya_cell.shadow_source.Path.home',return_value=home), \
                 patch('laya_cell.shadow_source.subprocess.run',side_effect=run), \
                 patch('laya_cell.shadow_source.time.time',side_effect=lambda:T/1000+clock[0]), \
                 patch('laya_cell.shadow_source.time.monotonic',side_effect=lambda:clock[0]), \
                 patch('laya_cell.shadow_source.time.sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)), \
                 patch('laya_cell.shadow_source.signal.signal'):
                collect(home/'capture',10)
            rows=[json.loads(x) for x in (home/'capture/quotes.jsonl').read_text().splitlines()]
            failures=[r['data'] for r in rows if r['kind']=='FAILURE']
            self.assertEqual(failures[0]['reason'],'DISCOVERY_LIST_REJECTED')
            self.assertEqual(failures[0]['tool_diagnostic']['exit_code'],4)
            self.assertTrue(all('tool_diagnostic' not in r for r in failures[1:]))
            _,quotes,prefix=read_source(home/'capture/quotes.jsonl')
            self.assertEqual(len(quotes),1)
            self.assertTrue(prefix['ended'])
            self.assertEqual(rows[-1]['data']['broker_orders_sent'],0)


if __name__=='__main__':unittest.main()
