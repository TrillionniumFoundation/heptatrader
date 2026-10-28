"""Discover every isolated historical test in the default CI core lane."""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
def load_tests(_loader,_suite,_pattern):
    paths=[ROOT/'research',ROOT/'research/neuron_learning',ROOT/'scripts']
    for path in paths:
        if str(path) not in sys.path:sys.path.insert(0,str(path))
    suite=unittest.TestSuite()
    files=sorted((ROOT/'research/historical_training/tests').glob('test_*.py'))
    if not files:raise RuntimeError('HISTORICAL_TEST_DISCOVERY_EMPTY')
    for path in files:
        name='hepta_historical_ci_'+path.stem
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module
        try:
            spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        except Exception as error:
            # A broken file must fail without hiding the remaining fixtures.
            sys.modules.pop(name,None)
            def failed_import(error=error):
                raise error
            failed_import.__name__=name
            suite.addTest(unittest.FunctionTestCase(failed_import))
    if not suite.countTestCases():raise RuntimeError('HISTORICAL_TEST_DISCOVERY_EMPTY')
    return suite
