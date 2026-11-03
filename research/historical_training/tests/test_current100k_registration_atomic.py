"""Real signed Current100k bundles, file locks and publication fault boundaries."""
import copy
import errno
import json
import os
from pathlib import Path
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from historical_training.tests import test_current100k_forward_scope as scope_fixture
from laya_cell import current100k_forward as forward
from laya_cell import financial_forward_witness as witness
from laya_cell.financial_forward_evaluate import validate_registration
from laya_cell.integrity import ContractError, digest, sha_file


class AtomicRegistrationTests(scope_fixture.ForwardScopeFixture, unittest.TestCase):
    scope_identifiers = scope_fixture.NewProducerScopeRegistrationTests.scope_identifiers
    complete_candidate = scope_fixture.NewProducerScopeRegistrationTests.complete_candidate
    prepare_new = scope_fixture.NewProducerScopeRegistrationTests.prepare_new

    def setUp(self):
        scope_fixture.NewProducerScopeRegistrationTests.setUp(self)
        roles = {}; keys = {}
        for role in ('registrar', 'predictor', 'evaluator', 'selector'):
            key = Ed25519PrivateKey.generate()
            path = self.root / (role + '.key')
            path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
            path.chmod(0o600); keys[role] = str(path)
            roles[role] = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        trust = {'scope': self.period, 'canonical_agentd_issuer': False, 'roles': roles}
        path = self.root / 'trust.json'; path.write_text(json.dumps(trust)); path.chmod(0o600)
        self.config.update(witness_trust_path=str(path), witness_trust_sha256=sha_file(path),
            witness_trust_digest=digest(trust), signing_role='registrar', role_key_path=keys['registrar'])
        self.output = Path(self.config['forward_root'])
        self.pending = self.output / forward.REGISTRATION_PENDING

    def args(self):
        return (self.config, self.directory, self.dataset,
            self.prior['directory'] / 'candidate.json', self.history)

    def options(self):
        return {'registration_scope': forward.NEW_PRODUCER_SCOPE,
                'prior_plan_path': self.prior_path, 'input_start_delay_ms': 900000}

    def register(self, **extra):
        return forward.register(*self.args(), **dict(self.options(), **extra))

    def state(self):
        return forward.registration_state(*self.args(), **self.options())

    def plans(self):
        return list((self.output / 'plans').glob('*/plan.json'))

    def snapshot(self, folder):
        return {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in folder.iterdir() if path.is_file()}

    def test_read_only_precheck_validates_candidate_and_prior_heads_without_allocating_root(self):
        self.assertEqual(self.state(), {'state': 'EMPTY', 'plan': None})
        self.assertFalse(self.output.exists())
        prior_head = self.prior['directory'] / 'value-head.safetensors'
        prior_head.write_bytes(b'changed-prior-head')
        with self.assertRaisesRegex(ContractError, 'HEAD_OR_ROWS'):
            self.state()
        self.assertFalse(self.output.exists())

    def test_only_complete_signed_bundle_is_published_to_original_reader_layout(self):
        old = self.prior_path.read_bytes()
        publish = forward._publish_registration_directory
        observed = []
        def commit(root_fd, plans_fd, plan_id):
            self.assertEqual(self.plans(), [])
            self.assertEqual(self.pending.parent, self.output)
            prepared = json.loads((self.pending / 'plan.json').read_text())
            validate_registration(self.config, self.pending / 'plan.json', prepared)
            self.assertEqual(set(p.name for p in self.pending.iterdir()), {'plan.json', 'registration.json'})
            observed.append(prepared)
            publish(root_fd, plans_fd, plan_id)
            self.assertEqual(len(self.plans()), 1)
            self.assertTrue((self.plans()[0].parent / 'registration.json').is_file())
        with mock.patch.object(forward, '_publish_registration_directory', side_effect=commit):
            plan = self.register()
        self.assertEqual(observed, [plan])
        self.assertEqual(plan['trial'], 2)
        self.assertEqual(plan['registration_publish_profile'], forward.REGISTRATION_PUBLISH_PROFILE)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.prior_path.read_bytes(), old)
        self.assertEqual(self.state()['state'], 'PUBLISHED')

    def test_same_request_reentry_is_read_only_and_keeps_original_window_and_signature(self):
        plan = self.register(); before = self.snapshot(self.plans()[0].parent)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
            with mock.patch.object(forward, '_publish_registration_directory', side_effect=AssertionError('must not re-publish')):
                with mock.patch.object(time, 'time', return_value=(plan['input_start_ms'] + 60000) / 1000):
                    self.assertEqual(self.register(), plan)
                    self.assertEqual(forward.existing_registration(*self.args(), **self.options()), plan)
        self.assertEqual(self.snapshot(self.plans()[0].parent), before)
        self.assertEqual(len(self.plans()), 1)

    def test_different_request_cannot_borrow_a_complete_registration(self):
        plan = self.register(); before = self.snapshot(self.plans()[0].parent)
        for update in ({'actor_update': True}, {'fixture_configuration_changed': True}):
            previous = copy.deepcopy(self.config); self.config.update(update)
            try:
                with self.assertRaises(ContractError):self.register()
            finally:self.config.clear(); self.config.update(previous)
        with self.assertRaisesRegex(ContractError, 'ALREADY_REGISTERED_NO_REPLAY'):
            forward.register(*self.args(), **dict(self.options(), input_start_delay_ms=300000))
        self.assertEqual(self.snapshot(self.plans()[0].parent), before)
        self.assertEqual(self.plans()[0].parent.name, plan['plan_id'])

    def test_local_signer_failure_resumes_original_complete_private_plan(self):
        with mock.patch.object(witness, 'sign_record', side_effect=TimeoutError('local signing interrupted')):
            with self.assertRaises(TimeoutError):self.register()
        self.assertEqual(self.plans(), [])
        plan_bytes = (self.pending / 'plan.json').read_bytes()
        plan = json.loads(plan_bytes)
        self.assertEqual(self.state()['state'], 'PREPARED')
        self.assertFalse(self.state()['registration_present'])
        with mock.patch.object(time, 'time', return_value=(plan['registered_at_ms'] + 60000) / 1000):
            self.assertEqual(self.register(), plan)
            self.assertEqual(self.state()['state'], 'PUBLISHED')
        self.assertEqual(self.plans()[0].read_bytes(), plan_bytes)

    def test_complete_original_plan_survives_lost_private_write_acknowledgement(self):
        write = forward.write_once
        def lost_acknowledgement(path, payload):
            result = write(path, payload)
            if path.name == 'plan.json':raise OSError(errno.EIO, 'original plan write reply lost')
            return result
        with mock.patch.object(forward, 'write_once', side_effect=lost_acknowledgement):
            with self.assertRaises(OSError):self.register()
        original = (self.pending / 'plan.json').read_bytes()
        self.assertEqual(self.state()['state'], 'PREPARED')
        self.assertFalse((self.pending / 'registration.json').exists())
        self.assertEqual(self.register(), json.loads(original))
        self.assertEqual(self.plans()[0].read_bytes(), original)

    def test_complete_private_signature_is_reused_after_failed_publication(self):
        with mock.patch.object(forward, '_publish_registration_directory', side_effect=OSError(errno.EIO, 'before rename')):
            with self.assertRaises(OSError):self.register()
        plan = json.loads((self.pending / 'plan.json').read_text())
        seal = (self.pending / 'registration.json').read_bytes()
        self.assertEqual(self.state()['state'], 'PREPARED')
        self.assertTrue(self.state()['registration_present'])
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
            self.assertEqual(self.register(), plan)
        self.assertEqual((self.plans()[0].parent / 'registration.json').read_bytes(), seal)

    def test_lost_rename_reply_reconciles_visible_original_without_second_effect(self):
        publish = forward._publish_registration_directory
        def lost_reply(*args):
            publish(*args)
            raise OSError(errno.EIO, 'rename completed but reply lost')
        with mock.patch.object(forward, '_publish_registration_directory', side_effect=lost_reply):
            with self.assertRaises(OSError):self.register()
        self.assertFalse(self.pending.exists())
        plan = json.loads(self.plans()[0].read_text())
        before = self.snapshot(self.plans()[0].parent)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
            with mock.patch.object(forward, '_publish_registration_directory', side_effect=AssertionError('must not rename twice')):
                self.assertEqual(self.register(), plan)
                self.assertEqual(self.state()['state'], 'PUBLISHED')
        self.assertEqual(self.snapshot(self.plans()[0].parent), before)
        self.assertEqual(len(self.plans()), 1)

    def test_failed_destination_fsync_after_rename_preserves_visible_original(self):
        fsync = os.fsync; failed = []
        def sync(fd):
            target = Path(os.readlink('/proc/self/fd/' + str(fd)))
            if target == self.output / 'plans' and self.plans() and not failed:
                failed.append(True)
                raise OSError(errno.EIO, 'published directory sync failed')
            return fsync(fd)
        with mock.patch.object(os, 'fsync', side_effect=sync):
            with self.assertRaises(OSError):self.register()
        self.assertEqual(failed, [True])
        self.assertFalse(self.pending.exists())
        plan = json.loads(self.plans()[0].read_text()); before = self.snapshot(self.plans()[0].parent)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
            with mock.patch.object(forward, '_publish_registration_directory', side_effect=AssertionError('must not rename twice')):
                self.assertEqual(self.register(), plan)
        self.assertEqual(self.snapshot(self.plans()[0].parent), before)

    def test_repeated_visible_result_sync_failure_stays_failed_until_original_bundle_is_durable(self):
        fsync = os.fsync; failures = []; synced = []
        def unavailable(fd):
            target = Path(os.readlink('/proc/self/fd/' + str(fd)))
            if target == self.output / 'plans' and self.plans():
                failures.append(target)
                raise OSError(errno.EIO, 'published parent durability unavailable')
            return fsync(fd)
        with mock.patch.object(os, 'fsync', side_effect=unavailable):
            with self.assertRaises(OSError):self.register()
            plan = json.loads(self.plans()[0].read_text()); folder = self.plans()[0].parent
            before = self.snapshot(folder)
            # Observation can report visible original bytes without claiming a
            # new durable write acknowledgement or calling any fsync itself.
            self.assertEqual(self.state()['state'], 'PUBLISHED')
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
                with mock.patch.object(forward, '_publish_registration_directory', side_effect=AssertionError('must not rename twice')):
                    with self.assertRaises(OSError):self.register()
            self.assertEqual(len(failures), 2)
            self.assertEqual(self.snapshot(folder), before)
        def recovered(fd):
            synced.append(Path(os.readlink('/proc/self/fd/' + str(fd))))
            return fsync(fd)
        with mock.patch.object(os, 'fsync', side_effect=recovered):
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
                with mock.patch.object(forward, '_publish_registration_directory', side_effect=AssertionError('must not rename twice')):
                    self.assertEqual(self.register(), plan)
        self.assertTrue({folder / 'plan.json', folder / 'registration.json', folder,
                         self.output / 'plans', self.output}.issubset(synced))
        self.assertEqual(self.snapshot(folder), before)

    def test_root_parent_sync_failure_is_retried_when_root_already_exists(self):
        fsync = os.fsync; attempted = []
        def unavailable(fd):
            if Path(os.readlink('/proc/self/fd/' + str(fd))) == self.output.parent:
                attempted.append(True)
                raise OSError(errno.EIO, 'new root parent entry not confirmed')
            return fsync(fd)
        with mock.patch.object(os, 'fsync', side_effect=unavailable):
            with self.assertRaises(OSError):self.register()
            self.assertTrue(self.output.is_dir()); inode = self.output.stat().st_ino
            self.assertEqual(list(self.output.iterdir()), [])
            with self.assertRaises(OSError):self.register()
            self.assertEqual(attempted, [True, True])
            self.assertEqual(self.output.stat().st_ino, inode)
            self.assertEqual(list(self.output.iterdir()), [])
        self.register()
        self.assertEqual(self.output.stat().st_ino, inode)
        self.assertEqual(len(self.plans()), 1)

    def test_preopened_parent_descriptor_is_bound_reused_and_left_owned_by_caller(self):
        parent = os.open(self.output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        original = os.open; initial = os.fstat(parent)
        def no_parent_reopen(path, *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)) and Path(os.fsdecode(path)) == self.output.parent:
                raise PermissionError('fixture forbids reopening parent after sandbox entry')
            return original(path, *args, **kwargs)
        try:
            with mock.patch.object(os, 'open', side_effect=no_parent_reopen):
                plan = self.register(root_parent_fd=parent)
                with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
                    self.assertEqual(self.register(root_parent_fd=parent), plan)
            self.assertFalse(os.get_inheritable(parent))
            self.assertEqual(os.fstat(parent).st_ino, initial.st_ino)
            self.assertEqual(len(self.plans()), 1)
        finally:os.close(parent)

    def test_wrong_closed_or_inheritable_parent_descriptor_is_rejected_before_root_creation(self):
        unrelated = self.root / 'unrelated'; unrelated.mkdir(mode=0o700)
        wrong = os.open(unrelated, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        correct = os.open(self.output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with self.assertRaisesRegex(ContractError, 'PARENT_CHANGED'):self.register(root_parent_fd=wrong)
            os.set_inheritable(correct, True)
            with self.assertRaisesRegex(ContractError, 'MUST_BE_CLOEXEC'):self.register(root_parent_fd=correct)
        finally:os.close(wrong); os.close(correct)
        with self.assertRaisesRegex(ContractError, 'PARENT_FD_INVALID'):self.register(root_parent_fd=correct)
        self.assertFalse(self.output.exists())

    def test_published_recovery_also_requires_root_parent_sync_acknowledgement(self):
        plan = self.register(); folder = self.plans()[0].parent; before = self.snapshot(folder); fsync = os.fsync
        def unavailable(fd):
            if Path(os.readlink('/proc/self/fd/' + str(fd))) == self.output.parent:
                raise OSError(errno.EIO, 'published root parent sync unavailable')
            return fsync(fd)
        with mock.patch.object(os, 'fsync', side_effect=unavailable):
            with self.assertRaises(OSError):self.register()
            self.assertEqual(self.state()['state'], 'PUBLISHED')
        self.assertEqual(self.snapshot(folder), before)
        self.assertEqual(self.register(), plan)
        self.assertEqual(self.snapshot(folder), before)

    def test_recovery_sync_detects_replaced_original_signature(self):
        self.register(); folder = self.plans()[0].parent; seal = folder / 'registration.json'
        fsync = os.fsync; replaced = []
        def changed(fd):
            result = fsync(fd)
            if Path(os.readlink('/proc/self/fd/' + str(fd))) == folder / 'plan.json' and not replaced:
                seal.write_bytes(seal.read_bytes() + b'\n'); replaced.append(True)
            return result
        with mock.patch.object(os, 'fsync', side_effect=changed):
            with self.assertRaisesRegex(ContractError, 'RECORD_CHANGED|BUNDLE_CHANGED'):self.register()
        self.assertEqual(replaced, [True])
        self.assertEqual(len(self.plans()), 1)

    def test_initial_admission_failure_has_no_root_or_signature_effect(self):
        admission = mock.Mock(side_effect=ContractError('fixture original request expired'))
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('admission must precede signing')):
            with self.assertRaisesRegex(ContractError, 'original request expired'):
                self.register(admission_check=admission)
        self.assertFalse(self.output.exists())
        self.assertEqual(admission.call_count, 1)

    def test_admission_is_rechecked_before_private_plan_is_created(self):
        def admission():
            if (self.output / 'plans').exists():raise ContractError('fixture producer exited before private plan')
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not sign after producer exit')):
            with self.assertRaisesRegex(ContractError, 'producer exited'):
                self.register(admission_check=admission)
        self.assertFalse(self.pending.exists())
        self.assertEqual(self.plans(), [])

    def test_producer_exit_after_original_plan_write_is_detected_before_signing(self):
        write = forward.write_once; alive = [True]
        def admission():
            if not alive[0]:raise ContractError('fixture original producer exited')
        def exited(path, payload):
            result = write(path, payload)
            if path.name == 'plan.json':alive[0] = False
            return result
        with mock.patch.object(forward, 'write_once', side_effect=exited):
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not sign for departed producer')):
                with self.assertRaisesRegex(ContractError, 'producer exited'):
                    self.register(admission_check=admission)
        self.assertTrue((self.pending / 'plan.json').is_file())
        self.assertFalse((self.pending / 'registration.json').exists())
        self.assertEqual(self.plans(), [])

    def test_signing_that_crosses_original_request_freshness_cannot_publish_a_seal(self):
        clock = [time.time()]; observed = clock[0]; sign = witness.sign_record
        def admission():
            if time.time() - observed >= 60:raise ContractError('fixture original request no longer fresh')
        def delayed_signature(*args):
            result = sign(*args); clock[0] += 61
            return result
        with mock.patch.object(time, 'time', side_effect=lambda: clock[0]):
            with mock.patch.object(witness, 'sign_record', side_effect=delayed_signature):
                with self.assertRaisesRegex(ContractError, 'no longer fresh'):
                    self.register(admission_check=admission)
            plan = json.loads((self.pending / 'plan.json').read_text())
            self.assertEqual(plan['registered_at_ms'], int(observed * 1000))
            self.assertFalse((self.pending / 'registration.json').exists())
            self.assertEqual(self.plans(), [])

    def test_io_crossing_wake_deadline_is_rechecked_at_publication_and_keeps_original_intent(self):
        clock = [time.time()]; deadline = clock[0] + 30; sync = forward._sync_registration_bundle
        def admission():
            if time.time() > deadline:raise ContractError('fixture wake deadline expired')
        def delayed_sync(*args):
            result = sync(*args); clock[0] += 31
            return result
        with mock.patch.object(time, 'time', side_effect=lambda: clock[0]):
            with mock.patch.object(forward, '_sync_registration_bundle', side_effect=delayed_sync):
                with self.assertRaisesRegex(ContractError, 'wake deadline expired'):
                    self.register(admission_check=admission)
            self.assertEqual(self.plans(), [])
            plan = json.loads((self.pending / 'plan.json').read_text()); before = self.snapshot(self.pending)
            self.assertEqual(self.state()['state'], 'PREPARED')
            # A later authorized original-plan continuation uses a newly
            # checked callback, not a changed plan/request identity or clock.
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must reuse original seal')):
                self.assertEqual(self.register(admission_check=lambda: None), plan)
            self.assertEqual(self.snapshot(self.plans()[0].parent), before)

    def test_published_reconciliation_never_requests_fresh_admission(self):
        plan = self.register(); before = self.snapshot(self.plans()[0].parent)
        admission = mock.Mock(side_effect=AssertionError('published recovery cannot ask for a new admission'))
        with mock.patch.object(time, 'time', return_value=(plan['input_start_ms'] + 60000) / 1000):
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not re-sign')):
                self.assertEqual(self.register(admission_check=admission), plan)
        admission.assert_not_called()
        self.assertEqual(self.snapshot(self.plans()[0].parent), before)

    def test_partial_private_plan_is_retained_and_never_repaired_or_replaced(self):
        write = forward.write_once
        def interrupted(path, payload):
            if path.name == 'plan.json':
                path.write_bytes(b'{"incomplete":'); path.chmod(0o600)
                raise OSError(errno.EIO, 'partial private plan')
            return write(path, payload)
        with mock.patch.object(forward, 'write_once', side_effect=interrupted):
            with self.assertRaises(OSError):self.register()
        before = self.snapshot(self.pending)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not sign partial plan')):
            with self.assertRaises(ValueError):self.register()
        self.assertEqual(self.snapshot(self.pending), before)
        self.assertEqual(self.plans(), [])

    def test_partial_private_signature_is_retained_and_never_overwritten(self):
        write = forward.write_once
        def interrupted(path, payload):
            if path.name == 'registration.json':
                path.write_bytes(b'{"partial_signature":'); path.chmod(0o600)
                raise OSError(errno.EIO, 'partial private signature')
            return write(path, payload)
        with mock.patch.object(forward, 'write_once', side_effect=interrupted):
            with self.assertRaises(OSError):self.register()
        before = self.snapshot(self.pending)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not replace signature bytes')):
            with self.assertRaises(ValueError):self.register()
        self.assertEqual(self.snapshot(self.pending), before)
        self.assertEqual(self.plans(), [])

    def test_expired_private_plan_cannot_backfill_or_restart_the_original_window(self):
        with mock.patch.object(witness, 'sign_record', side_effect=TimeoutError('local signing interrupted')):
            with self.assertRaises(TimeoutError):self.register()
        plan = json.loads((self.pending / 'plan.json').read_text()); before = self.snapshot(self.pending)
        with mock.patch.object(time, 'time', return_value=plan['input_start_ms'] / 1000):
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not sign expired plan')):
                with self.assertRaisesRegex(ContractError, 'EXPIRED_NO_BACKFILL'):self.register()
                with self.assertRaisesRegex(ContractError, 'EXPIRED_NO_BACKFILL'):self.state()
        self.assertEqual(self.snapshot(self.pending), before)
        self.assertEqual(self.plans(), [])

    def test_concurrent_same_root_registration_uses_real_existing_registrar_lock(self):
        entered = threading.Event(); release = threading.Event(); sign = witness.sign_record
        def held_sign(*args):
            entered.set()
            if not release.wait(5):raise TimeoutError('fixture did not release signer')
            return sign(*args)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with mock.patch.object(witness, 'sign_record', side_effect=held_sign):
                first = pool.submit(self.register)
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaises(BlockingIOError):self.register()
                finally:release.set()
                plan = first.result(timeout=10)
        self.assertEqual(len(self.plans()), 1)
        self.assertEqual(self.plans()[0].parent.name, plan['plan_id'])
        self.assertEqual(self.register(), plan)

    def test_unknown_empty_destination_is_never_overwritten(self):
        publish = forward._publish_registration_directory
        def collision(root_fd, plans_fd, plan_id):
            (self.output / 'plans' / plan_id).mkdir(mode=0o700)
            publish(root_fd, plans_fd, plan_id)
        with mock.patch.object(forward, '_publish_registration_directory', side_effect=collision):
            with self.assertRaises(FileExistsError):self.register()
        plan = json.loads((self.pending / 'plan.json').read_text())
        unknown = self.output / 'plans' / plan['plan_id']; identity = unknown.stat().st_ino
        before = self.snapshot(self.pending)
        self.assertEqual(list(unknown.iterdir()), [])
        with self.assertRaisesRegex(ContractError, 'MULTIPLE_OR_UNKNOWN_RESULTS'):self.register()
        self.assertEqual(unknown.stat().st_ino, identity)
        self.assertEqual(self.snapshot(self.pending), before)
        self.assertEqual(self.plans(), [])

    def test_lock_replacement_is_detected_before_signing(self):
        write = forward.write_once
        def replace_lock(path, payload):
            result = write(path, payload)
            if path.name == 'plan.json':
                lock = self.output / 'registrar.lock'
                lock.unlink(); lock.touch(mode=0o600)
            return result
        with mock.patch.object(forward, 'write_once', side_effect=replace_lock):
            with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('lost lock must not sign')):
                with self.assertRaisesRegex(ContractError, 'LOCK_CHANGED'):self.register()
        self.assertTrue((self.pending / 'plan.json').is_file())
        self.assertFalse((self.pending / 'registration.json').exists())
        self.assertEqual(self.plans(), [])

    def test_history_bytes_changed_during_preparation_are_not_committed(self):
        sign = witness.sign_record
        def changed_history(*args):
            seal = sign(*args)
            self.prior_path.write_bytes(self.prior_path.read_bytes() + b'\n')
            return seal
        with mock.patch.object(witness, 'sign_record', side_effect=changed_history):
            with self.assertRaisesRegex(ContractError, 'INPUT_CHANGED'):self.register()
        self.assertTrue((self.pending / 'registration.json').is_file())
        self.assertEqual(self.plans(), [])

    def test_unsigned_private_plan_cannot_change_frozen_runtime_or_trial(self):
        for field, value in (('trial', 99), ('runtime_sha256', {'current100k_forward.py': '0' * 64})):
            with self.subTest(field=field):
                if not self.pending.exists():
                    with mock.patch.object(witness, 'sign_record', side_effect=TimeoutError('local signing interrupted')):
                        with self.assertRaises(TimeoutError):self.register()
                    original = (self.pending / 'plan.json').read_bytes()
                plan = json.loads(original); plan[field] = value
                plan['plan_id'] = digest({k: v for k, v in plan.items() if k != 'plan_id'})
                path = self.pending / 'plan.json'; path.write_text(json.dumps(plan)); before = self.snapshot(self.pending)
                with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not sign changed original intent')):
                    with self.assertRaisesRegex(ContractError, 'ORIGINAL_SCOPE_CHANGED'):self.register()
                    with self.assertRaisesRegex(ContractError, 'ORIGINAL_SCOPE_CHANGED'):self.state()
                self.assertEqual(self.snapshot(self.pending), before)
                self.assertEqual(self.plans(), [])

    def test_invalid_published_signature_is_not_repaired_or_registered_again(self):
        self.register(); seal = self.plans()[0].parent / 'registration.json'; seal.write_text('{}')
        before = self.snapshot(seal.parent)
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not repair published proof')):
            with self.assertRaises(ContractError):self.register()
        self.assertEqual(self.snapshot(seal.parent), before)
        self.assertEqual(len(self.plans()), 1)

    def test_root_alias_and_unprivate_output_are_rejected_without_registration(self):
        target = self.root / 'aliased-output'; target.mkdir(mode=0o700)
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ContractError, 'CANONICAL_ROOT_REQUIRED'):self.state()
        with self.assertRaisesRegex(ContractError, 'CANONICAL_ROOT_REQUIRED'):self.register()
        self.assertEqual(list(target.iterdir()), [])
        self.output.unlink(); self.output.mkdir(mode=0o700); self.output.chmod(0o755)
        with self.assertRaisesRegex(ContractError, 'PRIVATE_DIRECTORY'):self.register()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_unknown_private_preparation_and_symlink_seal_are_preserved(self):
        self.output.mkdir(mode=0o700); self.pending.mkdir(mode=0o700)
        with self.assertRaisesRegex(ContractError, 'ORIGINAL_PLAN_UNAVAILABLE'):self.register()
        self.assertEqual(list(self.pending.iterdir()), [])
        self.pending.rmdir()
        with mock.patch.object(witness, 'sign_record', side_effect=TimeoutError('local signing interrupted')):
            with self.assertRaises(TimeoutError):self.register()
        seal = self.pending / 'registration.json'; seal.symlink_to(self.root / 'missing-seal')
        plan = (self.pending / 'plan.json').read_bytes()
        with mock.patch.object(witness, 'sign_record', side_effect=AssertionError('must not follow seal alias')):
            with self.assertRaisesRegex(ContractError, 'PRIVATE_FILE'):self.register()
        self.assertTrue(seal.is_symlink()); self.assertFalse(seal.exists())
        self.assertEqual((self.pending / 'plan.json').read_bytes(), plan)
        self.assertEqual(self.plans(), [])


if __name__ == '__main__':
    unittest.main()
