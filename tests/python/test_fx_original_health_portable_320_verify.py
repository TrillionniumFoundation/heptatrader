"""Synthetic portable archive checks for one frozen prefix specification."""
from __future__ import annotations
import copy
import tempfile
import unittest
from train_v2 import fx_original_health_portable_320_verify as _api
from train_v2.fx_original_health_portable_320_verify import (
    PIN, Fail, archive, pack, parse, read_private, sha, verify,
)
from portable_archive_fixtures import PortableArchiveCases, archive_fixture


def fixture(t):
    return archive_fixture(t,320,_api)


class PortableTests(PortableArchiveCases,unittest.TestCase):
    count=320
    api=_api
    fixture=staticmethod(fixture)

    def test_new_second_half_last_original_status_tamper_refused(self):
        with tempfile.TemporaryDirectory() as t:
            data, report, pins, _ = fixture(t)
            damaged = dict(data)
            name = "cycle-319-status.json"
            damaged[name] = damaged[name].replace(b'"USABLE"', b'"PATH_GAP"', 1)
            self.assertNotEqual(data[name], damaged[name])
            with self.assertRaisesRegex(Fail, "ORIGINAL_RAW_SOURCE_NOT_BOUND"):
                verify(damaged, report, pins)

    def test_new_second_half_original_receipt_tamper_refused(self):
        with tempfile.TemporaryDirectory() as t:
            data, report, pins, _ = fixture(t)
            damaged = dict(data)
            name = "cycle-160-receipt.json"
            damaged[name] = damaged[name].replace(
                b'"source_ready":true', b'"source_ready":false', 1)
            self.assertNotEqual(data[name], damaged[name])
            with self.assertRaises(Fail):
                verify(damaged, report, pins)

    def test_partial_160_source_report_cannot_masquerade_as_320(self):
        with tempfile.TemporaryDirectory() as t:
            data, report, pins, _ = fixture(t)
            fraudulent = copy.deepcopy(report[1])
            fraudulent["contiguous_prefix_cycles_checked"] = 160
            fraudulent["future_cycles_NOT_verified"] = 320
            raw = pack(fraudulent) + b"\n"
            with self.assertRaisesRegex(Fail, "OLD_AUDIT_CONTENT_CHANGED"):
                verify(data, (raw, fraudulent), pins)

if __name__=="__main__":unittest.main()
