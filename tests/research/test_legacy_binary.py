"""Independent ABI fixtures and real compiled-builder/replay integration.

ctypes is used ONLY to produce a native-layout test oracle; the production
reader uses explicit offsets and endian formats and never loads native data.
"""
from contextlib import contextmanager
import ctypes as C
from dataclasses import replace
from decimal import Decimal, localcontext
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hepta_research import legacy_binary as binary
from hepta_research.legacy import _source
from hepta_research.legacy_ticks import normalize_ticks, tick_report


class Level(C.Structure):
    _fields_ = [("price", C.c_double), ("volume", C.c_int32)]


class NativeDepth(C.Structure):
    # Independently declared in source-field order, NOT using parser offsets.
    _fields_ = [("exchange", C.c_char*11), ("day", C.c_char*9),
                ("action", C.c_char*9), ("clock", C.c_char*9),
                ("milliseconds", C.c_uint32), ("instrument", C.c_char*82),
                ("bids", Level*5), ("asks", Level*5),
                ("last", C.c_double), ("pre_settlement", C.c_double),
                ("pre_close", C.c_double), ("pre_interest", C.c_double),
                ("pre_delta", C.c_double), ("volume", C.c_int64),
                ("turnover", C.c_double), ("interest", C.c_double),
                ("opening", C.c_double), ("highest", C.c_double),
                ("lowest", C.c_double), ("close", C.c_double),
                ("settlement", C.c_double), ("upper", C.c_double),
                ("lower", C.c_double), ("delta", C.c_double),
                ("average", C.c_double)]


def record(*, price=10.0, clock="09:00:00", volume=10, day="20240102",
           action="20240102", instrument="TEST", fraction=0,
           turnover=100.0, interest=20.0):
    value = NativeDepth()
    value.exchange, value.day, value.action = b"TEST", day.encode(), action.encode()
    value.clock, value.milliseconds = clock.encode(), fraction
    value.instrument = instrument.encode()
    value.last, value.volume, value.turnover, value.interest = price, volume, turnover, interest
    return bytes(value)


