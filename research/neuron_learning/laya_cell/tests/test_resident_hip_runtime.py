"""CPU-only runtime selection/publication fixtures; no Torch, GPU or services."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from laya_cell import resident_hip_runtime as runtime
from laya_cell.integrity import ContractError


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mapping(path, permissions='r-xp'):
    info = Path(path).stat()
    return (f'1000-2000 {permissions} 00000000 '
            f'{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x} {info.st_ino} {path}\n')


class SelectedHipRuntimeTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.release = self.root / 'release'
        source = self.release / runtime.MODULE; source.parent.mkdir(parents=True)
        source.write_bytes(Path(runtime.__file__).read_bytes())
        self.core = self.root / 'venv/lib/python3.14/site-packages/_rocm_sdk_core'
        (self.core / 'lib').mkdir(parents=True)
        (self.core / '__init__.py').write_bytes(b'# original resolver fixture\n')
        (self.core / 'share').mkdir()
        self.original_hip = self.core / 'lib/libamdhip64.so.7'; self.original_hip.write_bytes(b'original hip')
        dependencies = {}
        for name, suffix in (('libhsa-runtime64.so', '.1'), ('libamd_comgr.so', '.3'), ('librocm_kpack.so', '.0')):
            path = self.core / 'lib' / (name + suffix); path.write_bytes(name.encode())
            dependencies[name] = {'path': str(path), 'sha256': sha(path)}
        self.overlay = self.root / 'selected-overlay'
        package = self.overlay / '_rocm_sdk_core'; lib = package / 'lib'; lib.mkdir(parents=True)
        (package / '__init__.py').write_bytes((self.core / '__init__.py').read_bytes())
        (package / 'share').symlink_to(self.core / 'share')
        for item in dependencies.values():
            path = Path(item['path']); (lib / path.name).symlink_to(path)
        self.hip = lib / 'libamdhip64.so.7'; self.hip.write_bytes(b'selected patched HIP fixture')
        self.proof = {'schema': 'heptatrader.diagnostic-hip-overlay.v2', 'variant': 'patched',
            'original_core': str(self.core), 'overlay_root': str(self.overlay),
            'candidate_path': str(self.hip), 'candidate_sha256': sha(self.hip),
            'original_init': {'path': str(self.core / '__init__.py'), 'sha256': sha(self.core / '__init__.py')},
            'original_dependencies': dependencies, 'soname': 'libamdhip64.so.7',
            'needed': ['libhsa-runtime64.so.1', 'librocm_kpack.so.0'], 'kpack_split_artifacts_enabled': True}
        self.manifest = self.release / runtime.MANIFEST; self.manifest.parent.mkdir()
        self.config = {'runtime_directory': str(self.release), 'python': str(self.root / 'venv/bin/python'),
            'runtime_sha256': {runtime.MODULE: sha(source)}}
        self.write_manifest()
        self.enter(patch.object(runtime, '__file__', str(source)))
        self.enter(patch.object(runtime, '_CACHE', {}))
        self.enter(patch.object(runtime, '_ACTIVE', None))

    def enter(self, context):
        value = context.__enter__(); self.addCleanup(context.__exit__, None, None, None); return value

    def write_manifest(self):
        self.manifest.write_text(json.dumps({'schema': 'heptatrader.resident-hip-runtime.v1', 'overlay': self.proof}))
        self.config['runtime_sha256'][runtime.MANIFEST] = sha(self.manifest)
        self.config['hip_runtime'] = {'profile': runtime.PROFILE, 'manifest': runtime.MANIFEST, 'sha256': sha(self.manifest)}

    def activated(self, target='predict'):
        environment = runtime.worker_environment(self.config, {'PATH': '/usr/bin:/bin'})
        self.enter(patch.dict(os.environ, environment, clear=True))
        context = runtime.prepare_worker(self.config, target)
        with patch.object(runtime.importlib.machinery.PathFinder, 'find_spec', return_value=SimpleNamespace(origin=str(self.core / '__init__.py'))), \
             patch.object(Path, 'read_text', return_value=''), \
             patch.object(sys, 'modules', {k: v for k, v in sys.modules.items() if k not in ('torch', '_rocm_sdk_core')}), \
             patch.object(sys, 'path', list(sys.path)):
            runtime.activate(context)
        self.enter(patch.dict(sys.modules, {'_rocm_sdk_core': SimpleNamespace(__file__=str(self.overlay / '_rocm_sdk_core/__init__.py'))}))
        return context

    def maps(self, *, comgr=True):
        return mapping(self.hip) + ''.join(mapping(item['path']) for name, item in self.proof['original_dependencies'].items()
                                           if comgr or name != 'libamd_comgr.so')

    def test_no_selection_preserves_existing_environment_and_requires_no_files(self):
        environment = {'PATH': '/fixture'}
        self.assertIsNone(runtime.check_config({}))
        self.assertIsNone(runtime.worker_environment({}))
        self.assertIs(runtime.worker_environment({}, environment), environment)
        self.assertIsNone(runtime.prepare_worker({}, 'train'))
        self.assertIsNone(runtime.prove_before_publish({}))

    def test_declared_selection_cannot_silently_fall_back_when_pin_is_missing(self):
        del self.config['runtime_sha256'][runtime.MANIFEST]
        with self.assertRaisesRegex(ContractError, 'SOURCE_AND_MANIFEST_PINS_REQUIRED'):
            runtime.worker_environment(self.config)

    def test_replacement_library_is_independent_and_original_bytes_are_unchanged(self):
        before = {p: (p.stat().st_ino, p.read_bytes()) for p in [self.original_hip, *map(Path, [x['path'] for x in self.proof['original_dependencies'].values()])]}
        value = runtime.check_config(self.config)
        self.assertEqual(value['proof']['candidate_sha256'], sha(self.hip))
        self.assertFalse(self.hip.samefile(self.original_hip))
        self.assertEqual(before, {p: (p.stat().st_ino, p.read_bytes()) for p in before})

    def test_original_sdk_owner_group_writable_installation_is_read_without_changes(self):
        original_init = self.core / '__init__.py'
        original_init.write_bytes(b'')
        (self.overlay / '_rocm_sdk_core/__init__.py').write_bytes(b'')
        self.proof['original_init']['sha256'] = sha(original_init); self.write_manifest()
        self.core.chmod(0o775); (self.core / 'lib').chmod(0o775)
        original_init.chmod(0o664)
        originals = [original_init, *(Path(item['path']) for item in self.proof['original_dependencies'].values())]
        for path in originals[1:]: path.chmod(0o775)
        before = {path: (runtime.stamp(path.stat()), path.read_bytes()) for path in originals}
        value = runtime.check_config(self.config)
        self.assertEqual(value['proof']['candidate_sha256'], sha(self.hip))
        self.assertEqual(before, {path: (runtime.stamp(path.stat()), path.read_bytes()) for path in originals})

    def test_new_code_manifest_copied_init_and_candidate_still_reject_group_write(self):
        for path in (self.release / runtime.MODULE, self.manifest,
                     self.overlay / '_rocm_sdk_core/__init__.py', self.hip):
            with self.subTest(path=path.name):
                path.chmod(0o664)
                try:
                    with self.assertRaisesRegex(ContractError, 'FILE_OWNER_TYPE_OR_SIZE'):
                        runtime.check_config(self.config)
                finally:
                    path.chmod(0o600)

    def test_original_sdk_world_write_is_rejected(self):
        for path in (self.core / '__init__.py',
                     *(Path(item['path']) for item in self.proof['original_dependencies'].values())):
            with self.subTest(path=path.name):
                path.chmod(0o777)
                try:
                    with self.assertRaisesRegex(ContractError, 'FILE_OWNER_TYPE_OR_SIZE'):
                        runtime.check_config(self.config)
                finally:
                    path.chmod(0o600)

    def test_original_sdk_group_write_requires_the_effective_group(self):
        hsa = Path(self.proof['original_dependencies']['libhsa-runtime64.so']['path'])
        hsa.chmod(0o775)
        with patch.object(runtime.os, 'getegid', return_value=hsa.stat().st_gid + 1), \
             self.assertRaisesRegex(ContractError, 'FILE_OWNER_TYPE_OR_SIZE'):
            runtime.check_config(self.config)

    def test_sdk_permission_change_invalidates_cache_and_blocks_prior_publication(self):
        self.activated(); before = runtime.check_config(self.config)
        hsa = Path(self.proof['original_dependencies']['libhsa-runtime64.so']['path'])
        hsa.chmod(0o775)
        self.assertIsNot(runtime.check_config(self.config), before)
        with patch.object(Path, 'read_text', return_value=self.maps()), \
             self.assertRaisesRegex(ContractError, 'MAPPED_LIBRARY_IDENTITY_CHANGED'):
            runtime.prove_before_publish(self.config)

    def test_retargeted_dependency_is_rejected_even_if_other_bytes_are_equal(self):
        link = self.hip.parent / 'librocm_kpack.so.0'; link.unlink()
        replacement = self.root / 'librocm_kpack.so.0'; replacement.write_bytes(b'librocm_kpack.so')
        link.symlink_to(replacement)
        with self.assertRaisesRegex(ContractError, 'OVERLAY_FOREIGN_ENTRY'):
            runtime.check_config(self.config)

    def test_modified_candidate_or_dependency_is_rejected_by_the_manifest(self):
        for path in (self.hip, Path(self.proof['original_dependencies']['libamd_comgr.so']['path'])):
            with self.subTest(path=path.name):
                original = path.read_bytes(); path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ContractError, 'FILE_CHANGED'):
                    runtime.check_config(self.config)
                path.write_bytes(original)

    def test_dispatch_environment_is_private_and_keeps_original_queue_flags(self):
        original = {'PATH': '/fixture', 'LD_LIBRARY_PATH': '/existing/lib', 'DEBUG_HIP_DYNAMIC_QUEUES': '0'}
        before = dict(original); parent = dict(os.environ)
        selected = runtime.worker_environment(self.config, original)
        self.assertEqual(original, before); self.assertEqual(dict(os.environ), parent)
        self.assertEqual(selected['LD_LIBRARY_PATH'], str(self.hip.parent) + ':/existing/lib')
        self.assertEqual(selected['DEBUG_HIP_DYNAMIC_QUEUES'], '0')
        self.assertEqual(selected[runtime.ENV_PIN], self.config['hip_runtime']['sha256'])

    def test_preload_or_missing_pre_exec_selection_fails_without_loading_a_gpu(self):
        with self.assertRaisesRegex(ContractError, 'UNREVIEWED_PRELOAD'):
            runtime.worker_environment(self.config, {'LD_PRELOAD': '/foreign.so'})
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ContractError, 'EXEC_ENVIRONMENT_NOT_BOUND'):
            runtime.prepare_worker(self.config, 'train')

    def test_present_torch_prevents_switching_an_already_loaded_runtime(self):
        value = runtime.check_config(self.config)
        with patch.dict(sys.modules, {'torch': SimpleNamespace()}), self.assertRaisesRegex(ContractError, 'MUST_PRECEDE_TORCH'):
            runtime.activate({**value, 'activated': False})

    def test_publication_proof_is_bound_to_actual_objects_without_additional_gpu_work(self):
        context = self.activated('train')
        with patch.object(Path, 'read_text', return_value=self.maps()):
            before = runtime.prove(context, 'AFTER_TORCH_BEFORE_MODEL')
            final = runtime.prove_before_publish(self.config)
        self.assertEqual(final['phase'], 'BEFORE_PUBLISH')
        self.assertEqual(final['target'], 'train')
        self.assertEqual(final['libraries'], before['libraries'])
        self.assertEqual(final['candidate_sha256'], sha(self.hip))
        self.assertFalse(final['worker_admission_granted']); self.assertFalse(final['extra_gpu_probe_performed'])

    def test_two_loaded_hips_or_kpacks_fail_before_publication(self):
        self.activated()
        for name in ('libamdhip64.so.7', 'librocm_kpack.so.0'):
            with self.subTest(name=name):
                other = self.root / name; other.write_bytes(b'unselected fixture')
                with patch.object(Path, 'read_text', return_value=self.maps() + mapping(other)), \
                     self.assertRaisesRegex(ContractError, 'MAPPING_NOT_UNIQUE'):
                    runtime.prove_before_publish(self.config)

    def test_path_replaced_after_loading_cannot_validate_an_old_mapping(self):
        self.activated(); old_maps = self.maps()
        temporary = self.hip.with_name('replacement'); temporary.write_bytes(self.hip.read_bytes()); temporary.replace(self.hip)
        with patch.object(Path, 'read_text', return_value=old_maps), self.assertRaisesRegex(ContractError, 'MAPPED_LIBRARY_IDENTITY_CHANGED'):
            runtime.prove_before_publish(self.config)

    def test_modified_mapped_dependency_or_manifest_blocks_publication(self):
        self.activated(); original_maps = self.maps()
        hsa = Path(self.proof['original_dependencies']['libhsa-runtime64.so']['path']); hsa.write_bytes(b'changed')
        with patch.object(Path, 'read_text', return_value=original_maps), self.assertRaisesRegex(ContractError, 'MAPPED_LIBRARY_IDENTITY_CHANGED'):
            runtime.prove_before_publish(self.config)

    def test_hsa_is_required_before_publication_and_comgr_can_remain_lazy(self):
        self.activated()
        with patch.object(Path, 'read_text', return_value=self.maps(comgr=False)):
            self.assertEqual(runtime.prove_before_publish(self.config)['libraries']['libamd_comgr.so'], {'loaded': False})
        maps = mapping(self.hip) + mapping(self.proof['original_dependencies']['librocm_kpack.so']['path'])
        with patch.object(Path, 'read_text', return_value=maps), self.assertRaisesRegex(ContractError, 'MAPPING_NOT_UNIQUE:libhsa'):
            runtime.prove_before_publish(self.config)

    def test_selected_runtime_cannot_be_removed_or_changed_before_publication(self):
        self.activated()
        with self.assertRaisesRegex(ContractError, 'SELECTION_DROPPED'):
            runtime.prove_before_publish({})
        changed = deepcopy(self.config); changed['hip_runtime']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ContractError, 'PUBLICATION_RUNTIME_NOT_BOUND'):
            runtime.prove_before_publish(changed)

    def test_new_plan_freezes_selected_manifest_and_old_plans_are_not_rewritten(self):
        old = {'evaluation_runtime_sha256': {}}; new = deepcopy(old)
        runtime.bind_plan(self.config, new)
        runtime.check_plan(self.config, new)
        self.assertEqual(old, {'evaluation_runtime_sha256': {}})
        self.assertEqual(new['evaluation_runtime_sha256'][runtime.MANIFEST], self.config['hip_runtime']['sha256'])
        with self.assertRaisesRegex(ContractError, 'FROZEN_PLAN_RUNTIME_CHANGED'):
            runtime.check_plan(self.config, old)
        runtime.check_plan({}, old)


if __name__ == '__main__':
    unittest.main()
