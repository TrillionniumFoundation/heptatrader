"""Reproduce the Pocket4 2026-11-23 quote diagnostic repair in a temporary directory."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ORIGINAL = {
    '__init__.py': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'feed_health.py': '32d9f7d9908128a928fac7cfa613d48e50c8fc8ad9a58ee338a208a1bfdc7392',
    'market_session.py': '51d31c2737d7de4c42cbb1b0fab2924e6419a8205732210d3e22c0bfd8135649',
    'shadow_source.py': 'f91d3d81b517d53958103da79a96fc9c5fd86916bc0718e1199526fabcc3c52f',
}
TESTS = {
    'test_feed_health.py': 'da0f50e62ac0256aff56598ff67f9dfed24fbddbaa7bed62f81bde48db997a72',
    'test_native_quote_diagnostics.py': '4e9396745eb2327229bb7bea479f97e4b760a3cee0855167e1eeba19c08b86ad',
    'test_original_quote_transport_faults.py': '7e4960be03d5d26edb8f2c96fa947c85b3eeb889106d8f4b00533d5b5c04af9b',
}
BASE = 'cae04476a0fb4a20763e391c84f18640ab1395428e68079884e6650f29c463a3'
REVIEWED = '29da7434684c75af225e257bab5929042a10291524cbfd9180b51cfabd1e0c7d'
PATCH = '0ed9e72339e36f6800b053a6f3564300aa7d1f6aeee8df07cfdefeed3b7a68c7'
REPAIRED = 'd129a9520bbd9779e51d1174668e9dc949aa4ee4f48cd2e724c926596c3621c6'


def checked(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'Input fingerprint mismatch at {path}')
    return raw


def replay(original, base, reviewed):
    before = checked(base, BASE).decode().splitlines(keepends=True)
    after = checked(reviewed / 'shadow_source.py', REVIEWED).decode().splitlines(keepends=True)
    patch = ''.join(difflib.unified_diff(before, after, 'source.py', 'source.py')).encode()
    if hashlib.sha256(patch).hexdigest() != PATCH:
        raise ValueError('Reviewed two-hunk patch fingerprint mismatch')
    with TemporaryDirectory() as name:
        root = Path(name)
        package = root / 'laya_cell'
        tests = root / 'tests'
        package.mkdir()
        tests.mkdir()
        for filename, digest in ORIGINAL.items():
            (package / filename).write_bytes(checked(original / filename, digest))
        for filename, digest in TESTS.items():
            (tests / filename).write_bytes(checked(reviewed / 'tests' / filename, digest))
        target = package / 'shadow_source.py'
        subprocess.run(['patch', '--batch', '--fuzz=0', str(target)],
                       input=patch, cwd=root, stdout=subprocess.DEVNULL, check=True)
        checked(target, REPAIRED)
        env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                                 '-s', str(tests)], cwd=root, env=env,
                                capture_output=True, text=True)
        if result.returncode or 'Ran 52 tests' not in result.stderr:
            raise RuntimeError(result.stderr)
    return {'patch_sha256': PATCH, 'repaired_sha256': REPAIRED, 'synthetic_tests_passed': 52}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_package', type=Path, help='Original deployed learn/laya_cell directory')
    parser.add_argument('base_module', type=Path, help='Base ffec655e shadow_source.py')
    parser.add_argument('reviewed_package', type=Path, help='Review ab60609f research/neuron_learning/laya_cell')
    args = parser.parse_args()
    print(json.dumps(replay(args.original_package, args.base_module, args.reviewed_package), sort_keys=True))