def epoch(label):
    delta = datetime.fromisoformat(label).replace(tzinfo=timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return delta.days*86400000000+delta.seconds*1000000+delta.microseconds


class BinaryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.sessions = self.root/"ticks.bin", self.root/"sessions.csv"
        self.source.write_bytes(record())
        self.begin = epoch("2024-01-02T09:00:00")
        self.sessions.write_text(f"begin_us,end_us,trading_day\n{self.begin},{self.begin+3600000000},20240102\n")
        self.options = dict(layout=binary.LAYOUT, instrument="TEST", tick_size="0.2")

    def decode(self, **kwargs):
        return binary.decode_binary(self.source, **(self.options | kwargs))

    def normalize(self, **kwargs):
        return binary.normalize_binary(self.source, self.sessions,
                                       **(self.options | {"clock_zone": "UTC"} | kwargs))


class BinaryCase(BinaryFixture):
    def test_independent_native_layout(self):
        self.assertEqual(sys.byteorder, "little", "test fixture profile is explicitly little endian")
        self.assertEqual(C.sizeof(Level), 16)
        self.assertEqual(C.sizeof(NativeDepth), 424)
        offsets = {"exchange":0, "day":11, "action":20, "clock":29, "milliseconds":40,
                   "instrument":44, "bids":128, "asks":208, "last":288,
                   "volume":328, "turnover":336, "interest":344, "average":416}
        for name, value in offsets.items():
            self.assertEqual(getattr(NativeDepth,name).offset, value)
        self.assertEqual(self.decode().metadata["record_bytes"], C.sizeof(NativeDepth))

    def test_known_normalized_observation_and_hashes(self):
        got = self.normalize()
        self.assertEqual(got.ticks_csv, "instrument,trading_day,timestamp_us,sequence,price_ticks,cumulative_volume\n"
                         f"TEST,20240102,{self.begin},1,50,10\n")
        self.assertEqual(got.metadata["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(got.metadata["sessions_sha256"], hashlib.sha256(self.sessions.read_bytes()).hexdigest())
        self.assertEqual(got.metadata["normalized_ticks_sha256"], hashlib.sha256(got.ticks_csv.encode()).hexdigest())
        self.assertNotIn("raw_fields", got.metadata["source_fields"][0])
        self.assertEqual(got.metadata["source_fields"][0]["last_price_binary64_hex"], (10.0).hex())
        self.assertEqual(got.metadata["source_format"], binary.LAYOUT)

    def test_explicit_layout_only(self):
        for layout in (None, "auto", "ctp", "hepta-depth82-be-a8-v1", "hepta-depth82-le-a4-v1"):
            with self.subTest(layout=layout), self.assertRaises(ValueError):
                self.decode(layout=layout)

    def test_every_short_record_and_trailing_fragment(self):
        original = record()
        for length in range(424):
            with self.subTest(length=length):
                self.source.write_bytes(original[:length])
                with self.assertRaises(ValueError): self.decode()
        for length in (1, 40, 423):
            self.source.write_bytes(original+original[:length])
            with self.assertRaises(ValueError): self.decode()

    def test_count_and_size_bounds(self):
        for count in (0, True, -1, 100001, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError): self.decode(max_ticks=count)
        self.source.write_bytes(record()*2)
        with self.assertRaises(ValueError): self.decode(max_ticks=1)
        with self.source.open("wb") as output: output.truncate(64*1024*1024+1)
        with self.assertRaises(ValueError): self.decode()

    def test_regular_no_symlink_or_fifo(self):
        link = self.root/"link.bin"
        link.symlink_to(self.source)
        for path in (link, self.root):
            with self.assertRaises((ValueError, OSError)):
                binary.decode_binary(path, **self.options)
        fifo = self.root/"fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ValueError): binary.decode_binary(fifo, **self.options)

    def test_strings_and_clock(self):
        cases = [record(instrument="OTHER"), record(clock="25:00:00"), record(clock="9:00:00"),
                 record(day="20240230"), record(action="20240230"), record(action="00000000")]
        for offset,size in ((0,11),(11,9),(20,9),(29,9),(44,82)):
            bad = bytearray(record()); bad[offset:offset+size] = b"x"*size; cases.append(bytes(bad))
        for byte in (b"\xff", b",", b"\n", b'"'):
            bad = bytearray(record()); bad[0:1] = byte; cases.append(bytes(bad))
        for case in cases:
            self.source.write_bytes(case)
            with self.assertRaises(ValueError): self.decode()

    def test_padding_not_data_or_public_payload(self):
        original = self.normalize()
        changed = bytearray(record())
        changed[38:40] = b"XY"
        changed[126:128] = b"XY"
        changed[49:69] = b"NEVER_EXPORT_PADDING"  # after InstrumentID's NUL
        for start in (128,208):
            for i in range(5): changed[start+i*16+12:start+i*16+16] = b"XXXX"
        self.source.write_bytes(changed)
        result = self.normalize()
        self.assertEqual(result.ticks_csv, original.ticks_csv)
        self.assertNotEqual(result.metadata["source_sha256"], original.metadata["source_sha256"])
        self.assertNotIn("NEVER_EXPORT_PADDING", repr(result.metadata))

    def test_no_action_day_guess(self):
        self.source.write_bytes(record(action=""))
        with self.assertRaises(ValueError): self.normalize()
        result = self.normalize(action_day="20240102")
        self.assertEqual(result.metadata["source_fields"][0]["action_day_source"], "external")
        self.assertIsNone(result.metadata["source_fields"][0]["recorded_action_day"])

    def test_external_dates_validate_not_override(self):
        self.normalize(action_day="20240102")
        with self.assertRaises(ValueError): self.normalize(action_day="20240103")
        dates = self.root/"dates.csv"
        dates.write_text("row,action_day\n1,20240102\n")
        got = self.normalize(action_days_path=dates)
        self.assertEqual(got.metadata["action_days_sha256"], hashlib.sha256(dates.read_bytes()).hexdigest())
        with self.assertRaises(ValueError): self.normalize(action_day="20240102", action_days_path=dates)

    def test_action_map_exact_record_coverage(self):
        dates = self.root/"dates.csv"
        for text in ("row,action_day\n", "row,action_day\n2,20240102\n",
                     "row,action_day\n1,20240102\n2,20240102\n"):
            dates.write_text(text)
            with self.assertRaises(ValueError): self.normalize(action_days_path=dates)
        self.source.write_bytes(record(action="")*2)
        dates.write_text("row,action_day\n1,20240102\n")
        with self.assertRaises(ValueError): self.normalize(action_days_path=dates)

    def test_night_session_midnight_and_trading_day(self):
        start = epoch("2024-01-01T15:59:59")
        self.sessions.write_text(f"begin_us,end_us,trading_day\n{start},{start+2000000},20240102\n")
        self.source.write_bytes(record(clock="23:59:59", action="20240101")+
                                record(clock="00:00:00", action="20240102", volume=12))
        result = self.normalize(clock_zone="Asia/Shanghai")
        self.assertIn(f"TEST,20240102,{start},1,50,10", result.ticks_csv)
        self.assertIn(f"TEST,20240102,{start+1000000},2,50,12", result.ticks_csv)
        self.source.write_bytes(record(clock="23:59:59", action="")+record(clock="00:00:00",action="",volume=12))
        dates = self.root/"dates.csv"; dates.write_text("row,action_day\n1,20240101\n2,20240102\n")
        self.assertEqual(self.normalize(clock_zone="Asia/Shanghai", action_days_path=dates).ticks_csv, result.ticks_csv)

    def test_binary_fraction_preserved(self):
        self.source.write_bytes(record(fraction=999))
        self.assertIn(f",{self.begin+999000},1,", self.normalize().ticks_csv)
        for fraction in (1000, 2**32-1):
            self.source.write_bytes(record(fraction=fraction))
            with self.assertRaises(ValueError): self.decode()

    def test_cumulative_volume_signed_and_ordered(self):
        self.source.write_bytes(record(volume=-1))
        with self.assertRaises(ValueError): self.decode()
        self.source.write_bytes(record()+record(clock="09:01:00",volume=9))
        with self.assertRaises(ValueError): self.normalize()
        self.source.write_bytes(record(volume=2**63-1))
        self.assertIn(",9223372036854775807\n", self.normalize().ticks_csv)

    def test_duplicate_observations_retained(self):
        self.source.write_bytes(record()*2)
        lines = self.normalize().ticks_csv.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1].split(",")[3], "1")
        self.assertEqual(lines[2].split(",")[3], "2")
        self.assertEqual(lines[1].split(",")[-1], lines[2].split(",")[-1])

    def test_session_time_and_day_failures(self):
        for payload in (record(clock="08:59:59"), record(day="20240103"),
                        record(clock="09:01:00")+record(clock="09:00:00",volume=12)):
            self.source.write_bytes(payload)
            with self.assertRaises(ValueError): self.normalize()
        self.source.write_bytes(record(day="20241103",action="20241103",clock="01:30:00"))
        with self.assertRaisesRegex(ValueError,"ambiguous"):
            self.normalize(clock_zone="America/New_York")
        self.source.write_bytes(record(day="20240310",action="20240310",clock="02:30:00"))
        with self.assertRaisesRegex(ValueError,"nonexistent"):
            self.normalize(clock_zone="America/New_York")

    def test_binary_price_exact_round_trip(self):
        for scale in ("1", "0.2", "0.005", "0.000001"):
            step = Decimal(scale)
            for ticks in range(-1000,1001):
                exact = ticks*step
                text, result = binary._price(float(exact), step)
                self.assertEqual(result, ticks)
                self.assertEqual(Decimal(text), exact)
        for price in (10.1, math.nextafter(10.0, math.inf), float("nan"),
                      float("inf"), -float("inf"), sys.float_info.max, 1e19):
            self.source.write_bytes(record(price=price))
            with self.assertRaises(ValueError): self.decode()

    def test_grid_bounds_and_ambiguous_binary64(self):
        for scale in ("0", "-1", "nan", "1e-19", True):
            with self.assertRaises(ValueError): self.decode(tick_size=scale)
        self.source.write_bytes(record(price=1e18))
        with self.assertRaises(ValueError): self.decode(tick_size="1")
        self.source.write_bytes(record(price=10.0))
        with self.assertRaises(ValueError): self.decode(tick_size="1e-18")

    def test_negative_and_signed_zero_future_price(self):
        for value,ticks in ((-0.6,-3),(-0.0,0)):
            self.source.write_bytes(record(price=value))
            result = self.normalize()
            self.assertIn(f",1,{ticks},10\n", result.ticks_csv)
            self.assertEqual(result.metadata["source_fields"][0]["last_price_binary64_hex"], value.hex())

    def test_auxiliary_nonfinite_and_interest(self):
        for key,value in (("interest",-1.0),("interest",float("nan")),
                          ("turnover",float("inf")),("interest",1e-19)):
            self.source.write_bytes(record(**{key:value}))
            with self.assertRaises(ValueError): self.decode()

    def test_low_decimal_context_does_not_change_price_grid(self):
        with localcontext() as ctx:
            ctx.prec = 3
            self.source.write_bytes(record(price=12345.6))
            got = self.decode().metadata["binary_records"][0]["price_ticks"]
        self.assertEqual(got,61728)

    def test_source_mutation_detected(self):
        source_path = self.source
        @contextmanager
        def changing(path):
            with _source(path) as (stream,digest):
                class Reader:
                    changed = False
                    def fileno(self): return stream.fileno()
                    def read(self,count):
                        result = stream.read(count)
                        if not self.changed:
                            self.changed = True
                            with source_path.open("ab") as out: out.write(record())
                        return result
                yield Reader(),digest
        with patch.object(binary,"_source",changing), self.assertRaises(ValueError): self.decode()


class BinaryPipeline(BinaryFixture):
    # Keep end-to-end tests in the same discovery module. CMake supplies the
    # real compiled target; missing executable is a failure, not a fake pass.
    def setUp(self):
        super().setUp()
        self.executable = Path(os.environ["HEPTA_RESEARCH_BARS"])
        self.assertTrue(self.executable.is_file())
        prices = (10,12,13,11,9,14)
        self.source.write_bytes(b"".join(record(price=p,clock=f"09:0{i}:00",volume=10+i) for i,p in enumerate(prices)))
        self.report_options = self.options | dict(clock_zone="UTC", bars_executable=self.executable,
            period_us=60000000, first_volume="baseline", capital="1000", quantity="2", fast=1, slow=2,
            slippage="0.5", fee_per_unit="0.25")

    def test_real_pipeline_known_fills_and_equity(self):
        report = binary.binary_report(self.source,self.sessions,**self.report_options)
        self.assertEqual([f["delta"] for f in report["fills"]],[Decimal(2),Decimal(-4)])
        self.assertEqual([f["price"] for f in report["fills"]],[Decimal("13.5"),Decimal("8.5")])
        self.assertEqual([v["value"] for v in report["equity"]],list(map(Decimal,["1000","1000","998.5","994.5","987.5","977.5"])))
        self.assertEqual(report["fees"],Decimal("1.5"))
        self.assertEqual(report["position"],Decimal(-2))
        self.assertIsNone(report["pending_target"])
        self.assertFalse(report["equity"][-1]["complete"])
        self.assertFalse(report["assumptions"]["broker_authorized"])

    def test_agrees_with_independently_written_csv_path(self):
        csv = self.root/"independent.csv"
        rows = []
        for i,price in enumerate((10,12,13,11,9,14)):
            f=["0"]*35
            f[1:9]=["IMMSG","TEST","20240102","20240102",f"09:0{i}:00","0",str(price),str(10+i)]
            f[10],f[32]="100","20"
            rows.append(",".join(f)+"\n")
        csv.write_text("".join(rows))
        normalized = normalize_ticks(csv,self.sessions,layout="immsg35",instrument="TEST",clock_zone="UTC",tick_size="0.2")
        self.assertEqual(self.normalize().ticks_csv,normalized.ticks_csv)
        old=tick_report(csv,self.sessions,**(self.report_options|{"layout":"immsg35"}))
        new=binary.binary_report(self.source,self.sessions,**self.report_options)
        self.assertEqual({k:v for k,v in old.items() if k!="input"},{k:v for k,v in new.items() if k!="input"})
        self.assertEqual(old["input"]["bars_sha256"],new["input"]["bars_sha256"])

    def test_builder_failure_and_timeout_reject(self):
        with patch("hepta_research.legacy_ticks.subprocess.run",return_value=subprocess.CompletedProcess([],2)):
            with self.assertRaises(ValueError): binary.binary_report(self.source,self.sessions,**self.report_options)
        with patch("hepta_research.legacy_ticks.subprocess.run",side_effect=subprocess.TimeoutExpired("builder",1)):
            with self.assertRaises(subprocess.TimeoutExpired): binary.binary_report(self.source,self.sessions,**self.report_options)

    def args(self,output):
        return ["--ticks",str(self.source),"--sessions",str(self.sessions),"--output",str(output),
                "--layout",binary.LAYOUT,"--instrument","TEST","--clock-zone","UTC","--tick-size","0.2",
                "--period-us","60000000","--first-volume","baseline","--capital","1000","--quantity","2",
                "--fast","1","--slow","2","--slippage","0.5","--fee-per-unit","0.25",
                "--bars-executable",str(self.executable)]

    def test_cli_publication_and_late_error_preserves_report(self):
        output=self.root/"report.json"
        self.assertEqual(binary.main(self.args(output)),0)
        original=output.read_bytes()
        self.assertEqual(json.loads(original)["equity"][-1]["value"],"977.50")
        with self.source.open("ab") as out: out.write(record(price=float("nan"),clock="09:06:00",volume=99))
        with patch("sys.stderr",io.StringIO()): self.assertEqual(binary.main(self.args(output)),2)
        self.assertEqual(output.read_bytes(),original)

    def test_output_alias_rejected(self):
        original=self.source.read_bytes()
        with patch("sys.stderr",io.StringIO()): self.assertEqual(binary.main(self.args(self.source)),2)
        self.assertEqual(self.source.read_bytes(),original)
        alias=self.root/"alias.json"; os.link(self.source,alias)
        with patch("sys.stderr",io.StringIO()): self.assertEqual(binary.main(self.args(alias)),2)
        self.assertEqual(self.source.read_bytes(),original)


if __name__ == "__main__":
    unittest.main()
