"""Loop observations cannot replace durable original-command recovery state."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from hepta_decision_attribution import DRIVER_OBSERVATION_FIELDS, merge_driver_observation


# Real filesystem operations and separate interpreters exercise abrupt process
# loss. The only substituted boundary is a local transport fixture: no client,
# socket, token, native runtime or broker is used by either process.
CRASH_WORKER = r'''
import importlib.util,json,os,signal,stat,sys
from pathlib import Path
module_path,state_path,mode,point=sys.argv[1:]
spec=importlib.util.spec_from_file_location('isolated_crash_driver',module_path)
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
path=Path(state_path)
command='fixture-original-command-never-a-broker-order'
operations=[]
if mode=='recover':
    state=d.load_state(path)
    original=state['pending']['command_id']
    def inspect(_binary,_socket,_token,_outbox,op,extra):
        assert op=='inspect', 'recovery attempted a transport mutation'
        assert extra[extra.index('--command-id')+1]==original
        operations.append(op)
        return {'ok':True,'result':{'payload':{'authoritative':True,
            'command_id':original,'command_status':'uncertain'}}}
    d.paper_cmd=inspect
    snapshot={'position':0.0,'active':[],'orders':{'recent_orders':[]}}
    for _ in range(2):
        d.inspect_pending(state,path,'unused','unused','unused','unused',snapshot)
    try:
        d.prepare_send(state,path,'unused','unused','unused','unused','OPEN',
            {'bid':1.1,'ask':1.2},'BUY')
    except d.DriverError as exc:
        assert 'replacement preparation forbidden' in str(exc)
    else:
        raise AssertionError('unresolved original command was replaceable')
    recovered=d.load_state(path)
    assert recovered['pending']['command_id']==command
    assert recovered['position']==0 and recovered['entries']==0
    print(json.dumps({'operations':operations,'pending':recovered['pending'],
        'position':recovered['position'],'entries':recovered['entries']}),flush=True)
    raise SystemExit(0)

state=d.load_state(path);d.atomic_json(path,state)
real_fsync=os.fsync;real_replace=os.replace
phase='initial'
def pause(name):
    if name==point:
        print(json.dumps({'point':name,'operations':operations}),flush=True)
        while True: signal.pause()
def fsync(fd):
    real_fsync(fd)
    kind='directory-sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file-sync'
    pause(phase+'-'+kind)
def replace(source,target):
    if str(target).endswith('.observation.json'):
        pause('observation-before-replace')
        real_replace(source,target)
        pause('observation-after-replace')
    else:
        real_replace(source,target)
        pause(phase+'-replace')
os.fsync=fsync;os.replace=replace
real_atomic=d.atomic_json
def atomic(path,value):
    global phase
    phase={'PREPARED':'prepared','POSSIBLY_SENT':'possibly',
        'ACKNOWLEDGED':'ack'}[value['pending']['send_state']]
    real_atomic(path,value)
d.atomic_json=atomic
def transport(_binary,_socket,_token,_outbox,op,extra):
    operations.append(op)
    if op=='prepare-open':
        return {'ok':True,'command_id':command}
    assert op=='submit'
    saved=d.load_state(path)
    assert saved['pending']['command_id']==command
    assert saved['pending']['send_state']=='POSSIBLY_SENT'
    pause('submitted-before-reply')
    return {'ok':True,'transport_ok':True,'application_status':'ok'}
d.paper_cmd=transport
if point.startswith('observation-'):
    state['pending']={'command_id':command,'kind':'OPEN','side':'BUY',
        'send_state':'POSSIBLY_SENT','broker_order_id':None,'position_before_send':0.0}
    d.atomic_json(path,state)
    state['history']=[{'observed_at_ms':1234,'bid':1.1,'ask':1.2}]
    state['last_error']='diagnostic after the durable original command'
    d.checkpoint_loop_state(path,state)
else:
    d.prepare_send(state,path,'unused','unused','unused','unused','OPEN',
        {'bid':1.1,'ask':1.2},'BUY')
raise AssertionError('requested crash boundary was not reached')
'''


class DriverProcessCrashTests(unittest.TestCase):
    def run_worker(self, path, mode, point):
        return subprocess.Popen([sys.executable, '-B', '-c', CRASH_WORKER,
            str(ROOT / 'scripts/heptatrader_paper_driver.py'), str(path), mode, point],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)

    def test_sigkill_keeps_original_command_across_send_and_observation_boundaries(self):
        # A process crash does not simulate power loss. Before rename, recovery
        # must see the previous complete state; after rename it sees the new
        # complete state. Sending is possible only after directory fsync.
        cases = [
            ('prepared-directory-sync', 'PREPARED', 0),
            ('possibly-file-sync', 'PREPARED', 0),
            ('possibly-replace', 'POSSIBLY_SENT', 0),
            ('possibly-directory-sync', 'POSSIBLY_SENT', 0),
            ('submitted-before-reply', 'POSSIBLY_SENT', 1),
            ('ack-file-sync', 'POSSIBLY_SENT', 1),
            ('ack-replace', 'ACKNOWLEDGED', 1),
            ('ack-directory-sync', 'ACKNOWLEDGED', 1),
            ('observation-before-replace', 'POSSIBLY_SENT', 0),
            ('observation-after-replace', 'POSSIBLY_SENT', 0),
        ]
        for point, expected_state, submit_count in cases:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'state.json'
                child = self.run_worker(path, 'crash', point)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(child.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(10), 'crash boundary timed out')
                        line = child.stdout.readline()
                    self.assertTrue(line, child.stderr.read() if child.poll() is not None else
                                    'child closed stdout before crash boundary')
                    event = json.loads(line)
                    self.assertEqual(event['point'], point)
                    self.assertEqual(event['operations'].count('submit'), submit_count)
                    self.assertEqual(event['operations'].count('prepare-open'),
                                     0 if point.startswith('observation-') else 1)
                    child.kill()
                    stdout, stderr = child.communicate(timeout=5)
                    self.assertEqual(child.returncode, -signal.SIGKILL)
                    self.assertEqual((stdout, stderr), ('', ''))
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.communicate(timeout=5)
                durable = json.loads(path.read_bytes())
                self.assertEqual(durable['pending']['send_state'], expected_state)
                self.assertEqual(durable['pending']['command_id'],
                                 'fixture-original-command-never-a-broker-order')
                # Loss of the optional sidecar cannot alter restart recovery.
                path.with_suffix('.observation.json').unlink(missing_ok=True)
                recovered = self.run_worker(path, 'recover', point)
                try:
                    stdout, stderr = recovered.communicate(timeout=10)
                finally:
                    if recovered.poll() is None:
                        recovered.kill()
                        recovered.communicate(timeout=5)
                self.assertEqual(recovered.returncode, 0, stderr)
                result = json.loads(stdout)
                self.assertEqual(result['operations'], ['inspect', 'inspect'])
                self.assertEqual(result['pending']['command_id'], durable['pending']['command_id'])
                self.assertEqual(result['pending']['send_state'], expected_state)
                self.assertEqual((result['position'], result['entries']), (0, 0))


class DriverObservationStateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "isolated_driver_observation", ROOT / "scripts/heptatrader_paper_driver.py")
        self.driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.driver)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "state.json"
        self.observation = self.path.with_suffix(".observation.json")
        self.state = self.driver.load_state(self.path)
        self.driver.atomic_json(self.path, self.state)

    def read_observed(self):
        raw = self.path.read_bytes()
        return merge_driver_observation(self.path, json.loads(raw), hashlib.sha256(raw).hexdigest())

    def tick(self, iteration=1):
        self.state["resident_loop_progress"] = {
            "schema": self.driver.RESIDENT_LOOP_PROGRESS_SCHEMA,
            **self.driver.resident_process_identity(), "iteration": iteration, "at_ms": 1000 + iteration}
        self.state["loop_observation"] = {
            "schema": self.driver.LOOP_OBSERVATION_SCHEMA,
            "iteration": iteration, "checked_at_ms": 1000 + iteration, "phase": "OBSERVING"}
        self.state["history"] = [{"observed_at_ms": 1000 + iteration, "bid": 1.1, "ask": 1.1001}]
        self.driver.checkpoint_loop_state(self.path, self.state)

    def test_idle_ticks_only_replace_bound_observation_without_fsync(self):
        self.assertEqual(self.driver.OBSERVATION_STATE_FIELDS, DRIVER_OBSERVATION_FIELDS)
        original = self.path.read_bytes()
        identity = self.driver.state_file_identity(self.path)
        with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            for iteration in range(1, 7):
                self.tick(iteration)
            self.assertEqual(sync.call_count, 0)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.driver.state_file_identity(self.path), identity)
        self.assertEqual(self.read_observed(), self.state)
        # Restart recovery deliberately ignores a fresh diagnostic/history sidecar.
        self.assertEqual(self.driver.load_state(self.path), json.loads(original))

    def test_every_business_transition_remains_durable(self):
        transitions = [
            {"pending": {"command_id": "original", "send_state": "PREPARED"}},
            {"pending": {"command_id": "original", "send_state": "POSSIBLY_SENT"}},
            {"halt_new_entries": True}, {"unknown_future_business_field": "preserve"},
        ]
        for change in transitions:
            with self.subTest(change=change):
                self.state.update(change)
                with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
                    self.tick()
                    self.assertEqual(sync.call_count, 2)
                for key, value in change.items():
                    self.assertEqual(self.driver.load_state(self.path)[key], value)
                self.assertEqual(self.read_observed(), self.state)

    def test_observation_failure_falls_back_to_existing_durable_write(self):
        self.state["last_error"] = "diagnostic"
        with patch.object(self.driver, "publish_loop_observation", side_effect=OSError("test")), \
                patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            self.driver.checkpoint_loop_state(self.path, self.state)
            self.assertEqual(sync.call_count, 2)
        self.assertEqual(self.driver.load_state(self.path), self.state)

    def test_later_critical_write_invalidates_previous_observation(self):
        self.tick()
        self.state["pending"] = {"command_id": "original", "send_state": "POSSIBLY_SENT"}
        self.driver.atomic_json(self.path, self.state)
        self.assertEqual(self.read_observed(), self.state)
        self.assertNotEqual(json.loads(self.observation.read_text())["state_sha256"],
                            hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_replaced_durable_file_invalidates_writer_cache(self):
        changed = copy.deepcopy(self.state)
        changed["last_error"] = "older diagnostic in a replacement inode"
        replacement = self.path.with_suffix(".replacement")
        replacement.write_text(json.dumps(changed))
        replacement.chmod(0o600)
        replacement.replace(self.path)
        with patch.object(self.driver.os, "fsync", wraps=os.fsync) as sync:
            self.tick()
            self.assertEqual(sync.call_count, 2)
        self.assertIsNone(self.driver.load_state(self.path)["pending"])

    def test_sidecar_cannot_override_pending_or_other_business_fields(self):
        self.tick()
        valid = json.loads(self.observation.read_text())
        original = json.loads(self.path.read_bytes())
        for key in ("pending", "position", "halt_new_entries", "lease_generation", "entries"):
            with self.subTest(key=key):
                bad = copy.deepcopy(valid)
                bad["fields"][key] = "untrusted"
                self.observation.write_text(json.dumps(bad))
                self.assertEqual(self.read_observed(), original)
        for key, value in (("state_sha256", "0" * 64), ("instrument", "BTC.USD"),
                           ("schema", "unbound"), ("extra", True)):
            bad = copy.deepcopy(valid)
            bad[key] = value
            self.observation.write_text(json.dumps(bad))
            self.assertEqual(self.read_observed(), original)

    def test_optional_nonregular_symlink_and_corrupt_sidecars_are_ignored(self):
        original = json.loads(self.path.read_bytes())
        os.mkfifo(self.observation, 0o600)
        self.assertEqual(self.read_observed(), original)
        self.observation.unlink()
        self.observation.symlink_to(self.path)
        self.assertEqual(self.read_observed(), original)
        self.observation.unlink()
        self.observation.write_text('{"schema":1,"schema":2}')
        self.observation.chmod(0o600)
        self.assertEqual(self.read_observed(), original)

    def test_durable_sync_failure_does_not_publish_new_observation(self):
        self.tick()
        previous = self.observation.read_bytes()
        self.state["pending"] = {"command_id": "original", "send_state": "POSSIBLY_SENT"}
        with patch.object(self.driver.os, "fsync", side_effect=OSError("test sync failure")):
            with self.assertRaises(OSError):
                self.driver.checkpoint_loop_state(self.path, self.state)
        self.assertEqual(self.observation.read_bytes(), previous)
        self.assertIsNone(self.driver.load_state(self.path)["pending"])


if __name__ == "__main__":
    unittest.main()
