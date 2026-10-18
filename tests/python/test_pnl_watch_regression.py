"""Behavioral regressions for idle OMS reuse, accounting and fresh valuation."""
from __future__ import annotations
import copy
import errno
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

import hepta_pnl_accounting as accounting
import hepta_pnl_report as report
from hepta_pnl_accounting import EvidenceError
from hepta_pnl_report import IncrementalOMSParser, read_private
from hepta_pnl_watch import StableJournalBytes
from test_pnl_accounting import period, basic, observation, START, NOW


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux inotify")
class StableJournalBytesRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hepta-pnl-watch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "oms-journal.jsonl"
        self.path.write_bytes(b"first-record\n")
        self.path.chmod(0o600)
        self.clock_ms = 1_000_000
        self.reader = mock.Mock(wraps=read_private)
        self.cache = StableJournalBytes(self.reader, lambda: self.clock_ms)
        self.addCleanup(self.cache.close)
        self.limit = 1_000_000

    def prime(self):
        value = self.cache.read(self.path, self.limit)
        if self.cache._watch.fd < 0:self.skipTest("inotify unavailable on this runner")
        return value

    def reset_source(self):
        self.cache.close()
        self.path.write_bytes(b"first-record\n")
        self.path.chmod(0o600)
        self.reader.reset_mock()
        self.cache = StableJournalBytes(self.reader, lambda: self.clock_ms)
        self.addCleanup(self.cache.close)
        self.prime()

    def change(self, mode):
        old = self.path.stat()
        if mode == "append":
            with self.path.open("ab") as stream:stream.write(b"second-record\n")
        elif mode == "truncate":
            self.path.write_bytes(b"x\n")
        elif mode == "replace":
            replacement = self.root / "replacement.jsonl"
            replacement.write_bytes(b"replacement\n")
            replacement.chmod(0o600)
            os.replace(replacement, self.path)
        elif mode == "same_size_restored_mtime":
            self.path.write_bytes(b"other-record\n")
            self.assertEqual(self.path.stat().st_size, old.st_size)
            os.utime(self.path, ns=(old.st_atime_ns, old.st_mtime_ns))
            self.assertEqual(self.path.stat().st_mtime_ns, old.st_mtime_ns)
        else:raise AssertionError(mode)
        return self.path.read_bytes()

    def test_idle_read_reuses_verified_bytes_without_private_read(self):
        first, first_fp = self.prime()
        second, second_fp = self.cache.read(self.path, self.limit)
        self.assertIs(first, second)
        self.assertEqual(first_fp, second_fp)
        self.assertEqual(self.reader.call_count, 1)
        self.assertEqual(self.cache.full_reads, 1)
        self.assertEqual(self.cache.hits, 1)

    def test_append_truncate_replace_and_equal_size_rewrite_force_fresh_read(self):
        for mode in ("append", "truncate", "replace", "same_size_restored_mtime"):
            with self.subTest(mode=mode):
                self.reset_source()
                expected = self.change(mode)
                actual, _ = self.cache.read(self.path, self.limit)
                self.assertEqual(actual, expected)
                self.assertEqual(self.reader.call_count, 2)
                self.cache.read(self.path, self.limit)
                self.assertEqual(self.reader.call_count, 2)

    def test_verify_rejects_each_mid_collection_mutation(self):
        for mode in ("append", "truncate", "replace", "same_size_restored_mtime"):
            with self.subTest(mode=mode):
                self.reset_source()
                expected = self.change(mode)
                with self.assertRaisesRegex(EvidenceError, "PNL_COLLECTION_LEDGER_OR_PERIOD_CHANGED"):
                    self.cache.verify(self.path)
                actual, _ = self.cache.read(self.path, self.limit)
                self.assertEqual(actual, expected)
                self.assertEqual(self.reader.call_count, 2)

    def test_full_read_rejects_mutation_between_reader_and_verification(self):
        def racing_reader(*args, **kwargs):
            result = read_private(*args, **kwargs)
            with self.path.open("ab") as stream:stream.write(b"changed-during-read\n")
            return result
        cache = StableJournalBytes(racing_reader, lambda: self.clock_ms)
        self.addCleanup(cache.close)
        with self.assertRaisesRegex(EvidenceError, "PNL_JOURNAL_CHANGED_DURING_READ"):
            cache.read(self.path, self.limit)

    def test_unrelated_parent_basename_does_not_invalidate(self):
        first = self.prime()
        unrelated = self.root / "observer-heartbeat.json"
        unrelated.write_bytes(b"one\n");unrelated.write_bytes(b"two\n")
        other = self.root / "unrelated-renamed.json"
        os.replace(unrelated, other);other.unlink()
        second = self.cache.read(self.path, self.limit)
        self.cache.verify(self.path)
        self.assertEqual(second, first)
        self.assertEqual(self.reader.call_count, 1)

    def test_closed_watch_forces_full_read_before_reuse(self):
        self.prime();self.cache._watch.close()
        self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 2)
        self.assertGreaterEqual(self.cache._watch.fd, 0)
        self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 2)

    def test_queue_overflow_forces_full_read(self):
        self.prime();watched_fd = self.cache._watch.fd
        real_read = os.read;injected = False
        def read_with_overflow(fd, length):
            nonlocal injected
            if fd == watched_fd and not injected:
                injected = True
                return struct.pack("iIII", -1, 0x00004000, 0, 0)
            return real_read(fd, length)
        with mock.patch("hepta_pnl_watch.os.read", side_effect=read_with_overflow):
            self.cache.read(self.path, self.limit)
        self.assertTrue(injected);self.assertEqual(self.reader.call_count, 2)

    def test_unusable_watch_fd_forces_full_read(self):
        self.prime();watched_fd = self.cache._watch.fd
        real_read = os.read;injected = False
        def read_with_lost_fd(fd, length):
            nonlocal injected
            if fd == watched_fd and not injected:
                injected = True
                raise OSError(errno.EBADF, "watch descriptor lost")
            return real_read(fd, length)
        with mock.patch("hepta_pnl_watch.os.read", side_effect=read_with_lost_fd):
            self.cache.read(self.path, self.limit)
        self.assertTrue(injected);self.assertEqual(self.reader.call_count, 2)

    def test_unavailable_watch_uses_full_private_reads_and_still_verifies(self):
        with mock.patch.object(type(self.cache._watch), "arm", return_value=None):
            first = self.cache.read(self.path, self.limit)
            self.assertLess(self.cache._watch.fd, 0)
            self.cache.verify(self.path)
            second = self.cache.read(self.path, self.limit)
            self.cache.verify(self.path)
        self.assertEqual(first, second)
        self.assertEqual(self.reader.call_count, 2)
        self.assertEqual(self.cache.hits, 0)

    def test_periodic_reverification_occurs_at_sixty_seconds(self):
        self.prime();self.clock_ms += 59_999
        self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 1)
        self.clock_ms += 1;self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 2)

    def test_clock_rollback_after_a_hit_forces_full_read(self):
        self.prime();self.clock_ms += 100
        self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 1)
        self.clock_ms -= 1;self.cache.read(self.path, self.limit)
        self.assertEqual(self.reader.call_count, 2)

    def test_cached_bytes_cannot_bypass_a_tighter_read_limit(self):
        self.prime()
        with self.assertRaisesRegex(EvidenceError, "UNSAFE_OR_OVERSIZE_INPUT"):
            self.cache.read(self.path, 1)


