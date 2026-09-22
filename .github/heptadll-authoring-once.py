"""One-time, branch-scoped inventory authoring; removed before PR validation.

Generate the core inventory from the real Linux CMake model. Carry only the
new unconditional SDK-free targets/dependencies into the retained IB model;
this does not observe, build, or qualify the external IB SDK profile.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys

root = Path.cwd().resolve()
sys.path.insert(0, str(root / 'scripts'))
import verify_build_ownership as ownership

inventory_path = root / 'docs/build-targets.json'
inventory = json.loads(inventory_path.read_text())
old_core = copy.deepcopy(inventory['profiles']['core'])
cmake = root / 'CMakeLists.txt'
text = cmake.read_text()
needle = 'include(cmake/HeptaInstall.cmake)'
assert text.count(needle) == 1 and 'include(cmake/HeptaResearch.cmake)' not in text
cmake.write_text(text.replace(needle, 'include(cmake/HeptaResearch.cmake)\n\n' + needle))

catalog_path = root / 'docs/module-catalog.json'
catalog = json.loads(catalog_path.read_text())
assert not any(m['id'] == 'native-research' for m in catalog['modules'])
catalog['modules'].append({
    'id': 'native-research', 'status': 'EXPERIMENTAL',
    'document': 'docs/modules/native-research.md',
    'implementation': ['strategies/native_research'],
    'tests': ['tests/native_research_tests.cpp', 'tests/native_strategy_gateway_tests.cpp',
              'tests/native_strategy_client_link_tests.cpp'],
    'broker_mutation': 'NONE', 'production_authorized': False})
agent = next(m for m in catalog['modules'] if m['id'] == 'agent-entry')
for path in ('tests/native_strategy_gateway_tests.cpp', 'tests/native_strategy_client_link_tests.cpp'):
    assert path not in agent['tests']
    agent['tests'].append(path)
catalog_path.write_text(json.dumps(catalog, indent=2) + '\n')
subprocess.run([sys.executable, 'scripts/check_documentation.py', '--write-module-metadata', '--write-index'], check=True)

new_core = ownership.observe(root, 'core')
old = {t['name']: t for t in old_core['targets']}
new = {t['name']: t for t in new_core['targets']}
expected = {'hepta_research_core', 'hepta_strategy_intent_client',
            'hepta_native_research_tests', 'hepta_native_strategy_gateway_tests',
            'hepta_native_strategy_client_link_tests'}
assert set(new) - set(old) == expected, set(new) - set(old)
assert not set(old) - set(new), 'existing target removed'
ib = {t['name']: t for t in inventory['profiles']['ib']['targets']}
for name in expected:
    assert name not in ib
    ib[name] = copy.deepcopy(new[name])
for name in old:
    if old[name] == new[name]:
        continue
    assert name == 'hepta_core_test_binaries', 'unexpected existing target modification: ' + name
    old_body = {k: v for k, v in old[name].items() if k != 'dependencies'}
    new_body = {k: v for k, v in new[name].items() if k != 'dependencies'}
    assert old_body == new_body
    assert set(old[name]['dependencies']) <= set(new[name]['dependencies'])
    delta = set(new[name]['dependencies']) - set(old[name]['dependencies'])
    assert delta <= expected
    ib[name]['dependencies'] = sorted(set(ib[name]['dependencies']) | delta)
inventory['profiles']['core'] = new_core
inventory['profiles']['ib']['targets'] = sorted(ib.values(), key=lambda t: t['name'])
ownership.validate_inventory(root, inventory)
inventory_path.write_text(json.dumps(inventory, indent=2) + '\n')
ownership.verify(root, inventory, 'core')
subprocess.run([sys.executable, 'scripts/check_component_coverage.py'], check=True)
subprocess.run([sys.executable, 'scripts/check_documentation.py'], check=True)
subprocess.run(['git', 'diff', '--check'], check=True)
allowed = {'CMakeLists.txt', 'docs/build-targets.json', 'docs/module-catalog.json',
           'docs/index.md', 'docs/modules/agent-entry.md'}
changed = set(subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines())
assert changed <= allowed and 'docs/build-targets.json' in changed, changed
print('Generated exact core inventory and additive SDK-free IB inventory delta; no IB build claim.')
