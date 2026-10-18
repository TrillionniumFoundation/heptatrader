"""Synthetic portable archive checks for one frozen prefix specification."""
from __future__ import annotations
import copy
import tempfile
import unittest
from train_v2 import fx_original_health_portable_prefix_verify as _api
from train_v2.fx_original_health_portable_prefix_verify import (
    PIN, Fail, archive, pack, parse, read_private, sha, verify,
)
from portable_archive_fixtures import PortableArchiveCases, archive_fixture


def fixture(t):
    return archive_fixture(t,32,_api)


class PortableTests(PortableArchiveCases,unittest.TestCase):
    count=32
    api=_api
    fixture=staticmethod(fixture)

if __name__=="__main__":unittest.main()
