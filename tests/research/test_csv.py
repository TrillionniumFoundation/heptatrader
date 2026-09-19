import csv
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class CsvTests(unittest.TestCase):
    def setUp(self):
        self.bin=os.environ.get("HEPTA_RESEARCH_BARS")
        if not self.bin: self.skipTest("CMake supplies the built bars executable")
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def run_csv(self,rows,policy="baseline",period=50):
        (self.root/"sessions").write_text("begin_us,end_us,trading_day\n0,100,20260921\n200,300,20260921\n")
        (self.root/"ticks").write_text("instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume\n"+rows)
        return subprocess.run([self.bin,str(self.root/"ticks"),str(self.root/"sessions"),str(period),policy],capture_output=True,text=True)
    def test_streamed_bars(self):
        p=self.run_csv("TEST,20260921,0,1,10,100\nTEST,20260921,49,2,12,105\nTEST,20260921,50,3,9,107\n")
        self.assertEqual(p.returncode,0,p.stderr)
        r=list(csv.DictReader(io.StringIO(p.stdout)))
        self.assertEqual((r[0]["high"],r[0]["volume"],r[0]["complete"]),("12","5","1"))
        self.assertEqual(r[1]["complete"],"0")
    def test_include_cumulative_explicit(self):
        p=self.run_csv("TEST,20260921,0,1,10,100\n","include")
        self.assertEqual(list(csv.DictReader(io.StringIO(p.stdout)))[0]["volume"],"100")
    def test_invalid_csv_and_session_data(self):
        for row in ("", "TEST,20260921,100,1,10,100\n", "TEST,20260921,0,-1,10,100\n",
                    "TEST,20260921,0,1,nan,100\n", "TEST,20260921,0,1,10,100,extra\n",
                    "TEST,20260230,0,1,10,100\n", "X"*4097+"\n"):
            with self.subTest(row=row[:40]):self.assertNotEqual(self.run_csv(row).returncode,0)
    def test_sequence_conflict_not_silently_deduplicated(self):
        p=self.run_csv("TEST,20260921,0,1,10,100\nTEST,20260921,0,1,11,100\n")
        self.assertNotEqual(p.returncode,0)
    def test_daily_breaks_not_new_days(self):
        p=self.run_csv("TEST,20260921,0,1,10,100\nTEST,20260921,200,2,20,110\n",period=0)
        rows=list(csv.DictReader(io.StringIO(p.stdout)))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["end_us"],"300")
