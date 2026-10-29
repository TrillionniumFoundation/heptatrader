"""Select one accepted HIP in bounded resident workers, before importing Torch.

This module grants no worker admission and never changes a hold, unit, SDK, or
trial. The existing supervisor owns admission, lifetime and recovery. A selected
runtime is staged and accepted by the operator; this code verifies its bytes.
Finite acceptance and hold release remain outside the resident runtime.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import json
import os
from pathlib import Path
import re
import stat
import sys

from .integrity import ContractError

PROFILE = 'PINNED_HIP_KPACK_V1'
MANIFEST = 'gpu/hip-runtime.json'
MODULE = 'learn/laya_cell/resident_hip_runtime.py'
QUEUE_FLAGS = ('DEBUG_HIP_DYNAMIC_QUEUES', 'GPU_MAX_HW_QUEUES')
ENV_PIN = 'HEPTA_RESIDENT_HIP_MANIFEST_SHA256'
LIBRARIES = ('libamdhip64.so', 'libhsa-runtime64.so', 'libamd_comgr.so', 'librocm_kpack.so')
_CACHE = {}
_ACTIVE = None


def need(value, reason):
    if not value:
        raise ContractError('HIP_RUNTIME_' + reason)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def bound_file(path, expected, snapshots, *, json_value=False, limit=1024**3, original_sdk=False):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'CANONICAL_FILE_REQUIRED')
    need(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'FILE_PIN_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    digest = hashlib.sha256(); chunks = []
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        # Installed SDK inputs may retain the owner's group-write permission.
        # Newly staged code, manifests and replacement files keep the stricter default.
        mode_ok = not before.st_mode & 0o022 or (
            original_sdk and before.st_gid == os.getegid() and not before.st_mode & 0o002)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and mode_ok and before.st_size <= limit, 'FILE_OWNER_TYPE_OR_SIZE')
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk); need(size <= limit, 'FILE_GREW_PAST_BOUND')
            digest.update(chunk)
            if json_value: chunks.append(chunk)
        after = os.fstat(stream.fileno())
    need(stamp(before) == stamp(after) == stamp(path.lstat()) and digest.hexdigest() == expected,
         'FILE_CHANGED:' + path.name)
    snapshots[str(path)] = stamp(after)
    if not json_value:
        return {'path': str(path), 'sha256': expected, 'bytes': size}
    def unique(pairs):
        value = {}
        for key, item in pairs:
            need(key not in value, 'DUPLICATE_JSON_KEY'); value[key] = item
        return value
    return json.loads(b''.join(chunks), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ContractError('HIP_RUNTIME_NONFINITE_JSON')))


def unchanged(snapshots):
    return all(stamp(Path(path).lstat()) == old for path, old in snapshots.items())


def check_config(config):
    """CPU-only validation. Omission retains the existing SDK; invalid opt-in fails."""
    selection = config.get('hip_runtime')
    if selection is None:
        return None
    need(isinstance(selection, dict) and set(selection) == {'profile', 'manifest', 'sha256'}
         and selection['profile'] == PROFILE and selection['manifest'] == MANIFEST, 'CONFIGURATION_PROFILE')
    runtime = Path(config['runtime_directory']); pins = config['runtime_sha256']
    need(runtime.is_absolute() and runtime.resolve() == runtime and pins.get(MANIFEST) == selection['sha256']
         and re.fullmatch('[0-9a-f]{64}', str(pins.get(MODULE, ''))), 'SOURCE_AND_MANIFEST_PINS_REQUIRED')
    key = (str(runtime), config['python'], selection['sha256'], pins[MODULE])
    cached = _CACHE.get(key)
    if cached is not None and unchanged(cached['snapshots']):
        return cached
    snapshots = {}
    bound_file(runtime / MODULE, pins[MODULE], snapshots, limit=2 * 1024**2)
    need(Path(__file__).resolve() == runtime / MODULE, 'LOADER_NOT_FROM_PINNED_RELEASE')
    manifest_path = runtime / MANIFEST
    manifest = bound_file(manifest_path, selection['sha256'], snapshots, json_value=True, limit=256 * 1024)
    need(manifest.get('schema') == 'heptatrader.resident-hip-runtime.v1', 'MANIFEST_SCHEMA')
    proof = manifest['overlay']; core = Path(proof['original_core']); root = Path(proof['overlay_root'])
    package = root / '_rocm_sdk_core'; lib = package / 'lib'
    need(core.is_absolute() and core.resolve() == core and core.parents[3] == Path(config['python']).parent.parent
         and root.is_absolute() and root.resolve() == root and not root.is_relative_to(core), 'ORIGINAL_VENV_OR_OVERLAY_SCOPE')
    need(proof.get('schema') == 'heptatrader.diagnostic-hip-overlay.v2' and proof.get('variant') == 'patched'
         and proof.get('candidate_path') == str(lib / 'libamdhip64.so.7')
         and proof.get('soname') == 'libamdhip64.so.7' and proof.get('needed', []).count('librocm_kpack.so.0') == 1
         and all('/' not in x for x in proof.get('needed', []))
         and proof.get('kpack_split_artifacts_enabled') is True, 'OVERLAY_NOT_BOUND_TO_ACCEPTED_BUILD')
    dependencies = proof['original_dependencies']
    need(set(dependencies) == set(LIBRARIES[1:]), 'ORIGINAL_DEPENDENCIES_REQUIRED')
    need(Path(proof['original_init']['path']) == core / '__init__.py', 'ORIGINAL_CORE_INIT_REQUIRED')
    for name, item in dependencies.items():
        path = Path(item['path'])
        need(path.parent == core / 'lib' and (path.name == name or path.name.startswith(name + '.')),
             'DEPENDENCY_OUTSIDE_ORIGINAL_CORE')
        bound_file(path, item['sha256'], snapshots, original_sdk=True)
    bound_file(core / '__init__.py', proof['original_init']['sha256'], snapshots, limit=1024**2, original_sdk=True)
    bound_file(package / '__init__.py', proof['original_init']['sha256'], snapshots, limit=1024**2)
    bound_file(lib / 'libamdhip64.so.7', proof['candidate_sha256'], snapshots)
    need(set(x.name for x in root.iterdir()) == {'_rocm_sdk_core'}, 'OVERLAY_ROOT_CONTENTS')
    expected_package = {x.name for x in core.iterdir()} - {'__pycache__'}
    need(set(x.name for x in package.iterdir()) == expected_package, 'OVERLAY_PACKAGE_CONTENTS')
    expected_lib = {x.name for x in (core / 'lib').iterdir() if not (x.name == 'libamdhip64.so' or x.name.startswith('libamdhip64.so.'))} | {'libamdhip64.so.7'}
    need(set(x.name for x in lib.iterdir()) == expected_lib, 'OVERLAY_LIBRARY_CONTENTS')
    for parent, original, exclusions in ((package, core, {'__init__.py', 'lib'}), (lib, core / 'lib', {'libamdhip64.so.7'})):
        for path in parent.iterdir():
            if path.name in exclusions: continue
            need(path.is_symlink() and os.readlink(path) == str(original / path.name), 'OVERLAY_FOREIGN_ENTRY')
            snapshots[str(path)] = stamp(path.lstat()); snapshots[str(original / path.name)] = stamp((original / path.name).lstat())
    for path in (root, package, lib):
        info = path.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022, 'OVERLAY_DIRECTORY_OWNER')
        snapshots[str(path)] = stamp(info)
    value = {'manifest_sha256': selection['sha256'], 'manifest_path': str(manifest_path), 'manifest': manifest,
             'proof': proof, 'snapshots': snapshots, 'library_dir': str(lib), 'readonly': [str(manifest_path), str(root)]}
    need(unchanged(snapshots), 'INPUT_CHANGED_DURING_VALIDATION')
    if len(_CACHE) >= 4: _CACHE.clear()
    _CACHE[key] = value
    return value


def worker_environment(config, inherited=None):
    value = check_config(config)
    if value is None:
        return inherited
    env = dict(os.environ if inherited is None else inherited)
    need(not env.get('LD_PRELOAD'), 'UNREVIEWED_PRELOAD')
    previous = env.get('LD_LIBRARY_PATH', '')
    env['LD_LIBRARY_PATH'] = value['library_dir'] + (os.pathsep + previous if previous else '')
    env[ENV_PIN] = value['manifest_sha256']
    return env


def check_guard_selection(config, original):
    """Financial workers retain the runtime already accepted by their guard.

    A code-only worker package may have a new directory/inventory. Its selected
    HIP manifest and Python environment must remain those of the original owner.
    This CPU-only check grants no resource admission and imports no GPU package.
    """
    need(config.get('hip_runtime') == original.get('hip_runtime'), 'GUARD_SELECTION_CHANGED_OR_DROPPED')
    if config.get('hip_runtime') is not None:
        need(config.get('python') == original.get('python'), 'GUARD_PYTHON_CHANGED')


def check_publication_proof(proof, manifest_sha, target):
    """Check the proof carried by a frozen/signed financial publication."""
    if manifest_sha is None:
        need(proof is None, 'UNSELECTED_PUBLICATION_PROOF')
        return
    need(isinstance(manifest_sha, str) and re.fullmatch('[0-9a-f]{64}', manifest_sha)
         and isinstance(proof, dict) and proof.get('schema') == 'heptatrader.resident-hip-runtime-proof.v1'
         and proof.get('phase') == 'BEFORE_PUBLISH' and proof.get('target') == target
         and proof.get('manifest_sha256') == manifest_sha, 'PUBLICATION_PROOF_BINDING')
    libraries = proof.get('libraries')
    need(isinstance(proof.get('candidate_sha256'), str)
         and re.fullmatch('[0-9a-f]{64}', proof['candidate_sha256'])
         and type(proof.get('pid')) is int and proof['pid'] > 0
         and proof.get('worker_admission_granted') is False and proof.get('extra_gpu_probe_performed') is False
         and isinstance(libraries, dict) and set(libraries) == set(LIBRARIES), 'PUBLICATION_PROOF_LIBRARIES')
    for name, library in libraries.items():
        need(isinstance(library, dict) and type(library.get('loaded')) is bool, 'PUBLICATION_PROOF_LIBRARIES')
        if not library['loaded']:
            need(name == 'libamd_comgr.so' and set(library) == {'loaded'}, 'PUBLICATION_PROOF_LIBRARIES')
            continue
        need(isinstance(library.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', library['sha256'])
             and all(type(library.get(key)) is int and library[key] >= 0 for key in ('device_major', 'device_minor'))
             and type(library.get('inode')) is int and library['inode'] > 0, 'PUBLICATION_PROOF_LIBRARIES')
    need(libraries['libamdhip64.so']['sha256'] == proof['candidate_sha256'], 'PUBLICATION_PROOF_CANDIDATE')


def prepare_worker(config, target):
    value = check_config(config)
    if value is None: return None
    need(target in ('train', 'predict', 'owner-predict') and os.environ.get(ENV_PIN) == value['manifest_sha256']
         and os.environ.get('LD_LIBRARY_PATH', '').split(os.pathsep)[0] == value['library_dir'], 'EXEC_ENVIRONMENT_NOT_BOUND')
    return {**value, 'target': target, 'activated': False,
            'queue_flags': {name: os.environ.get(name) for name in QUEUE_FLAGS}}


def parse_maps(text):
    groups = {name: {} for name in LIBRARIES}
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6: continue
        path = re.sub(r'\\([0-7]{3})', lambda x: chr(int(x.group(1), 8)), fields[5])
        basename = Path(path.removesuffix(' (deleted)')).name
        name = next((name for name in LIBRARIES if basename == name or basename.startswith(name + '.')), None)
        if name is None: continue
        need(not path.endswith(' (deleted)'), 'MAPPED_LIBRARY_DELETED')
        major, minor = (int(part, 16) for part in fields[3].split(':'))
        group = groups[name].setdefault((major, minor, int(fields[4])), {'paths': set(), 'executable': False})
        group['paths'].add(path); group['executable'] |= 'x' in fields[1]
    return groups


def activate(context):
    global _ACTIVE
    if context is None: return
    need(_ACTIVE is None and 'torch' not in sys.modules and '_rocm_sdk_core' not in sys.modules
         and not parse_maps(Path('/proc/self/maps').read_text())['libamdhip64.so'], 'ACTIVATION_MUST_PRECEDE_TORCH')
    spec = importlib.machinery.PathFinder.find_spec('_rocm_sdk_core', sys.path)
    need(spec is not None and spec.origin is not None
         and Path(spec.origin).parent.resolve() == Path(context['proof']['original_core']), 'ORIGINAL_SDK_RESOLVER_CHANGED')
    sys.path.insert(0, context['proof']['overlay_root'])
    context['activated'] = True; _ACTIVE = context


def prove(context, phase):
    if context is None: return None
    need(context is _ACTIVE and context['activated'] and phase in ('AFTER_TORCH_BEFORE_MODEL', 'BEFORE_PUBLISH'), 'WORKER_CONTEXT_REQUIRED')
    need({name: os.environ.get(name) for name in QUEUE_FLAGS} == context['queue_flags'], 'QUEUE_FLAGS_CHANGED')
    proof = context['proof']; parsed = parse_maps(Path('/proc/self/maps').read_text())
    expected = {'libamdhip64.so': {'path': proof['candidate_path'], 'sha256': proof['candidate_sha256']}, **proof['original_dependencies']}
    observed = {}
    for name, groups in parsed.items():
        required = name in ('libamdhip64.so', 'librocm_kpack.so') or (phase == 'BEFORE_PUBLISH' and name == 'libhsa-runtime64.so')
        need(len(groups) == 1 if required else len(groups) <= 1, 'MAPPING_NOT_UNIQUE:' + name)
        if not groups:
            observed[name] = {'loaded': False}; continue
        key, group = next(iter(groups.items())); need(group['executable'], 'MAPPING_NOT_EXECUTABLE:' + name)
        canonical = Path(expected[name]['path'])
        for spelling in group['paths']:
            path = Path(spelling); info = path.stat()
            need(path.resolve() == canonical and (os.major(info.st_dev), os.minor(info.st_dev), info.st_ino) == key
                 and stamp(info) == context['snapshots'][str(canonical)], 'MAPPED_LIBRARY_IDENTITY_CHANGED:' + name)
        observed[name] = {'loaded': True, 'sha256': expected[name]['sha256'], 'device_major': key[0], 'device_minor': key[1], 'inode': key[2]}
    need(stamp(Path(context['manifest_path']).lstat()) == context['snapshots'][context['manifest_path']], 'MANIFEST_CHANGED')
    module = sys.modules.get('_rocm_sdk_core')
    need(module is not None and Path(module.__file__).parent == Path(proof['overlay_root']) / '_rocm_sdk_core', 'SDK_IGNORED_SELECTED_RUNTIME')
    return {'schema': 'heptatrader.resident-hip-runtime-proof.v1', 'phase': phase, 'pid': os.getpid(),
            'target': context['target'], 'manifest_sha256': context['manifest_sha256'],
            'candidate_sha256': proof['candidate_sha256'], 'libraries': observed,
            'queue_flags': context['queue_flags'], 'worker_admission_granted': False, 'extra_gpu_probe_performed': False}


def prove_before_publish(config):
    if config.get('hip_runtime') is None:
        need(_ACTIVE is None, 'PUBLICATION_RUNTIME_SELECTION_DROPPED')
        return None
    need(_ACTIVE is not None and _ACTIVE['manifest_sha256'] == config['hip_runtime']['sha256'], 'PUBLICATION_RUNTIME_NOT_BOUND')
    return prove(_ACTIVE, 'BEFORE_PUBLISH')


def bind_plan(config, plan):
    if config.get('hip_runtime') is None: return
    for name in (MODULE, MANIFEST):
        plan['evaluation_runtime_sha256'][name] = config['runtime_sha256'][name]
    plan['hip_runtime_manifest_sha256'] = config['hip_runtime']['sha256']


def check_plan(config, plan):
    selected = config.get('hip_runtime')
    need(plan.get('hip_runtime_manifest_sha256') == (selected['sha256'] if selected is not None else None), 'FROZEN_PLAN_RUNTIME_CHANGED')
