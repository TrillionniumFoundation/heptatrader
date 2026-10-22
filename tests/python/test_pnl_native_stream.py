"""PnL keeps native discovery warm without changing its broker snapshot checks."""
from pathlib import Path
import json
import contextlib
import io
import sys
import tempfile
import unittest
from unittest import mock

import hepta_pnl_report as report
import test_pnl_accounting as fixtures


class NativeStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hepta-pnl-process-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        native = fixtures.CollectionTests().native
        fixture = {instrument: {tool: native(instrument, tool) for tool in report.READ_TOOLS}
                   for instrument in ('BTC.USD', 'EUR.USD')}
        (self.root / 'fixture.json').write_text(json.dumps(fixture))
        executable = self.root / 'bin/heptactl'
        executable.write_text('#!' + sys.executable + '\n' + r'''
import json,os,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
args=sys.argv[1:]
assert args[-1]=='pnl-read-stream'
assert args[args.index('--io-timeout-ms')+1]=='2000'
token=Path(args[args.index('--token-file')+1]).name
instrument='BTC.USD' if token=='crypto.token' else 'EUR.USD'
with (root/'calls').open('a') as f:f.write(json.dumps(['START',os.getpid(),token])+'\n')
fixture=json.loads((root/'fixture.json').read_text())[instrument]
counts={}
for line in sys.stdin:
    tool=line.strip().split()[0]
    with (root/'calls').open('a') as f:f.write(json.dumps(['CALL',os.getpid(),line.strip()])+'\n')
    mode=(root/'mode').read_text() if (root/'mode').exists() else ''
    if mode=='eof':sys.exit(3)
    if mode=='delay':time.sleep(.2)
    if mode=='oversize':sys.stdout.write('x'*1048578);sys.stdout.flush();continue
    counts[tool]=counts.get(tool,0)+1
    payload=fixture[tool].copy()
    if mode=='large':payload['padding']='x'*200000
    if mode=='drift' and tool=='orders.list' and counts[tool]>1:payload['active_orders_generation']=2
    value={'status':'ok','tool':tool,'payload':payload}
    if mode=='mismatch':value['tool']='trade.place_order'
    if mode=='schema':value.update(status='rejected',reason_code='SCHEMA_HASH_MISMATCH')
    print(json.dumps(value),flush=True)
''')
        executable.chmod(0o700)
        self.reader = report.NativeReader(self.root, self.root, Path('/unused-socket'), persistent=True)
        self.addCleanup(self.reader.close)
        # Explicit interpreter invocation keeps the process fixture runnable on
        # hosts whose private temporary directories are mounted noexec.
        real_popen = report.subprocess.Popen
        launcher = mock.patch.object(report.subprocess, 'Popen',
            side_effect=lambda args, **kwargs: real_popen([sys.executable, *args], **kwargs))
        launcher.start()
        self.addCleanup(launcher.stop)

    def calls(self):
        return [json.loads(line) for line in (self.root/'calls').read_text().splitlines()]

    def mode(self, value):
        (self.root/'mode').write_text(value)

    def test_two_owners_reuse_processes_and_all_seven_reads_each_sample(self):
        for _ in range(2):
            for instrument in ('EUR.USD', 'BTC.USD'):
                actual = report.observe(self.reader, fixtures.period(), instrument)
                expected = report.observe(fixtures.CollectionTests().native, fixtures.period(), instrument)
                actual.pop('sampled_at_ms');expected.pop('sampled_at_ms')
                self.assertEqual(actual, expected)
        calls = self.calls()
        self.assertEqual(len([x for x in calls if x[0]=='START']), 2)
        self.assertEqual(len([x for x in calls if x[0]=='CALL']), 28)
        self.assertEqual({x[2] for x in calls if x[0]=='START'}, {'paper.token','crypto.token'})
        self.assertEqual(len(self.reader._processes), 2)
        children=list(self.reader._processes.values());self.reader.close()
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_snapshot_drift_remains_rejected(self):
        self.mode('drift')
        with self.assertRaisesRegex(report.EvidenceError, 'BROKER_SNAPSHOT_CHANGED'):
            report.observe(self.reader, fixtures.period(), 'BTC.USD')

    def test_large_valid_response_is_read_across_pipe_chunks(self):
        self.mode('large')
        value=self.reader('BTC.USD','orders.list')
        self.assertEqual(len(value['padding']),200000)
        self.assertEqual(len(self.reader._processes),1)

    def test_invalid_requests_never_start_process(self):
        with mock.patch.object(report.subprocess,'Popen') as start:
            for tool in ('trade.place_order','trade.cancel_order','trade.flatten_position','risk.preview_order'):
                with self.assertRaisesRegex(report.EvidenceError, 'ALLOWLIST'):
                    self.reader('BTC.USD',tool)
            with self.assertRaisesRegex(report.EvidenceError,'ALLOWLIST'):
                self.reader('SPY','orders.list')
            start.assert_not_called()

    def test_bad_response_discards_reader_without_retry(self):
        for mode,reason in [('mismatch','TOOL_MISMATCH'),('eof','CLOSED'),
                            ('schema','SCHEMA_HASH_MISMATCH'),('oversize','TOO_LARGE')]:
            self.mode(mode)
            self.reader.close()
            with self.assertRaisesRegex(report.EvidenceError,reason):
                self.reader('BTC.USD','orders.list')
            self.assertEqual(self.reader._processes,{})
        calls=self.calls()
        self.assertEqual(len([x for x in calls if x[0]=='START']),4)
        self.assertEqual(len([x for x in calls if x[0]=='CALL']),4)
        self.mode('')
        self.assertIsInstance(self.reader('BTC.USD','orders.list'),dict)

    def test_timeout_reaps_owned_read_process(self):
        self.reader('EUR.USD','system.get_health')
        original=self.reader._processes['EUR.USD']
        self.mode('delay')
        with mock.patch.object(report.selectors,'DefaultSelector') as factory:
            factory.return_value.__enter__.return_value.select.return_value=[]
            with self.assertRaisesRegex(report.EvidenceError,'TIMEOUT'):
                self.reader('EUR.USD','system.get_health')
        self.assertEqual(self.reader._processes,{})
        self.assertIsNotNone(original.poll())
        self.mode('')
        self.reader('EUR.USD','orders.list')
        self.assertNotEqual(self.reader._processes['EUR.USD'].pid, original.pid)

    def test_default_retains_single_call_cli_compatibility(self):
        reader=report.NativeReader(self.root,self.root,Path('/unused-socket'))
        response=report.subprocess.CompletedProcess([],0,json.dumps({'status':'ok','payload':{'old_cli':True}}),'')
        with mock.patch.object(report.subprocess,'run',return_value=response) as run, \
                mock.patch.object(report.subprocess,'Popen') as start:
            self.assertEqual(reader('BTC.USD','orders.list'),{'old_cli':True})
            self.assertEqual(run.call_args.args[0][-2:],['call','orders.list'])
            start.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
            report.main(['--persistent-native-reader'])
        self.assertEqual(raised.exception.code,2)


if __name__=='__main__':
    unittest.main()
