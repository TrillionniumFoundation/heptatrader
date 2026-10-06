#!/usr/bin/env python3
"""Standard-library test runner with JUnit and source-tree receipt."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tests'))
sys.path.insert(0, str(ROOT/'scripts'))

class RecordedResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.monotonic()
        self.record = {'id': test.id(), 'status': 'running'}
        self.records.append(self.record)
        super().startTest(test)
    def stopTest(self, test):
        self.record['seconds'] = time.monotonic()-self.started
        super().stopTest(test)
    def addSuccess(self, test):
        self.record['status'] = 'passed'
        super().addSuccess(test)
    def addFailure(self, test, err):
        self.record.update(status='failure', detail=self._exc_info_to_string(err,test))
        super().addFailure(test,err)
    def addError(self, test, err):
        self.record.update(status='error', detail=self._exc_info_to_string(err,test))
        super().addError(test,err)
    def addSkip(self, test, reason):
        self.record.update(status='skipped',detail=reason)
        super().addSkip(test,reason)
    def __init__(self,*args,**kwargs):
        self.records=[]
        super().__init__(*args,**kwargs)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',default='local')
    parser.add_argument('--report-dir',type=Path,default=ROOT/'reports')
    parser.add_argument('--environment',default='LOCAL_TEST_RUNTIME_NOT_BROKER')
    args=parser.parse_args()
    if not args.prefix.replace('-','').isalnum():
        parser.error('alphanumeric report prefix required')
    reports=args.report_dir;reports.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    started=time.monotonic()
    with (reports/(args.prefix+'-tests.log')).open('w',encoding='utf-8') as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=RecordedResult).run(suite)
    elapsed=time.monotonic()-started
    doc=ET.Element('testsuite',name='heptatrader-cost-upgrade-isolated',tests=str(result.testsRun),
                   failures=str(len(result.failures)),errors=str(len(result.errors)),skipped=str(len(result.skipped)),time=str(elapsed))
    for record in result.records:
        klass,name=record['id'].rsplit('.',1)
        node=ET.SubElement(doc,'testcase',classname=klass,name=name,time=str(record['seconds']))
        if record['status']!='passed':
            child=ET.SubElement(node,record['status']);child.text=record.get('detail','')
    ET.ElementTree(doc).write(reports/(args.prefix+'-junit.xml'),encoding='utf-8',xml_declaration=True)
    hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
            for root in (ROOT/'scripts',ROOT/'tests') for path in sorted(root.glob('*.py'))}
    receipt={'schema':'heptatrader.cost-upgrade-test-receipt.v1',
             'completed_at_utc':datetime.now(timezone.utc).isoformat(),
             'environment':args.environment,'python':platform.python_version(),
             'optimization_level':sys.flags.optimize,'test_cases':result.testsRun,
             'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
             'elapsed_seconds':elapsed,'result':'PASS' if result.wasSuccessful() and not result.skipped else 'FAIL',
             'production_deployed':False,'real_broker_orders_sent':0,'actual_model_calls':0,
             'hosted_ci_pass_claimed':False,'full_repository_regression_run':False,
             'source_sha256':hashes}
    (reports/(args.prefix+'-receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({key:value for key,value in receipt.items() if key!='source_sha256'},indent=2))
    return 0 if result.wasSuccessful() and not result.skipped else 1

if __name__=='__main__':raise SystemExit(main())
