"""Run the real controller with private fake engines; never an OCI qualification."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import verify_ib_candidate_artifact as artifact

REFERENCE = 'dockcross/linux-x64@sha256:' + 'a' * 64
RESOLVED = 'docker.io/' + REFERENCE
IMAGE_ID = 'sha256:' + 'b' * 64
STUB = r'''#!/usr/bin/python3
import datetime,hashlib,json,os,pathlib,subprocess,sys,time
args=sys.argv[1:];name=pathlib.Path(sys.argv[0]).name
with open(os.environ['CALLS'],'a') as out:out.write(json.dumps([name,*args])+'\n')
root=pathlib.Path(os.environ['ENGINE_STATE']);root.mkdir(exist_ok=True)
def stored(key):
 for path in root.glob('*.json'):
  item=json.loads(path.read_bytes())
  if item['Id']==key or item['Name']==key:return path,item
 return None,None
def save(path,item):path.write_text(json.dumps(item))
if args==['--version']:print(name+' version 99.0');sys.exit(0)
if args[0]=='pull':sys.exit(0)
if args[:2]==['image','inspect']:
 if os.environ.get('INSPECT_MISSING')=='1':sys.exit(125)
 print(json.dumps([{'Id':'b'*64 if name=='podman' else 'sha256:'+'b'*64,
  'RepoDigests':[os.environ.get('INSPECT_DIGEST','docker.io/dockcross/linux-x64@sha256:'+'a'*64)]}]))
 sys.exit(0)
if args[0]=='info':print(os.environ.get('ROOTLESS','true'));sys.exit(0)
if args[0]=='container':
 if os.environ.get('ENGINE_UNAVAILABLE')=='1':sys.exit(17)
 mode=args[1]
 if mode=='ls':
  filters=[args[i+1] for i,x in enumerate(args) if x=='--filter']
  for p in root.glob('*.json'):
   item=json.loads(p.read_bytes());matches=True
   for value in filters:
    if value.startswith('id='):matches=matches and item['Id']==value[3:]
    elif value.startswith('name='):matches=matches and value[5:] in item['Name']
    else:
     key,want=value[len('label='):].split('=',1)
     matches=matches and item['Config']['Labels'].get(key)==want
   if matches:print(item['Id'])
  sys.exit(0)
 path,item=stored(args[-1])
 if item is None:sys.exit(1)
 if mode=='inspect':print(json.dumps([item]));sys.exit(0)
 if os.environ.get('CLEANUP_FAIL')=='1':sys.exit(17)
 if mode=='stop':item['State'].update(Running=False,Status='exited',ExitCode=137);save(path,item);sys.exit(0)
 assert mode=='rm'
 # Real Podman removes its cidfile with the container.
 pathlib.Path(item['cidfile']).unlink(missing_ok=True)
 path.unlink();sys.exit(0)
if args[0]=='create':
 if os.environ.get('CREATE_NO_OBJECT')=='1':sys.exit(124)
 cidfile=pathlib.Path(args[args.index('--cidfile')+1]);cid=hashlib.sha256(str(cidfile).encode()).hexdigest()
 labels={}
 for i,x in enumerate(args):
  if x=='--label':k,v=args[i+1].split('=',1);labels[k]=v
 if os.environ.get('FOREIGN_CONTAINER')=='1':labels['io.heptatrader.ib-builder.operation']='foreign'
 item={'Id':cid,'Image':'sha256:'+'b'*64,'Name':args[args.index('--name')+1],
  'Created':datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'Config':{'Labels':labels},'State':{'Running':False,'Status':'created','ExitCode':0,'OOMKilled':False,'Error':''},'args':args,'cidfile':str(cidfile)}
 save(root/(cid+'.json'),item)
 if os.environ.get('OMIT_CID')!='1':cidfile.write_text(cid+'\n')
 time.sleep(float(os.environ.get('CREATE_SLEEP','0')))
 print(cid);sys.exit(int(os.environ.get('CREATE_STATUS','0')))
assert args[0]=='start'
path,item=stored(args[-1]);assert item is not None
item['State'].update(Running=True,Status='running');save(path,item);args=item['args']
mount=next(x for x in args if x.startswith('type=bind,src=') and x.endswith(',dst=/build'))
build=pathlib.Path(mount[len('type=bind,src='):-len(',dst=/build')]);assert (build/'home').is_dir()
pos=args.index('sha256:'+'b'*64);cmd=args[pos+1:]
status=0
if os.environ.get('START_SLEEP'):
 time.sleep(float(os.environ['START_SLEEP']))
 if cmd[:2]==['/bin/sh','-ceu']:
  import shutil
  (build/'compilers.selected').write_text(shutil.which('c++')+'\n'+shutil.which('cc')+'\n')
  print('synthetic deadline-only toolchain')
 item['State'].update(Running=False,Status='exited');save(path,item);sys.exit(0)
if cmd[:2]==['/bin/sh','-ceu']:
 env=dict(os.environ,HOME=str(build/'home'),HEPTA_BUILDER_EXPECTED_UID=str(os.getuid()),HEPTA_BUILDER_EXPECTED_GID=str(os.getgid()))
 for i,x in enumerate(args):
  if x=='--env' and args[i+1].startswith('HEPTA_BUILDER_EXPECTED_'):
   key,want=args[i+1].split('=',1);env[key]=want
 if env['HEPTA_BUILDER_EXPECTED_UID']=='0':
  ids=build/'fake-namespace-id';ids.mkdir()
  fake_id=ids/'id';fake_id.write_text('#!/bin/sh\nprintf 0\n');fake_id.chmod(0o700)
  env['PATH']=str(ids)+os.pathsep+env['PATH']
 env.pop('CXX',None);env.pop('CC',None)
 if 'FAKE_CXX' in env:env['CXX']=env['FAKE_CXX']
 if 'FAKE_CC' in env:env['CC']=env['FAKE_CC']
 cmd[-1]=cmd[-1].replace('/build/compilers.selected',str(build/'compilers.selected'))
 if os.environ.get('BAD_TOOL_HASH')=='1':
  tools=build/'badtools';tools.mkdir();bad=tools/'sha256sum';bad.write_text('#!/bin/sh\nexit 7\n');bad.chmod(0o700)
  env['PATH']=str(tools)+os.pathsep+env['PATH']
 if os.environ.get('MISSING_TOOL')=='1':env['PATH']=os.environ['EMPTY_PATH']
 status=subprocess.run(cmd,env=env).returncode
elif cmd[:2]==['cmake','--build']:
 time.sleep(float(os.environ.get('COMPILE_SLEEP','0')))
 target=build/'work/bin/hepta-ib-executiond';target.parent.mkdir(parents=True)
 target.write_bytes(b'\x7fELFsynthetic-controller-fixture');target.chmod(0o500);print('synthetic compiler output')
elif cmd[0]!='cmake':
 assert cmd[:3]==['python3','-I','-B'];status=int(os.environ.get('PROBE_STATUS','0'))
item['State'].update(Running=os.environ.get('LEAVE_RUNNING')=='1',
 Status='running' if os.environ.get('LEAVE_RUNNING')=='1' else 'exited',
 ExitCode=False if os.environ.get('BOOL_EXIT')=='1' else int(os.environ.get('CONTAINER_EXIT',str(status))),
 OOMKilled=os.environ.get('CONTAINER_OOM')=='1',Error=os.environ.get('CONTAINER_ERROR',''));save(path,item)
sys.exit(status)
'''

class CandidateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hepta-builder-control-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'candidate'; self.source.mkdir()
        self.bin = self.root/'bin'; self.bin.mkdir()
        self.sdk = self.root/'sdk'; self.sdk.mkdir()
        (self.sdk/'Decimal.cpp').write_text('synthetic source')
        (self.sdk/'libbid.a').write_bytes(b'synthetic archive, not executable')
        self.quota = self.root/'quota'; self.quota.mkdir()
        self.empty = self.root/'empty'; self.empty.mkdir()
        self.calls = self.root/'calls'
        self.output = self.root/'candidate.tar'
        for name in ('docker','podman'):
            path=self.bin/name;path.write_text(STUB);path.chmod(0o700)
        for name,body in [('mountpoint','exit 0'),('df',"printf '1B-blocks\\n2147483648\\n'")]:
            p=self.bin/name;p.write_text('#!/bin/sh\n'+body+'\n');p.chmod(0o700)
        def git(*args):
            return subprocess.run(['git','-c','commit.gpgsign=false',*args],cwd=self.source,
                check=True,capture_output=True,text=True).stdout.strip()
        git('init','-q');git('config','user.email','fixture@example.invalid')
        git('config','user.name','CPU fixture')
        (self.source/'README').write_text('Inert candidate source fixture.\n')
        git('add','README');git('commit','-qm','fixture')
        self.sha=git('rev-parse','HEAD')

    def run_builder(self, **changes):
        env={k:v for k,v in os.environ.items() if not k.startswith('HEPTA_IB_BUILD')}
        env.update(PATH=str(self.bin)+os.pathsep+os.environ['PATH'],CALLS=str(self.calls),
                   EMPTY_PATH=str(self.empty),ENGINE_STATE=str(self.root/'engine-state'),HEPTA_IB_BUILD_SDK_ROOT=str(self.sdk),
                   HEPTA_IB_BUILD_QUOTA_ROOT=str(self.quota),HEPTA_IB_BUILDER_IMAGE=REFERENCE)
        env.update(changes)
        return subprocess.run(['bash',str(ROOT/'scripts/build_ib_candidate_artifact.sh'),
            str(self.source),self.sha,str(self.output)],env=env,capture_output=True,text=True,timeout=15)

    def records(self):
        return [json.loads(x) for x in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_default_docker_retains_closed_artifact_and_bound_evidence(self):
        result=self.run_builder();self.assertEqual(result.returncode,0,result.stderr)
        calls=self.records();self.assertTrue(any(x[1]=='pull' for x in calls))
        for call in (x for x in calls if x[1]=='create'):
            self.assertIn('--pull=never',call);self.assertIn(IMAGE_ID,call)
            self.assertNotIn(REFERENCE,call);self.assertIn('CMAKE_BUILD_PARALLEL_LEVEL=2',call)
            self.assertIn('--network',call);self.assertIn('none',call);self.assertIn('--read-only',call)
        with tarfile.open(self.output) as tar:
            self.assertEqual(tar.getnames(),['manifest.json','hepta-ib-executiond'])
            manifest=json.load(tar.extractfile('manifest.json'))
        self.assertEqual(set(manifest['builder']),set(artifact.BUILDER_KEYS))
        artifact.verify_and_extract(self.output,self.sha,None,self.root/'verified',
            trusted_root=ROOT,expected_image=REFERENCE)
        evidence=Path(str(self.output)+'.diagnostics')
        status=json.loads((evidence/'build-status.json').read_bytes())
        self.assertEqual(set(status['evidence_sha256']),set(artifact.BUILDER_EVIDENCE_FILES)|{'container-lifecycle.json'})
        for name,pin in status['evidence_sha256'].items():
            self.assertEqual(hashlib.sha256((evidence/name).read_bytes()).hexdigest(),pin)
        identity=json.loads((evidence/'image-identity.json').read_bytes())
        self.assertEqual(identity['engine'],'docker');self.assertEqual(identity['matched_repo_digest'],RESOLVED)
        self.assertEqual(identity['observed_repo_digests'],[RESOLVED])
        sdk=json.loads((evidence/'sdk-provenance.json').read_bytes())
        self.assertEqual(sdk['version_evidence'],'UNKNOWN');self.assertIsNone(sdk['sdk_version'])
        self.assertEqual(list(self.quota.iterdir()),[])
        policy=evidence/'resource-policy.json'
        policy.chmod(0o600);policy.write_bytes(policy.read_bytes()+b' ')
        with self.assertRaises(artifact.ArtifactError):
            artifact.capture_builder_diagnostics(evidence/'candidate-build.log',self.sha,
                'completed',0,self.root/'tampered-evidence',evidence)
        self.assertFalse((self.root/'tampered-evidence').exists())

    def test_podman_never_pull_id_mapping_and_job_override(self):
        result=self.run_builder(HEPTA_IB_BUILD_ENGINE='podman',HEPTA_IB_BUILD_PULL_POLICY='never',
            HEPTA_IB_BUILD_JOBS='1',HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS='5')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=self.records();self.assertFalse(any(x[1]=='pull' for x in calls))
        runs=[x for x in calls if x[1]=='create'];self.assertEqual(len(runs),4)
        for call in runs:
            self.assertEqual(call[0],'podman');self.assertIn('--userns=keep-id',call)
            self.assertIn('CMAKE_BUILD_PARALLEL_LEVEL=1',call);self.assertIn(IMAGE_ID,call)
        compile_call=next(x for x in runs if '--parallel' in x)
        self.assertEqual(compile_call[compile_call.index('--parallel')+1],'1')
        evidence=Path(str(self.output)+'.diagnostics')
        policy=json.loads((evidence/'resource-policy.json').read_bytes())
        self.assertEqual(policy['jobs'],1);self.assertEqual(policy['phase_timeout_seconds'],5)
        self.assertEqual(policy['user_namespace'],'keep-id')
        identity=json.loads((evidence/'image-identity.json').read_bytes())
        self.assertEqual(identity['raw_image_id'],'b'*64);self.assertEqual(identity['image_id'],IMAGE_ID)

    def test_wrong_registry_and_rootful_podman_refuse_before_any_run(self):
        for options in [dict(INSPECT_DIGEST='evil.example/dockcross/linux-x64@sha256:'+'a'*64),
                        dict(HEPTA_IB_BUILD_ENGINE='podman',ROOTLESS='false')]:
            with self.subTest(options=options):
                self.calls.unlink(missing_ok=True)
                result=self.run_builder(**options)
                self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
                self.assertFalse(any(x[1]=='create' for x in self.records()))
                diagnostics=Path(str(self.output)+'.diagnostics')
                # Each attempt uses a fresh immutable output, including evidence.
                if diagnostics.exists():
                    import shutil
                    shutil.rmtree(diagnostics)

    def test_missing_real_toolchain_probe_and_failed_behavior_never_pack(self):
        for options in [dict(MISSING_TOOL='1'),dict(PROBE_STATUS='37')]:
            with self.subTest(options=options):
                result=self.run_builder(**options)
                self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
                diagnostics=Path(str(self.output)+'.diagnostics')
                status=json.loads((diagnostics/'build-status.json').read_bytes())
                self.assertEqual(status['phase'],'toolchain' if 'MISSING_TOOL' in options else 'probe')
                import shutil
                shutil.rmtree(diagnostics)

    def test_tool_hash_and_actual_compiler_version_failure_are_not_hidden_by_pipelines(self):
        broken=self.bin/'broken-cxx'
        broken.write_text('#!/bin/sh\necho misleading-version\nexit 17\n');broken.chmod(0o700)
        for options in [dict(BAD_TOOL_HASH='1'),dict(FAKE_CXX=str(broken))]:
            result=self.run_builder(**options)
            self.assertEqual(result.returncode,70,result.stderr);self.assertFalse(self.output.exists())
            evidence=Path(str(self.output)+'.diagnostics')
            self.assertEqual(json.loads((evidence/'build-status.json').read_bytes())['phase'],'toolchain')
            import shutil
            shutil.rmtree(evidence)

    def test_compile_timeout_and_invalid_parameters(self):
        for options in [dict(HEPTA_IB_BUILD_ENGINE='sh'),dict(HEPTA_IB_BUILD_PULL_POLICY='missing'),
                        dict(HEPTA_IB_BUILD_JOBS='0'),dict(HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS='1m')]:
            result=self.run_builder(**options);self.assertEqual(result.returncode,78)
            self.assertFalse(self.calls.exists());self.assertFalse(self.output.exists())
        result=self.run_builder(HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS='1',COMPILE_SLEEP='3')
        self.assertEqual(result.returncode,70,result.stderr);self.assertFalse(self.output.exists())
        status=json.loads(Path(str(self.output)+'.diagnostics/build-status.json').read_bytes())
        self.assertEqual(status['phase'],'compile')

    def test_actual_compiler_selection_is_pinned_and_missing_or_flagged_cxx_refuses(self):
        # Generic c++ exists, but an image-declared compiler must not fall back.
        for compiler in ['/missing/image-specific-cxx', 'c++ -funrecognized-flag']:
            result=self.run_builder(FAKE_CXX=compiler)
            self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
            calls=self.records()
            self.assertFalse(any('--build' in x for x in calls))
            import shutil
            shutil.rmtree(Path(str(self.output)+'.diagnostics'))
            self.calls.unlink()
        import shutil
        compiler=shutil.which('c++');self.assertIsNotNone(compiler)
        result=self.run_builder(FAKE_CXX=compiler)
        self.assertEqual(result.returncode,0,result.stderr)
        configure=next(x for x in self.records() if '-S' in x)
        self.assertIn('-DCMAKE_CXX_COMPILER='+compiler,configure)
        self.assertIn('CXX='+compiler,configure)
        evidence=Path(str(self.output)+'.diagnostics')
        self.assertIn('selected_compiler='+compiler+' path='+compiler,
                      (evidence/'toolchain.txt').read_text())

    def test_never_pull_missing_image_does_not_attempt_a_registry_or_run(self):
        result=self.run_builder(HEPTA_IB_BUILD_PULL_POLICY='never',INSPECT_MISSING='1')
        self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
        self.assertFalse(any(x[1] in ('pull','create','start') for x in self.records()))

    def test_owner_root_mapping_is_explicit_podman_only_and_preserves_host_identity(self):
        bad=self.run_builder(HEPTA_IB_BUILD_PODMAN_USER_MAPPING='owner-root')
        self.assertEqual(bad.returncode,78);self.assertFalse(self.calls.exists())
        result=self.run_builder(HEPTA_IB_BUILD_ENGINE='podman',
            HEPTA_IB_BUILD_PODMAN_USER_MAPPING='owner-root')
        self.assertEqual(result.returncode,0,result.stderr)
        creates=[x for x in self.records() if x[1]=='create']
        for call in creates:
            self.assertIn('--userns=keep-id:uid=0,gid=0',call)
            self.assertEqual(call[call.index('--user')+1],'0:0')
            self.assertIn('HEPTA_BUILDER_EXPECTED_UID=0',call)
        policy=json.loads(Path(str(self.output)+'.diagnostics/resource-policy.json').read_bytes())
        self.assertEqual(policy['host_uid'],os.getuid());self.assertEqual(policy['host_gid'],os.getgid())
        self.assertEqual((policy['container_uid'],policy['container_gid']),(0,0))
        self.assertEqual(policy['user_namespace'],'keep-id:uid=0,gid=0')

    def test_owner_root_rejects_a_root_host_caller(self):
        fake_id=self.bin/'id';fake_id.write_text('#!/bin/sh\nprintf 0\n');fake_id.chmod(0o700)
        result=self.run_builder(HEPTA_IB_BUILD_ENGINE='podman',
            HEPTA_IB_BUILD_PODMAN_USER_MAPPING='owner-root')
        self.assertEqual(result.returncode,78);self.assertFalse(self.calls.exists())

    def test_client_exit_does_not_leave_owned_workload_or_podman_cidfile(self):
        result=self.run_builder(HEPTA_IB_BUILD_ENGINE='podman',LEAVE_RUNNING='1')
        self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
        calls=self.records()
        self.assertEqual(sum(x[1:3]==['container','stop'] for x in calls),1)
        self.assertEqual(list((self.root/'engine-state').glob('*.json')),[])
        lifecycle=json.loads(Path(str(self.output)+'.diagnostics/container-lifecycle.json').read_bytes())
        self.assertTrue(lifecycle['all_owned_containers_removed'])
        self.assertEqual(len(lifecycle['entries']),1)
        self.assertFalse(lifecycle['entries'][0]['result']['completion']['success'])

    def test_cli_zero_with_failed_container_exit_or_oom_is_not_workload_success(self):
        for options in [dict(CONTAINER_EXIT='17'),dict(BOOL_EXIT='1'),dict(CONTAINER_OOM='1'),dict(CONTAINER_ERROR='synthetic error')]:
            with self.subTest(options=options):
                self.output=self.root/(next(iter(options))+'.tar')
                result=self.run_builder(**options)
                self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
                lifecycle=json.loads(Path(str(self.output)+'.diagnostics/container-lifecycle.json').read_bytes())
                self.assertTrue(lifecycle['all_owned_containers_removed'])
                self.assertFalse(lifecycle['entries'][0]['result']['completion']['success'])
                self.assertEqual(list(self.quota.iterdir()),[])

    def test_create_without_cidfile_is_bound_by_exact_name_before_orphan_cleanup(self):
        result=self.run_builder(OMIT_CID='1',CREATE_STATUS='124')
        self.assertEqual(result.returncode,124,result.stderr)
        self.assertFalse(self.output.exists());self.assertEqual(list(self.quota.iterdir()),[])
        self.assertFalse(any(x[1]=='start' for x in self.records()))
        self.assertEqual(list((self.root/'engine-state').glob('*.json')),[])

    def test_create_and_attach_share_one_original_phase_budget(self):
        result=self.run_builder(HEPTA_IB_BUILD_PHASE_TIMEOUT_SECONDS='2',
            CREATE_SLEEP='0.8',START_SLEEP='1.4')
        self.assertEqual(result.returncode,124,result.stderr);self.assertFalse(self.output.exists())
        status=json.loads(Path(str(self.output)+'.diagnostics/build-status.json').read_bytes())
        self.assertEqual(status['phase'],'toolchain')
        self.assertEqual(sum(x[1]=='start' for x in self.records()),1)
        self.assertEqual(list(self.quota.iterdir()),[])

    def test_failed_create_with_proven_empty_exact_name_cleans_workspace(self):
        result=self.run_builder(CREATE_NO_OBJECT='1')
        self.assertEqual(result.returncode,124,result.stderr);self.assertFalse(self.output.exists())
        self.assertEqual(list(self.quota.iterdir()),[])
        lifecycle=json.loads(Path(str(self.output)+'.diagnostics/container-lifecycle.json').read_bytes())
        self.assertTrue(lifecycle['all_owned_containers_removed'])
        self.assertIsNone(lifecycle['entries'][0]['removed']['container_id'])

    def test_unproven_foreign_create_and_cleanup_failure_preserve_workspace(self):
        for options in [dict(FOREIGN_CONTAINER='1'),dict(CLEANUP_FAIL='1'),dict(CREATE_NO_OBJECT='1',ENGINE_UNAVAILABLE='1')]:
            with self.subTest(options=options):
                # Every attempt has its own quota/output/engine metadata.
                self.output=self.root/(next(iter(options))+'.tar')
                result=self.run_builder(**options)
                self.assertNotEqual(result.returncode,0);self.assertFalse(self.output.exists())
                diagnostic=Path(str(self.output)+'.diagnostics')
                lifecycle=json.loads((diagnostic/'container-lifecycle.json').read_bytes())
                self.assertFalse(lifecycle['all_owned_containers_removed'])
                self.assertTrue(Path(lifecycle['workspace']).is_dir())
                if 'FOREIGN_CONTAINER' in options:
                    self.assertFalse(any(x[1:3] in (['container','stop'],['container','rm']) for x in self.records()))
                self.calls.unlink(missing_ok=True)

class BuilderIdentityTests(unittest.TestCase):
    def test_actual_id_and_hub_alias_are_checked_without_fabricating_digest_list(self):
        self.assertEqual(artifact.canonical_image_reference('docker.io/ubuntu@sha256:'+'a'*64),
                         artifact.canonical_image_reference('ubuntu@sha256:'+'a'*64))
        raw=[{'Id':'b'*64,'RepoDigests':[RESOLVED]}]
        value=artifact.image_identity(raw,REFERENCE,'podman','podman version 5.0','c'*64)
        self.assertEqual(value['raw_image_id'],'b'*64);self.assertEqual(raw[0]['RepoDigests'],[RESOLVED])
        for bad in ['evil.example/'+REFERENCE,'docker.io/not-dockcross/linux-x64@sha256:'+'a'*64]:
            with self.assertRaises(artifact.ArtifactError):
                artifact.image_identity([{'Id':IMAGE_ID,'RepoDigests':[bad]}],REFERENCE,'docker','Docker version 1','c'*64)
        with self.assertRaises(artifact.ArtifactError):
            artifact.image_identity(raw,REFERENCE,'docker','podman version 5.0','c'*64)

    def test_owner_receipt_binds_bid_but_does_not_invent_archive_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'libbid.a').write_bytes(b'archive')
            bid=hashlib.sha256(b'archive').hexdigest()
            receipt=root/'receipt';receipt.write_text(json.dumps({'sdk_version':'10.30.01','sdk_sha256':'a'*64,'bid_sha256':bid}))
            value=artifact.sdk_identity(root,receipt)
            self.assertEqual(value['sdk_version'],'10.30.01')
            self.assertFalse(value['upstream_archive_mapping_verified']);self.assertIsNone(value['bid_version'])
            receipt.write_text(json.dumps({'sdk_version':'10.30.01','sdk_sha256':'a'*64,'bid_sha256':'b'*64}))
            with self.assertRaises(artifact.ArtifactError):artifact.sdk_identity(root,receipt)

    def test_evidence_fifo_symlink_and_partial_publication_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();out=root/'out'
            log=root/'missing';p=source/'toolchain.txt'
            os.mkfifo(p,0o600)
            with self.assertRaises(artifact.ArtifactError):
                artifact.capture_builder_diagnostics(log,'a'*40,'toolchain',70,out,source)
            self.assertFalse(out.exists());p.unlink()
            p.symlink_to(root/'missing')
            with self.assertRaises(OSError):artifact.capture_builder_diagnostics(log,'a'*40,'toolchain',70,out,source)
            self.assertFalse(out.exists());p.unlink()
            with self.assertRaises(artifact.ArtifactError):
                artifact.capture_builder_diagnostics(log,'a'*40,'completed',0,out,source)
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
