#!/usr/bin/env python3
"""Candidate preparation is byte-bound and has no live trading side effects."""
import contextlib
import errno
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import install_decision_attribution as installer
import activate_prepared_driver as activation
import hepta_btc_entry_policy as policy


class PrepareUpgradeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.home=self.root/'home'
  self.repo=self.root/'checkout';self.source=self.repo/'scripts'
  self.source.mkdir(parents=True)
  for name in installer.runtime_filenames('BTC.USD'):
   self._put(self.source/name,(ROOT/'scripts'/name).read_bytes())
  self.source_sha='b'*40
  self.period=self.home/'.config/heptatrader/pnl-period.json'
  self._put(self.period,b'{"execution_domain":"PAPER","account":"DU-FIXTURE"}\n')
  self.protected={}
  self._protect(self.home/'.local/state/heptatrader/operator-maintenance/maintenance-hold.json',
                b'{"hold":true,"owner":"fixture"}\n')
  self._protect(self.home/'.local/state/heptatrader/kill-switch',b'fixture hold\n')
  # Only the installer entry UID is simulated; private files use their real owner.
  self.os_proxy=SimpleNamespace(**vars(os))
  self.os_proxy.geteuid=lambda:os.geteuid()+1
  self.os_proxy.umask=lambda value:0o077

 def _put(self,path,raw):
  path.parent.mkdir(parents=True,exist_ok=True)
  if path.exists():path.chmod(0o600)
  path.write_bytes(raw);path.chmod(0o600)

 def _protect(self,path,raw):
  self._put(path,raw);self.protected[path]=raw

 def _resident(self,instrument,label='default',state=None):
  release=self.root/'resident'/label/instrument
  hashes={}
  for name in installer.runtime_filenames(instrument):
   self._put(release/name,(self.source/name).read_bytes())
   hashes[name]=installer.sha(release/name)
  state_path=self.home/'.local/state/heptatrader'/label/instrument/'state.json'
  ledger_path=self.home/'.local/state/heptatrader'/label/instrument/'ledger.json'
  self._protect(state_path,json.dumps(state or {'position':0,'pending':None,'halt_new_entries':False}).encode())
  self._protect(ledger_path,b'{"entries":["existing-ledger-entry"]}\n')
  manifest={'schema':'heptatrader.attribution-producer.v1','source_sha':'a'*40,
   'instrument':instrument,'execution_domain':'PAPER','actor_scope':'EXISTING_LOCAL_DRIVER',
   'period_path':str(self.period),'period_sha256':installer.sha(self.period),
   'runtime_sha256':hashes,'btc_profile_sha256':None,'created_at_ms':123,
   'trading_behavior_changed':False,
   'account':'DU-FIXTURE','state_path':str(state_path),'ledger_path':str(ledger_path),
   'journal_directory':str(self.home/'journal'/label/instrument),
   'behavior_profile':'EXISTING_POLICY_UNCHANGED',
   'strategy_config':{'confidence_min':'0.62','reference_notional_usd':'100000'}}
  if instrument=='BTC.USD':
   profile={'schema':'heptatrader.btc-entry-runtime.v1','profile':policy.PROFILE,
    'period_sha256':manifest['period_sha256'],
    'initial_control':{'schema':'heptatrader.btc-entry-control.v1',
                       'open_reservations':[],'last_flat_at_ms':0}}
   self._put(release/'profile.json',(json.dumps(profile,indent=2)+'\n').encode())
   manifest['btc_profile_sha256']=installer.sha(release/'profile.json')
  self._put(release/'manifest.json',(installer.canonical(manifest)+'\n').encode())
  command=('ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B '+
           str(release/'heptatrader_attributed_driver.py')+' --manifest '+
           str(release/'manifest.json')+' ; ignore_errors=no ; }')
  service={'ActiveState':'active','MainPID':'4201','NRestarts':'0','ExecStart':command}
  return service,manifest,release

 @contextlib.contextmanager
 def _offline(self,service):
  real_read=installer.read_private
  def private_read(path,*args,**kwargs):
   if Path(path) in self.protected:
    raise AssertionError('preparation read live state/hold/ledger: '+str(path))
   return real_read(path,*args,**kwargs)
  def git_only(command,*args,**kwargs):
   self.assertEqual(command,['git','-C',str(self.repo),'rev-parse','HEAD'])
   self.assertIs(kwargs.get('text'),True)
   return self.source_sha+'\n'
  with contextlib.ExitStack() as stack:
   stack.enter_context(mock.patch.object(installer,'__file__',str(self.source/'install_decision_attribution.py')))
   stack.enter_context(mock.patch.object(installer.Path,'home',return_value=self.home))
   stack.enter_context(mock.patch.object(installer,'os',self.os_proxy))
   show=stack.enter_context(mock.patch.object(installer,'show',return_value=dict(service)))
   stack.enter_context(mock.patch.object(installer,'read_private',side_effect=private_read))
   stack.enter_context(mock.patch.object(installer,'state_ok',side_effect=AssertionError('state gate called')))
   stack.enter_context(mock.patch.object(installer.subprocess,'check_output',side_effect=git_only))
   stack.enter_context(mock.patch.object(installer.subprocess,'run',side_effect=AssertionError('subprocess mutation called')))
   stack.enter_context(mock.patch.object(installer,'load_verified_module',side_effect=AssertionError('candidate code imported')))
   stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
   yield show

 def _prepare(self,instrument,service):
  with self._offline(service) as show:
   result=installer.prepare_upgrade(instrument)
   show.assert_called_once_with(installer.unit_for(instrument))
  return result

 def _unchanged(self):
  for path,raw in self.protected.items():self.assertEqual(path.read_bytes(),raw)

 def test_explicit_stream_preparation_pins_reader_and_records_behavior_change(self):
  service,old,resident=self._resident('EUR.USD','stream-opt-in')
  before={p.name:p.read_bytes() for p in resident.iterdir()}
  with self._offline(service):
   result=installer.prepare_upgrade('EUR.USD',native_read_stream=True)
  manifest=json.loads(Path(result['manifest']).read_bytes())
  self.assertIs(manifest['native_read_stream'],True)
  self.assertIn('hepta_native_reader.py',manifest['runtime_sha256'])
  self.assertIs(manifest['runtime_bytes_changed'],False)
  self.assertIs(manifest['trading_behavior_changed'],True)
  self.assertIs(manifest['behavior_equivalence_assessed'],False)
  self.assertFalse(result['activated']);self.assertFalse(result['trade_authority'])
  self.assertEqual({p.name:p.read_bytes() for p in resident.iterdir()},before)
  self._unchanged()

 def test_ordinary_upgrade_preserves_an_existing_stream_choice(self):
  service,old,resident=self._resident('EUR.USD','stream-retained')
  old['native_read_stream']=True
  binary=self.root/'existing-qualified/bin/heptactl'
  self._put(binary,b'existing read-only CLI fixture\n');binary.chmod(0o500)
  old['native_read_binary']={'path':str(binary),'sha256':installer.sha(binary)}
  self._put(resident/'manifest.json',(installer.canonical(old)+'\n').encode())
  result=self._prepare('EUR.USD',service)
  prepared=json.loads(Path(result['manifest']).read_bytes())
  self.assertIs(prepared['native_read_stream'],True)
  self.assertEqual(prepared['native_read_binary'],old['native_read_binary'])
  self._unchanged()

 def test_qualified_read_binary_is_preserved_and_reverified_before_activation(self):
  service,old,resident=self._resident('EUR.USD','qualified-reader')
  binary=self.root/'qualified/bin/heptactl'
  self._put(binary,b'qualified read-only CLI fixture\n');binary.chmod(0o500)
  binding={'path':str(binary),'sha256':installer.sha(binary)}
  with self._offline(service):
   result=installer.prepare_upgrade('EUR.USD',native_read_stream=True,native_read_binary=binding)
  candidate=Path(result['manifest'])
  manifest=json.loads(candidate.read_bytes())
  self.assertEqual(manifest['native_read_binary'],binding)
  installed={'manifest':str(resident/'manifest.json'),
             'manifest_sha256':installer.sha(resident/'manifest.json')}
  inspected=activation.verify_prepared(candidate,result['manifest_sha256'],'EUR.USD',installed)
  self.assertEqual(inspected['native_read_binary'],binding)
  self._put(binary,b'changed after preparation\n');binary.chmod(0o500)
  with self.assertRaisesRegex(ValueError,'NATIVE_READ_BINARY_CHANGED'):
   activation.verify_prepared(candidate,result['manifest_sha256'],'EUR.USD',installed)
  self._unchanged()

 def test_all_native_clients_are_prepared_together_and_reverified_before_activation(self):
  for stream in (False,True):
   with self.subTest(stream=stream):
    service,old,resident=self._resident('EUR.USD','all-clients-'+str(stream))
    bindings={}
    for name in ('heptactl','hepta-paper-command','hepta-sessionctl'):
     binary=self.root/('native-'+str(stream))/name
     self._put(binary,(name+' fixture bytes\n').encode());binary.chmod(0o500)
     bindings[name]={'path':str(binary),'sha256':installer.sha(binary)}
    with self._offline(service):
     result=installer.prepare_upgrade('EUR.USD',native_read_stream=stream,native_clients=bindings)
    candidate=Path(result['manifest']);manifest=json.loads(candidate.read_bytes())
    self.assertEqual(manifest['native_clients'],bindings)
    self.assertEqual(manifest.get('native_read_binary'),bindings['heptactl'] if stream else None)
    for key in ('period_path','period_sha256','state_path','ledger_path','strategy_config','behavior_profile'):
     self.assertEqual(manifest[key],old[key])
    self.assertEqual(manifest['runtime_sha256'],old['runtime_sha256'])
    self.assertFalse(manifest['behavior_equivalence_assessed'])
    installed={'manifest':str(resident/'manifest.json'),'manifest_sha256':installer.sha(resident/'manifest.json')}
    target=activation.verify_prepared(candidate,result['manifest_sha256'],'EUR.USD',installed)
    self.assertEqual(target['native_clients'],bindings)
    # Mutation/recovery clients are reverified too, not only the read stream.
    changed=Path(bindings['hepta-paper-command']['path'])
    self._put(changed,b'changed order transport\n');changed.chmod(0o500)
    with self.assertRaisesRegex(ValueError,'NATIVE_READ_BINARY_CHANGED'):
     activation.verify_prepared(candidate,result['manifest_sha256'],'EUR.USD',installed)
    self._unchanged()

 def test_client_bindings_cli_requires_exact_private_file_digest(self):
  bindings={name:{'path':'/qualified/'+name,'sha256':'a'*64}
   for name in ('heptactl','hepta-paper-command','hepta-sessionctl')}
  path=self.root/'clients.json';self._put(path,(json.dumps(bindings)+'\n').encode())
  arguments=['install_decision_attribution.py','--instrument','EUR.USD','--prepare-upgrade',
   '--native-client-bindings',str(path),'--native-client-bindings-sha256',installer.sha(path)]
  with mock.patch.object(sys,'argv',arguments),mock.patch.object(installer,'os',self.os_proxy), \
       mock.patch.object(installer,'prepare_upgrade',return_value={}) as prepare:
   installer.main()
  prepare.assert_called_once_with('EUR.USD',native_clients=bindings)
  arguments[-1]='0'*64
  with mock.patch.object(sys,'argv',arguments),mock.patch.object(installer,'os',self.os_proxy), \
       mock.patch.object(installer,'prepare_upgrade') as prepare:
   with self.assertRaisesRegex(RuntimeError,'NATIVE_CLIENT_BINDINGS_FILE_CHANGED'):installer.main()
  prepare.assert_not_called()

 def test_complete_candidate_preserves_bindings_while_flat_held_or_pending(self):
  states={
   'flat':{'position':0,'pending':None,'halt_new_entries':False},
   'held':{'position':0.1,'pending':None,'halt_new_entries':True},
   'pending':{'position':0.1,'pending':{'command_id':'original-command'},'halt_new_entries':True}}
  for instrument in ('EUR.USD','BTC.USD'):
   for label,state in states.items():
    with self.subTest(instrument=instrument,state=label):
     service,installed,resident=self._resident(instrument,label,state)
     resident_before={p.name:p.read_bytes() for p in resident.iterdir()}
     result=self._prepare(instrument,service)
     self.assertEqual(result['status'],'PREPARED_NOT_ACTIVATED')
     self.assertFalse(result['activated']);self.assertFalse(result['service_changed'])
     self.assertFalse(result['trade_authority']);self.assertEqual(result['forced_orders'],0)
     release=Path(result['release']);prepared=json.loads((release/'manifest.json').read_bytes())
     for key,value in installed.items():
      if key not in ('source_sha','runtime_sha256','created_at_ms'):
       self.assertEqual(prepared[key],value,key)
     self.assertFalse(prepared['runtime_bytes_changed'])
     self.assertIs(prepared['behavior_equivalence_assessed'],False)
     self.assertEqual(prepared['source_sha'],self.source_sha)
     self.assertEqual(prepared['prepared_from_manifest_sha256'],installer.sha(resident/'manifest.json'))
     self.assertEqual(set(prepared['runtime_sha256']),installer.runtime_filenames(instrument))
     for name,digest in prepared['runtime_sha256'].items():
      self.assertEqual((release/name).read_bytes(),(self.source/name).read_bytes())
      self.assertEqual(installer.sha(release/name),digest)
     if instrument=='BTC.USD':
      self.assertEqual((release/'profile.json').read_bytes(),(resident/'profile.json').read_bytes())
     self.assertEqual({p.name:p.read_bytes() for p in resident.iterdir()},resident_before)
     self._unchanged()

 def test_unrelated_dirty_file_and_repeat_reuse_exact_candidate(self):
  service,_,_=self._resident('EUR.USD')
  first=self._prepare('EUR.USD',service);release=Path(first['release'])
  before={p.name:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in release.iterdir()}
  self._put(self.repo/'docs/unrelated-dirty-note.md',b'Unrelated local edit\n')
  again=self._prepare('EUR.USD',service)
  self.assertEqual(again['status'],'ALREADY_PREPARED_NOT_ACTIVATED')
  self.assertEqual(again['release'],first['release'])
  self.assertEqual({p.name:(p.read_bytes(),p.stat().st_ino,p.stat().st_mtime_ns) for p in release.iterdir()},before)
  self._unchanged()

 def test_actual_runtime_byte_change_creates_distinct_candidate_at_same_git_sha(self):
  service,_,_=self._resident('EUR.USD')
  first=self._prepare('EUR.USD',service)
  first_manifest=Path(first['manifest']).read_bytes()
  base=self.source/'heptatrader_paper_driver.py'
  self._put(base,base.read_bytes()+b'\n# Candidate source edit in the same checkout.\n')
  second=self._prepare('EUR.USD',service)
  changed=json.loads(Path(second['manifest']).read_bytes())
  self.assertTrue(changed['runtime_bytes_changed'])
  self.assertIs(changed['trading_behavior_changed'],True)
  self.assertIs(changed['behavior_equivalence_assessed'],False)
  self.assertEqual(second['source_sha'],first['source_sha'])
  self.assertNotEqual(second['release'],first['release'])
  self.assertNotEqual(second['runtime_sha256']['heptatrader_paper_driver.py'],first['runtime_sha256']['heptatrader_paper_driver.py'])
  self.assertEqual(Path(first['manifest']).read_bytes(),first_manifest)
  self._unchanged()

 def test_existing_corrupt_candidate_is_rejected_without_overwrite(self):
  service,_,_=self._resident('EUR.USD')
  prepared=self._prepare('EUR.USD',service)
  base=Path(prepared['release'])/'heptatrader_paper_driver.py'
  tampered=base.read_bytes()+b'\n# Tampered after preparation.\n';self._put(base,tampered)
  with self.assertRaisesRegex(RuntimeError,'ATTRIBUTION_PREPARED_RUNTIME_CHANGED'):
   self._prepare('EUR.USD',service)
  self.assertEqual(base.read_bytes(),tampered);self._unchanged()

 def test_unregistered_module_cannot_change_candidate_imports(self):
  service,_,_=self._resident('EUR.USD')
  prepared=self._prepare('EUR.USD',service)
  release=Path(prepared['release']);release.chmod(0o700)
  extra=release/'queue.py';self._put(extra,b"raise RuntimeError('unregistered import')\n")
  release.chmod(0o500)
  with self.assertRaisesRegex(RuntimeError,'ATTRIBUTION_PREPARED_FILE_SET_CHANGED'):
   self._prepare('EUR.USD',service)
  self.assertTrue(extra.exists());self._unchanged()

 def test_boolean_behavior_identity_preserves_parent_cohort(self):
  for parent_changed,bytes_changed in ((False,False),(False,True),(True,False),(True,True)):
   with self.subTest(parent_changed=parent_changed,bytes_changed=bytes_changed):
    base=self.source/'heptatrader_paper_driver.py'
    self._put(base,(ROOT/'scripts'/base.name).read_bytes())
    service,parent,resident=self._resident('EUR.USD',str(parent_changed)+'-'+str(bytes_changed))
    parent['trading_behavior_changed']=parent_changed
    parent_raw=(installer.canonical(parent)+'\n').encode()
    self._put(resident/'manifest.json',parent_raw)
    if bytes_changed:self._put(base,base.read_bytes()+b'\n# Candidate revision.\n')
    result=self._prepare('EUR.USD',service)
    prepared=json.loads(Path(result['manifest']).read_bytes())
    self.assertIs(prepared['runtime_bytes_changed'],bytes_changed)
    self.assertIs(prepared['trading_behavior_changed'],parent_changed or bytes_changed)
    self.assertIs(prepared['behavior_equivalence_assessed'],False)
    self.assertEqual((resident/'manifest.json').read_bytes(),parent_raw)

 def test_btc_base_binding_is_not_automatically_bypassed(self):
  service,_,_=self._resident('BTC.USD')
  base=self.source/'heptatrader_paper_driver.py'
  self._put(base,base.read_bytes()+b'\n# A changed base requires the wrapper to bind it explicitly.\n')
  with self.assertRaisesRegex(RuntimeError,'ATTRIBUTION_PREPARED_BTC_BASE_HASH_MISMATCH'):
   self._prepare('BTC.USD',service)
  self.assertFalse((self.home/'.local/share/heptatrader/decision-attribution/releases').exists())
  self._unchanged()

 def test_write_failure_does_not_reserve_an_unusable_candidate(self):
  service,_,_=self._resident('EUR.USD')
  real_write=installer.private_write
  def fail_write(path,*args,**kwargs):
   if path.name=='heptatrader_paper_driver.py':raise OSError('fixture disk failure')
   return real_write(path,*args,**kwargs)
  with mock.patch.object(installer,'private_write',side_effect=fail_write):
   with self.assertRaisesRegex(OSError,'fixture disk failure'):
    self._prepare('EUR.USD',service)
  result=self._prepare('EUR.USD',service)
  self.assertEqual(result['status'],'PREPARED_NOT_ACTIVATED');self._unchanged()

 def test_concurrent_identical_publication_reuses_verified_candidate(self):
  service,_,_=self._resident('EUR.USD')
  def publish_first(staging,release):
   shutil.copytree(staging,release)
   raise OSError(errno.ENOTEMPTY,'another preparation published first')
  with mock.patch.object(self.os_proxy,'rename',side_effect=publish_first):
   result=self._prepare('EUR.USD',service)
  self.assertEqual(result['status'],'ALREADY_PREPARED_NOT_ACTIVATED')
  self.assertEqual(list(Path(result['release']).parent.glob('.*-preparing-*')),[])
  self._unchanged()

 def test_failed_publication_is_not_reported_as_noop(self):
  service,_,_=self._resident('EUR.USD')
  with mock.patch.object(self.os_proxy,'rename',side_effect=OSError(errno.EIO,'publication failed')):
   with self.assertRaisesRegex(OSError,'publication failed'):
    self._prepare('EUR.USD',service)
  result=self._prepare('EUR.USD',service)
  self.assertEqual(result['status'],'PREPARED_NOT_ACTIVATED');self._unchanged()

 def _legacy_resident_layout(self,instrument,label):
  service,old,resident=self._resident(instrument,label)
  (resident/'hepta_verified_loader.py').unlink()
  old['runtime_sha256'].pop('hepta_verified_loader.py')
  old['runtime_sha256'].pop('hepta_native_reader.py')
  old.pop('runtime_loader',None)
  self._put(resident/'manifest.json',(installer.canonical(old)+'\n').encode())
  return service,old,resident

 def test_legacy_resident_prepares_candidate_that_activation_can_verify(self):
  # Only validate release bytes: neither release is imported or activated.
  for instrument in ('EUR.USD','BTC.USD'):
   with self.subTest(instrument=instrument):
    service,old,resident=self._legacy_resident_layout(instrument,'legacy-parent')
    before={p.name:p.read_bytes() for p in resident.iterdir()}
    prepared=self._prepare(instrument,service)
    manifest=Path(prepared['manifest'])
    new=json.loads(manifest.read_bytes())
    self.assertEqual(new['runtime_loader'],'verified-bytes.v1')
    installed={'manifest':str(resident/'manifest.json'),
               'manifest_sha256':installer.sha(resident/'manifest.json')}
    observed=activation.verify_prepared(manifest,prepared['manifest_sha256'],instrument,installed)
    self.assertEqual(observed['status'],'INSTALLED_VERIFIED')
    self.assertEqual(set(observed['runtime_sha256']),installer.runtime_filenames(instrument))
    self.assertEqual({p.name:p.read_bytes() for p in resident.iterdir()},before)
    self._unchanged()

 def test_existing_legacy_prepared_layout_remains_verifiable(self):
  for instrument in ('EUR.USD','BTC.USD'):
   with self.subTest(instrument=instrument):
    _,old,resident=self._legacy_resident_layout(instrument,'legacy-prepared-parent')
    installed={'manifest':str(resident/'manifest.json'),
               'manifest_sha256':installer.sha(resident/'manifest.json')}
    candidate=self.root/'legacy-prepared'/instrument
    names=set(old['runtime_sha256'])
    if instrument=='BTC.USD':names.add('profile.json')
    for name in names:self._put(candidate/name,(resident/name).read_bytes())
    new=dict(old);new.pop('created_at_ms',None)
    new.update(source_sha=self.source_sha,
               prepared_from_manifest_sha256=installed['manifest_sha256'],
               runtime_bytes_changed=False,trading_behavior_changed=False,
               behavior_equivalence_assessed=False,created_at_ms=456)
    manifest=candidate/'manifest.json'
    self._put(manifest,(installer.canonical(new)+'\n').encode())
    result=activation.verify_prepared(manifest,installer.sha(manifest),instrument,installed)
    self.assertEqual(set(result['runtime_sha256']),installer.runtime_filenames(instrument,legacy=True))
    self.assertFalse((candidate/'hepta_verified_loader.py').exists())
    new['runtime_loader']='unsupported-loader'
    self._put(manifest,(installer.canonical(new)+'\n').encode())
    with self.assertRaisesRegex(RuntimeError,'RUNTIME_INCOMPLETE'):
     activation.verify_prepared(manifest,installer.sha(manifest),instrument,installed)
    self._unchanged()

 def test_explicit_prepare_cli_needs_no_evidence_directory(self):
  with mock.patch.object(sys,'argv',['install_decision_attribution.py','--instrument','EUR.USD','--prepare-upgrade']), \
       mock.patch.object(installer,'os',self.os_proxy), \
       mock.patch.object(installer,'prepare_upgrade',return_value={}) as prepare, \
       mock.patch.object(installer,'install',side_effect=AssertionError('installation called')):
   installer.main()
  prepare.assert_called_once_with('EUR.USD')

 def test_prepare_cli_forwards_only_the_explicit_read_binary_binding(self):
  arguments=['install_decision_attribution.py','--instrument','EUR.USD','--prepare-upgrade',
             '--native-read-stream','--native-read-binary','/qualified/bin/heptactl',
             '--native-read-binary-sha256','a'*64]
  with mock.patch.object(sys,'argv',arguments), \
       mock.patch.object(installer,'os',self.os_proxy), \
       mock.patch.object(installer,'prepare_upgrade',return_value={}) as prepare, \
       mock.patch.object(installer,'install',side_effect=AssertionError('installation called')):
   installer.main()
  prepare.assert_called_once_with('EUR.USD',native_read_stream=True,
   native_read_binary={'path':'/qualified/bin/heptactl','sha256':'a'*64})


if __name__=='__main__':unittest.main()
