#!/usr/bin/env python3
"""UID1000 original-sandbox admission check using only a private child in /tmp."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import activate_prepared_driver as driver
from precheck_current100k_registration import OriginalProducerAdmission, prepare_registrar_root
from sandbox import enter
from laya_cell.current100k_forward import _registration_lock


def main():
    if os.geteuid() != 1000:
        raise ValueError('EXACT_ORIGINAL_UID1000_REQUIRED_NO_BYPASS')
    os.umask(0o077)
    backend_path = Path(_registration_lock.__wrapped__.__code__.co_filename).resolve()
    backend_sha = hashlib.sha256(backend_path.read_bytes()).hexdigest()
    fixture = Path(tempfile.mkdtemp(prefix='hepta-registration-sandbox-', dir='/tmp'))
    release = fixture / 'producer'; release.mkdir(mode=0o700)
    script = release / 'driver.py'
    script.write_text("from pathlib import Path\nimport json,os,sys\nprint(json.dumps({'pid':os.getpid(),'proc':str(Path('/proc/self').resolve())}),flush=True)\nsys.stdin.readline()\n")
    manifest = release / 'manifest.json'; manifest.write_text('{"private_fixture":true}\n')
    for path in (script, manifest): path.chmod(0o400)
    fields = {}
    for name in ('period', 'dataset', 'oms'):
        path = fixture / (name + '.json'); path.write_text('{}\n'); path.chmod(0o400); fields[name] = str(path)
    export = fixture / 'export'; export.mkdir(mode=0o700)
    argv = [sys.executable, '-B', str(script)]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=dict(os.environ, INVOCATION_ID='private-sandbox-fixture'))
    original_argv_for = driver.argv_for
    try:
        greeting = json.loads(process.stdout.readline())
        if greeting != {'pid': process.pid, 'proc': '/proc/' + str(process.pid)}:
            raise ValueError('REAL_PROC_NAMESPACE_REQUIRED_NO_FIXTURE_MAPPING')
        driver.argv_for = lambda path: argv if path == manifest else original_argv_for(path)
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        config = {'producer_registration': {'manifest': str(manifest), 'sha256': sha(manifest)}}
        started = int(time.time() * 1000)
        proof = {'producer': {'manifest': str(manifest), 'manifest_sha256': sha(manifest),
            'release': str(release), 'runtime_sha256': {'driver.py': sha(script)},
            'pid_key': list(driver.pid_key(process.pid)), 'invocation_id': 'private-sandbox-fixture',
            'request_activity': {'started_at_ms': started}}}
        output = fixture / 'output'
        with OriginalProducerAdmission(config, {'registration_not_before_ms': 1}, proof) as admission:
            with prepare_registrar_root(output) as parent_fd:
                parent_identity = os.fstat(parent_fd)
                restrictions = enter({'output_dir': str(output), 'period_path': fields['period'],
                    'legacy_capture': fields['dataset'], 'oms_path': fields['oms'], 'export_dir': str(export),
                    'read_only_paths': [str(Path(__file__).resolve().parent), sys.prefix, *admission.read_only_paths]})
                with _registration_lock(output, root_parent_fd=parent_fd) as (directory_fd, confirm):
                    admission(); confirm(); os.fsync(directory_fd)
                os.fsync(parent_fd)
                observed_parent = os.fstat(parent_fd)
                if (parent_identity.st_dev, parent_identity.st_ino) != (observed_parent.st_dev, observed_parent.st_ino):
                    raise ValueError('FIXTURE_PARENT_FD_CHANGED')
                admission.producer['request_activity']['started_at_ms'] = int(time.time() * 1000) - 60000
                try: admission()
                except ValueError as error:
                    if 'REQUEST_EXPIRED_BEFORE_PUBLICATION' not in str(error): raise
                else: raise ValueError('EXPIRED_FIXTURE_ADMISSION_ACCEPTED')
                admission.producer['request_activity']['started_at_ms'] = int(time.time() * 1000)
                process.stdin.close(); process.wait(timeout=5)
                try: admission()
                except ValueError as error:
                    if 'PRODUCER_EXITED_OR_CHANGED' not in str(error): raise
                else: raise ValueError('EXITED_FIXTURE_PRODUCER_ACCEPTED')
                result = {'state': 'UID1000_ORIGINAL_SANDBOX_ADMISSION_VERIFIED', 'uid': os.geteuid(),
                    'fixture': str(fixture), 'sandbox': restrictions, 'actual_pidfd': True,
                    'actual_proc_namespace': True, 'parent_fd_fsync_inside_sandbox': True,
                    'original_registration_lock_inside_sandbox': True,
                    'backend_source': str(backend_path), 'backend_sha256': backend_sha,
                    'invocation_verified_before_sandbox': admission.initial_invocation_id == 'private-sandbox-fixture',
                    'invocation_reread_inside_sandbox': False,
                    'pidfd_startticks_argv_runtime_rechecked_inside_sandbox': True,
                    'expired_request_refused': True, 'exited_process_refused': True,
                    'real_producer_or_plan_modified': False, 'service_actions': [], 'broker_orders_sent': 0,
                    'gpu_loaded': 'torch' in sys.modules}
                (output / 'RESULT.json').write_text(json.dumps(result, sort_keys=True) + '\n')
                print(json.dumps(result, sort_keys=True))
    finally:
        driver.argv_for = original_argv_for
        if not process.stdin.closed: process.stdin.close()
        process.wait(timeout=5); process.stdout.close()


if __name__ == '__main__': main()