def oms_bytes(events):
    return b"".join(json.dumps(row, separators=(",", ":"), default=str).encode()+b"\n" for row in events)


class LedgerReuseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.parser = IncrementalOMSParser()
        self.addCleanup(self.parser.close)
        self.events = self.parser.parse(oms_bytes([{"ts_ms": START + 1}]))
        self.period = period()

    @staticmethod
    def ledger(events, selected_period, instrument, at_ms):
        return {"instrument": instrument, "fills": [{"quantity": 1}], "at_ms": at_ms}

    def test_unchanged_journal_reuses_each_instrument_independently(self):
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            btc = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            fx = self.parser.replay_ledger(self.events, self.period, "EUR.USD", NOW)
            self.assertEqual(self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+1), btc)
            self.assertEqual(self.parser.replay_ledger(self.events, self.period, "EUR.USD", NOW+1), fx)
            self.assertEqual(raw.call_count, 2)
            self.assertEqual(btc["instrument"], "BTC.USD")
            self.assertEqual(fx["instrument"], "EUR.USD")

    def test_returned_ledger_mutation_does_not_change_cached_ledger(self):
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            first = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            first["fills"][0]["quantity"] = 999;first["fills"].append({"quantity": 888})
            second = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+1)
            self.assertEqual(second["fills"], [{"quantity": 1}])
            self.assertEqual(raw.call_count, 1)
            second["fills"][0]["quantity"] = -1
            third = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+2)
            self.assertEqual(third["fills"], [{"quantity": 1}])

    def test_equivalent_period_key_order_does_not_repeat_replay(self):
        reordered = dict(reversed(list(self.period.items())))
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            first = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            second = self.parser.replay_ledger(self.events, reordered, "BTC.USD", NOW+1)
            self.assertEqual(second, first);self.assertEqual(raw.call_count, 1)

    def test_changed_period_invalidates_all_instrument_ledgers(self):
        revised = copy.deepcopy(self.period);revised["started_at_ms"] = START+1
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            for instrument in ("EUR.USD", "BTC.USD"):
                self.parser.replay_ledger(self.events, self.period, instrument, NOW)
            for instrument in ("EUR.USD", "BTC.USD"):
                self.parser.replay_ledger(self.events, revised, instrument, NOW+1)
            self.assertEqual(raw.call_count, 4)

    def test_asof_clock_rollback_after_a_hit_does_not_reuse_later_ledger(self):
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+10)
            rolled_back = self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW+9)
            self.assertEqual(raw.call_count, 2);self.assertEqual(rolled_back["at_ms"], NOW+9)

    def test_future_event_replays_until_timestamp_is_reached(self):
        events = self.parser.parse(oms_bytes([{"ts_ms": NOW+1000}]))
        with mock.patch.object(report, "replay_ledger", side_effect=self.ledger) as raw:
            for at_ms in (NOW, NOW+500, NOW+999):
                value = self.parser.replay_ledger(events, self.period, "BTC.USD", at_ms)
                self.assertEqual(value["at_ms"], at_ms)
            self.assertEqual(raw.call_count, 3)
            matured = self.parser.replay_ledger(events, self.period, "BTC.USD", NOW+1000)
            self.assertEqual(raw.call_count, 4)
            self.assertEqual(self.parser.replay_ledger(events, self.period, "BTC.USD", NOW+1001), matured)
            self.assertEqual(raw.call_count, 4)


class PnlValuationReuseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.parser = IncrementalOMSParser()
        self.addCleanup(self.parser.close)
        self.period = period()
        self.events = self.parser.parse(oms_bytes(basic()))

    def test_reused_ledger_matches_full_replay_with_fresh_valuation(self):
        observations = (observation(bid=90, ask=92), observation(bid=95, ask=97))
        reports = []
        with mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
            for native in observations:
                expected = accounting.instrument_report(self.events, self.period, "BTC.USD", native, NOW)
                actual = accounting.instrument_report(self.events, self.period, "BTC.USD", native, NOW,
                                                       ledger_provider=self.parser.replay_ledger)
                self.assertEqual(actual, expected);reports.append(actual)
            self.assertEqual(raw.call_count, 1)
        self.assertNotEqual(reports[0], reports[1])

    def test_appended_verified_journal_invalidates_replay_scope(self):
        original = oms_bytes(basic())
        with mock.patch.object(report, "replay_ledger", side_effect=LedgerReuseRegressionTests.ledger) as raw:
            self.parser.replay_ledger(self.events, self.period, "BTC.USD", NOW)
            changed = self.parser.parse(original+oms_bytes([{"ts_ms": START+2}]))
            self.parser.replay_ledger(changed, self.period, "BTC.USD", NOW)
            self.assertEqual(raw.call_count, 2)

    def test_non_parser_event_identity_cannot_reuse_verified_ledger(self):
        with self.assertRaisesRegex(EvidenceError, "OMS_CACHE_EVENTS_IDENTITY"):
            self.parser.replay_ledger(copy.deepcopy(self.events), self.period, "BTC.USD", NOW)

    def test_collect_refreshes_native_observations_while_reusing_journal_and_ledger(self):
        with tempfile.TemporaryDirectory(prefix="hepta-pnl-collect-") as directory:
            root = Path(directory);state_root = root/"state";state_root.mkdir(mode=0o700)
            period_path = root/"period.json"
            def private_json(path, value):
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_text(json.dumps(value, default=str));path.chmod(0o600)
            private_json(period_path, self.period)
            journal = state_root/"execution/oms-journal.jsonl";journal.parent.mkdir(mode=0o700)
            journal.write_bytes(oms_bytes(basic()));journal.chmod(0o600)
            for name, instrument, quantity in (("paper-driver", "EUR.USD", 0), ("crypto-driver", "BTC.USD", 1)):
                private_json(state_root/name/"state.json", {
                    "instrument":instrument,"day_one_started_at_ms":START,"gross_pnl":0,"position":quantity})
            reader = object();current_bid = 90
            def observe_fresh(selected_reader, selected_period, instrument):
                self.assertIs(selected_reader, reader);self.assertEqual(selected_period, self.period)
                native = observation(quantity=1 if instrument=="BTC.USD" else 0,
                                     bid=current_bid, ask=current_bid+2, instrument=instrument)
                native["service_identity"] = ["same-service", 3]
                return native
            with mock.patch.object(report, "now_ms", return_value=NOW), \
                 mock.patch.object(report, "observe", side_effect=observe_fresh) as native, \
                 mock.patch.object(report, "replay_ledger", wraps=report.replay_ledger) as raw:
                first = report.collect(reader, period_path, state_root, self.parser)
                current_bid = 95
                second = report.collect(reader, period_path, state_root, self.parser)
                self.assertEqual(raw.call_count, 2)
                uncached = report.collect(reader, period_path, state_root)
                self.assertEqual(second, uncached);self.assertNotEqual(first, second)
                self.assertEqual(native.call_args_list, [
                    mock.call(reader, self.period, instrument)
                    for _ in range(3) for instrument in accounting.INSTRUMENTS])
                self.assertEqual(self.parser._files.full_reads, 1)


if __name__=="__main__":unittest.main()
