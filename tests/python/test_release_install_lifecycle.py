"""Own only this temporary installer group; never a service or model worker."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_release_package as release


class InstallStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / 'build'
        self.build.mkdir()
        self.stage = self.root / 'owned-stage'
        self.stage.mkdir()
        self.sibling = self.root / 'unrelated'
        self.sibling.write_text('unchanged')
        self.patch = mock.patch.object(release.tempfile, 'mkdtemp', return_value=str(self.stage))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def process(self, code=0):
        process = mock.Mock(pid=123456789, returncode=code)
        process.communicate.return_value = ('installer output', None)
        return process

    def test_launch_error_removes_only_owned_stage_and_preserves_exception(self):
        error = FileNotFoundError('fixture cmake missing')
        with mock.patch.object(release.subprocess, 'Popen', side_effect=error):
            with self.assertRaises(FileNotFoundError) as caught:
                release.install_from_build(self.build)
        self.assertIs(caught.exception, error)
        self.assertFalse(self.stage.exists())
        self.assertEqual(self.sibling.read_text(), 'unchanged')

    def test_success_hands_stage_to_caller(self):
        (self.stage / 'usr').mkdir()
        process = self.process()
        with mock.patch.object(release.subprocess, 'Popen', return_value=process) as spawn, \
             mock.patch.object(release, '_installer_group_exists', return_value=False):
            result = release.install_from_build(self.build)
        self.assertEqual(result, self.stage / 'usr')
        self.assertTrue(result.is_dir())
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        process.communicate.assert_called_once_with(timeout=300)

    def test_interrupted_spawn_without_handle_retains_stage_and_original_interrupt(self):
        error = KeyboardInterrupt('fixture interrupted constructor')
        with mock.patch.object(release.subprocess, 'Popen', side_effect=error), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(KeyboardInterrupt) as caught:
                release.install_from_build(self.build)
        self.assertIs(caught.exception, error)
        self.assertTrue(self.stage.is_dir())
        self.assertIn(str(self.stage), stderr.getvalue())

    def test_nonzero_and_missing_tree_keep_failure_and_clean_stage(self):
        for code in (7, 0):
            with self.subTest(code=code):
                self.stage.mkdir(exist_ok=True)
                process = self.process(code)
                with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
                     mock.patch.object(release, '_installer_group_exists', return_value=False):
                    with self.assertRaisesRegex(release.PackageError,
                            r'failed \(7\)' if code else 'did not create the expected staging tree'):
                        release.install_from_build(self.build)
                self.assertFalse(self.stage.exists())
                self.assertEqual(self.sibling.read_text(), 'unchanged')

    def test_unknown_group_identity_preserves_stage_and_original_timeout(self):
        process = self.process(None)
        error = subprocess.TimeoutExpired(['cmake', '--install'], 300)
        process.communicate.side_effect = error
        with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
             mock.patch.object(release.os, 'waitid', return_value=None), \
             mock.patch.object(release.os, 'getpgid', return_value=process.pid + 1), \
             mock.patch.object(release.os, 'killpg') as kill, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                release.install_from_build(self.build)
        self.assertIs(caught.exception, error)
        self.assertTrue(self.stage.is_dir())
        self.assertIn(str(self.stage), stderr.getvalue())
        kill.assert_not_called()
        process.poll.assert_not_called()
        process.wait.assert_not_called()

    def test_stale_handle_without_kernel_child_cannot_signal_a_reused_pid(self):
        process = self.process(None)
        error = subprocess.TimeoutExpired(['cmake', '--install'], 300)
        process.communicate.side_effect = error
        with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
             mock.patch.object(release.os, 'waitid', side_effect=ChildProcessError), \
             mock.patch.object(release.os, 'killpg') as kill, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                release.install_from_build(self.build)
        self.assertIs(caught.exception, error)
        kill.assert_not_called()
        self.assertTrue(self.stage.is_dir())
        self.assertIn(str(self.stage), stderr.getvalue())

    def test_reaped_leader_never_authorizes_signalling_numeric_group(self):
        process = self.process(0)
        with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
             mock.patch.object(release, '_installer_group_exists', return_value=True), \
             mock.patch.object(release.time, 'monotonic', side_effect=[0, 6]), \
             mock.patch.object(release.os, 'killpg') as kill, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaisesRegex(release.PackageError, 'left a process group'):
                release.install_from_build(self.build)
        kill.assert_not_called()
        self.assertTrue(self.stage.is_dir())
        self.assertIn(str(self.stage), stderr.getvalue())

    def test_ordinary_cleanup_exception_preserves_original_timeout_and_stage(self):
        errors = [ValueError('fixture cleanup'),
                  UnicodeDecodeError('utf8', b'\xff', 0, 1, 'fixture cleanup'),
                  MemoryError('fixture cleanup')]
        for cleanup_error in errors:
            with self.subTest(error=type(cleanup_error).__name__):
                original = subprocess.TimeoutExpired(['cmake', '--install'], 300)
                process = self.process(None)
                process.communicate.side_effect = [original, cleanup_error]
                with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
                     mock.patch.object(release.os, 'waitid', return_value=None), \
                     mock.patch.object(release.os, 'getpgid', return_value=process.pid), \
                     mock.patch.object(release.os, 'getsid', return_value=process.pid), \
                     mock.patch.object(release.os, 'killpg'), \
                     contextlib.redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        release.install_from_build(self.build)
                self.assertIs(caught.exception, original)
                self.assertTrue(self.stage.is_dir())
                self.assertIn(str(self.stage), stderr.getvalue())

    def test_ordinary_diagnostic_exception_cannot_mask_original_timeout(self):
        for diagnostic_error in (MemoryError('fixture print'), TypeError('fixture print')):
            with self.subTest(error=type(diagnostic_error).__name__):
                original = subprocess.TimeoutExpired(['cmake', '--install'], 300)
                process = self.process(None)
                process.communicate.side_effect = original
                with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
                     mock.patch.object(release.os, 'waitid', side_effect=ChildProcessError), \
                     mock.patch('builtins.print', side_effect=diagnostic_error) as emit:
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        release.install_from_build(self.build)
                self.assertIs(caught.exception, original)
                self.assertTrue(self.stage.is_dir())
                self.assertIn(str(self.stage), emit.call_args.args[0])

    def test_ordinary_pipe_close_error_cannot_mask_original_timeout(self):
        for close_error in (ValueError('fixture close'), MemoryError('fixture close')):
            with self.subTest(error=type(close_error).__name__):
                original = subprocess.TimeoutExpired(['cmake', '--install'], 300)
                process = self.process(None)
                process.communicate.side_effect = original
                process.stdout.close.side_effect = close_error
                with mock.patch.object(release.subprocess, 'Popen', return_value=process), \
                     mock.patch.object(release.os, 'waitid', side_effect=ChildProcessError), \
                     contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        release.install_from_build(self.build)
                self.assertIs(caught.exception, original)
                self.assertTrue(self.stage.is_dir())

    def test_successful_install_close_error_is_not_success(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                self.stage.mkdir(exist_ok=True)
                (self.stage / 'usr').mkdir(exist_ok=True)
                process = self.process()
                error = ValueError('fixture successful-close failure')
                process.stdout.close.side_effect = error
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(release.subprocess, 'Popen', return_value=process))
                    stack.enter_context(mock.patch.object(release, '_installer_group_exists', return_value=False))
                    stderr = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    if cleanup_fails:
                        stack.enter_context(mock.patch.object(release.shutil, 'rmtree', side_effect=MemoryError))
                    with self.assertRaises(ValueError) as caught:
                        release.install_from_build(self.build)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.stage.exists(), cleanup_fails)
                self.assertEqual(str(self.stage) in stderr.getvalue(), cleanup_fails)
                self.assertEqual(self.sibling.read_text(), 'unchanged')

    def test_failed_stage_removal_preserves_original_launch_error(self):
        original = FileNotFoundError('fixture missing cmake')
        with mock.patch.object(release.subprocess, 'Popen', side_effect=original), \
             mock.patch.object(release.shutil, 'rmtree', side_effect=MemoryError), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(FileNotFoundError) as caught:
                release.install_from_build(self.build)
        self.assertIs(caught.exception, original)
        self.assertTrue(self.stage.is_dir())
        self.assertIn(str(self.stage), stderr.getvalue())

    def test_second_user_termination_in_cleanup_diagnostic_or_close_propagates(self):
        for location in ('cleanup', 'diagnostic', 'close'):
            for kind in (KeyboardInterrupt, SystemExit):
                with self.subTest(location=location, kind=kind.__name__):
                    original = subprocess.TimeoutExpired(['cmake', '--install'], 300)
                    interruption = kind('fixture second user termination')
                    process = self.process(None)
                    process.communicate.side_effect = ([original, interruption]
                                                       if location == 'cleanup' else original)
                    if location == 'close':
                        process.stdout.close.side_effect = interruption
                    with contextlib.ExitStack() as stack:
                        stack.enter_context(mock.patch.object(release.subprocess, 'Popen', return_value=process))
                        stack.enter_context(mock.patch.object(release.os, 'waitid', return_value=None,
                            side_effect=None if location == 'cleanup' else ChildProcessError))
                        stack.enter_context(mock.patch.object(release.os, 'getpgid', return_value=process.pid))
                        stack.enter_context(mock.patch.object(release.os, 'getsid', return_value=process.pid))
                        stack.enter_context(mock.patch.object(release.os, 'killpg'))
                        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                        if location == 'diagnostic':
                            stack.enter_context(mock.patch('builtins.print', side_effect=interruption))
                        with self.assertRaises(kind) as caught:
                            release.install_from_build(self.build)
                    self.assertIs(caught.exception, interruption)
                    self.assertTrue(self.stage.is_dir())


# This finite child interpreter scopes subreaper/signal settings to the fixture.
# Its reaper waits only the exact synthetic grandchild, never kills it, and
# never adopts Popen's leader or the deliberately unrelated sibling's status.
TREE_FIXTURE = r'''
import ctypes,importlib.util,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1]);source=Path(sys.argv[2]);mode=sys.argv[3]
signal.signal(signal.SIGALRM,lambda *_: (_ for _ in ()).throw(TimeoutError('fixture watchdog')))
signal.setitimer(signal.ITIMER_REAL,10)
libc=ctypes.CDLL(None,use_errno=True)
assert libc.prctl(36,1,0,0,0)==0
spec=importlib.util.spec_from_file_location('fixture_release',source)
release=importlib.util.module_from_spec(spec);sys.modules[spec.name]=release;spec.loader.exec_module(release)
release.INSTALL_TIMEOUT_SECONDS=.15
root.joinpath('build').mkdir();root.joinpath('bin').mkdir()
stage=root/'owned-stage';stage.mkdir();(root/'unrelated-file').write_text('preserve')
pidfile=root/'pids.json'
fake=root/'bin/cmake'
fake.write_text('#!'+sys.executable+'\n'+
 'import json,os,sys,time\nfrom pathlib import Path\n'+
 'prefix=Path(sys.argv[sys.argv.index("--prefix")+1]);prefix.mkdir(parents=True)\n'+
 'child=os.fork()\n'+
 'if child==0:\n print("child keeps stdout open",flush=True)\n time.sleep(30)\n os._exit(0)\n'+
 'Path('+repr(str(pidfile))+').write_text(json.dumps({"leader":os.getpid(),"child":child,"pgid":os.getpgrp(),"sid":os.getsid(0)}))\n'+
 ('os._exit(0)\n' if mode=='leader-exit' else 'time.sleep(30)\n'))
fake.chmod(0o700)
os.environ['PATH']=str(root/'bin')+':/usr/bin:/bin'
Popen=subprocess.Popen
unrelated=Popen([sys.executable,'-I','-B','-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
done=threading.Event();reaped=[]
def reap_grandchild():
 while not done.is_set():
  try:
   child=json.loads(pidfile.read_text())['child']
   pid,status=os.waitpid(child,os.WNOHANG)
   if pid:reaped.append({'pid':pid,'exit':os.waitstatus_to_exitcode(status)});return
  except (FileNotFoundError,json.JSONDecodeError,ChildProcessError):pass
  time.sleep(.005)
thread=threading.Thread(target=reap_grandchild);thread.start()
spawned=[];signals=[]
killpg=os.killpg
def audited_killpg(pid,sig):
 assert spawned and pid==spawned[0].pid and pid!=unrelated.pid
 signals.append([pid,int(sig)])
 return killpg(pid,sig)
def spawn(argv,**kwargs):
 assert argv[0]=='cmake' and argv[1]=='--install' and kwargs['start_new_session'] is True
 process=Popen(argv,**kwargs);spawned.append(process)
 until=time.monotonic()+5
 while True:
  try:proof=json.loads(pidfile.read_text());break
  except (FileNotFoundError,json.JSONDecodeError):
   if time.monotonic()>=until:raise TimeoutError('fixture did not become ready')
   time.sleep(.005)
 assert proof['leader']==proof['pgid']==proof['sid']==process.pid
 return process
try:
 error=None
 with patch.object(release.tempfile,'mkdtemp',return_value=str(stage)),patch.object(release.subprocess,'Popen',side_effect=spawn),patch.object(release.os,'killpg',side_effect=audited_killpg):
  try:release.install_from_build(root/'build')
  except subprocess.TimeoutExpired as caught:error=caught
 assert error is not None and error.timeout==.15
 proof=json.loads(pidfile.read_text())
 assert not stage.exists() and (root/'unrelated-file').read_text()=='preserve'
 assert unrelated.poll() is None and spawned[0].returncode is not None
 assert not Path('/proc/'+str(proof['leader'])).exists() and not Path('/proc/'+str(proof['child'])).exists()
 assert len(reaped)==1 and reaped[0]['exit']==-9
 assert [proof['leader'],9] in signals and all(pid==proof['leader'] for pid,sig in signals)
 print(json.dumps({'mode':mode,'group':proof,'direct_child_reaped':True,'grandchild_exited_and_reaped':reaped,'unrelated_sibling_alive':True,'unrelated_file_unchanged':True,'stage_removed':True,'signals':signals}),flush=True)
finally:
 # Fixture-only fallback cleanup remains bound to its synthetic live child.
 if pidfile.exists():
  proof=json.loads(pidfile.read_text())
  try:
   if os.getpgid(proof['child'])==proof['pgid'] and os.getsid(proof['child'])==proof['sid']:killpg(proof['pgid'],signal.SIGKILL)
  except ProcessLookupError:pass
 if spawned:
  if spawned[0].returncode is None:
   try:
    if os.getpgid(spawned[0].pid)==spawned[0].pid and os.getsid(spawned[0].pid)==spawned[0].pid:killpg(spawned[0].pid,signal.SIGKILL)
   except ProcessLookupError:pass
  try:spawned[0].communicate(timeout=2)
  except subprocess.TimeoutExpired:pass
 thread.join(2);done.set();thread.join(1)
 unrelated.terminate();unrelated.wait(timeout=2)
 signal.setitimer(signal.ITIMER_REAL,0)
'''


class RealInstallerTreeTests(unittest.TestCase):
    def test_timeout_reaps_owned_tree_even_when_leader_already_exited(self):
        for mode in ('leader-alive', 'leader-exit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                environment = {'PATH': '/usr/bin:/bin', 'HOME': directory,
                               'TMPDIR': directory, 'PYTHONDONTWRITEBYTECODE': '1',
                               'LC_ALL': 'C.UTF-8'}
                result = subprocess.run(
                    [sys.executable, '-I', '-B', '-c', TREE_FIXTURE,
                     directory, str(ROOT / 'scripts/build_release_package.py'), mode],
                    cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                proof = json.loads(result.stdout)
                self.assertTrue(proof['stage_removed'])
                self.assertTrue(proof['direct_child_reaped'])
                self.assertTrue(proof['unrelated_sibling_alive'])
                print('INSTALL_TREE_FIXTURE ' + json.dumps(proof, sort_keys=True), flush=True)
