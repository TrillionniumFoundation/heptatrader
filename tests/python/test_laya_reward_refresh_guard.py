"""Real input-watch invalidation and transactional reward snapshot reuse."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

from laya_cell import resident
from laya_cell.integrity import ContractError, read_json
from laya_cell.resident_reward_cache import RewardRefreshGuard
from laya_cell.resident_store import database
from hepta_pnl_watch import _ChangeWatch
import test_decision_attribution_directory_cache as attribution_fixtures


@unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux inotify")
class RewardRefreshGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = attribution_fixtures.AttributionDirectoryCacheTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.period_path = self.root / "period.json"
        self.oms = self.root / "oms.jsonl"
        self.fixture.private(self.period_path, b'{"period":1}')
        self.fixture.private(self.oms, b'{"event":"audit","ts_ms":1}\n')
        self.exports = self.root / "exports"
        self.exports.mkdir()
        self.fx_exports = self.exports / "EUR.USD"
        self.fx_exports.mkdir()
        self.btc_exports = self.exports / "BTC.USD"
        self.c = {**self.fixture.config(), "period_path": str(self.period_path), "oms_path": str(self.oms),
                  "export_directories": {"EUR.USD": str(self.fx_exports), "BTC.USD": str(self.btc_exports)}}
        self.guard = RewardRefreshGuard()
        self.addCleanup(self.guard.close)
        self.result = {"at_ms": 100000, "reason": "ATTRIBUTED_SETTLED_REWARDS_READY", "blocked": [],
                       "rewards": [{"reward_id": "reward", "realized_net_usd": "-22"}],
                       "execution_rewards": [{"reward_id": "execution", "realized_net_usd": "-23"}],
                       "components": {"EUR.USD": {"ledger_complete": True}, "BTC.USD": {"ledger_complete": True}}}

    def prime(self):
        self.assertTrue(self.guard.begin(self.c, 100000, 100.0))
        self.guard.observe(1)
        self.assertTrue(self.guard.finish(self.c, self.result, 100000))

    def test_idle_reuses_original_asof_beyond_old_one_minute_timer(self):
        self.prime()
        self.assertEqual(self.guard.state(self.c, 161000, 161.0), "UNCHANGED")
        self.assertEqual(self.guard.status()["last_full_read_at_ms"], 100000)
        self.assertEqual(self.result["at_ms"], 100000)

    def test_original_full_replay_is_required_at_five_minutes(self):
        self.prime()
        self.assertEqual(self.guard.state(self.c, 399999, 399.999), "UNCHANGED")
        self.assertEqual(self.guard.state(self.c, 400000, 400.0), "CHANGED")

    def test_append_invalidates_at_next_poll(self):
        self.prime()
        with self.oms.open("ab") as stream:
            stream.write(b'{"event":"broker_commission"}\n')
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_equal_size_rewrite_with_restored_mtime_invalidates(self):
        self.prime()
        before = self.oms.stat()
        self.oms.write_bytes(self.oms.read_bytes().replace(b'"ts_ms":1', b'"ts_ms":2'))
        os.utime(self.oms, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.oms.stat().st_size, before.st_size)
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_period_rewrite_invalidates(self):
        self.prime()
        self.period_path.write_bytes(b'{"period":2}')
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_manifest_pin_change_invalidates(self):
        self.prime()
        path = Path(self.fixture.registrations[0]["manifest"])
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_producer_health_creation_invalidates(self):
        self.prime()
        self.fixture.private(self.fixture.directory / "producer-1.health.json", b'{"dropped":1}')
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_producer_stream_append_invalidates(self):
        self.prime()
        with (self.fixture.directory / "producer-1.jsonl").open("ab") as stream:
            stream.write(b'{"new":"record"}\n')
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_new_segment_and_export_files_invalidate(self):
        for directory in (self.fixture.directory, self.fx_exports):
            with self.subTest(directory=directory):
                self.prime()
                self.fixture.private(directory / "new.jsonl", b"{}\n")
                self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_absent_btc_export_is_not_created_and_creation_invalidates(self):
        self.prime()
        self.assertFalse(self.btc_exports.exists())
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "UNCHANGED")
        self.btc_exports.mkdir()
        self.assertEqual(self.guard.state(self.c, 100002, 100.002), "CHANGED")

    def test_unrelated_manifest_parent_file_does_not_invalidate(self):
        self.prime()
        (self.root / "unrelated-status.json").write_text("{}")
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "UNCHANGED")

    def test_lost_watch_requires_full_refresh(self):
        self.prime()
        next(iter(self.guard._watches.values())).close()
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def test_unavailable_watch_falls_back_without_claiming_reuse(self):
        with mock.patch.object(_ChangeWatch, "arm", return_value=False):
            self.assertFalse(self.guard.begin(self.c, 100000, 100.0))
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "UNAVAILABLE")

    def test_wall_and_monotonic_clock_regressions_invalidate(self):
        for at_ms, tick in ((100001, 100.0001), (100000, 100.002)):
            with self.subTest(at_ms=at_ms, tick=tick):
                self.prime()
                self.assertEqual(self.guard.state(self.c, 100001, 100.001), "UNCHANGED")
                self.assertEqual(self.guard.state(self.c, at_ms, tick), "CHANGED")

    def test_future_witness_does_not_allow_snapshot_reuse(self):
        self.assertTrue(self.guard.begin(self.c, 100000, 100.0))
        self.guard.observe(100001)
        self.assertFalse(self.guard.finish(self.c, self.result, 100000))
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "UNAVAILABLE")

    def test_attribution_clock_failure_cannot_be_cached(self):
        self.assertTrue(self.guard.begin(self.c, 100000, 100.0))
        self.guard.observe(1)
        result = {**self.result, "blocked": [{"reason": "ATTRIBUTION_UNAVAILABLE:EXPORT_CLOCK"}]}
        self.assertFalse(self.guard.finish(self.c, result, 100000))

    def test_mid_read_mutation_forces_next_poll_even_inside_old_timer(self):
        self.assertTrue(self.guard.begin(self.c, 100000, 100.0))
        self.guard.observe(1)
        self.oms.write_bytes(b'{"event":"changed"}\n')
        self.assertFalse(self.guard.finish(self.c, self.result, 100000))
        self.assertEqual(self.guard.state(self.c, 100001, 100.001), "CHANGED")

    def supervisor(self):
        service = resident.Supervisor.__new__(resident.Supervisor)
        service.c = self.c
        service.root = self.root / "control"
        service.root.mkdir()
        service.db = database(service.root / "scheduler.sqlite")
        self.addCleanup(service.db.close)
        self.addCleanup(lambda: getattr(service, "_reward_refresh_guard", self.guard).close())
        return service

    def test_scheduler_skips_real_db_publication_until_change_or_bound(self):
        service = self.supervisor()
        def source(config, now, *, source_observer=None):
            if source_observer is not None:
                source_observer(1)
            return {**copy.deepcopy(self.result), "at_ms": now}
        with mock.patch.object(resident, "rewards_current", side_effect=source) as read, \
             mock.patch.object(resident.time, "monotonic", return_value=100.0) as tick:
            first = service.sync_rewards(100000)
            changes = service.db.total_changes
            tick.return_value = 161.0
            self.assertEqual(service.sync_rewards(161000), first)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(service.db.total_changes, changes)
            self.assertEqual(read_json(service.root / "rewards-status.json")["at_ms"], 100000)
            tick.return_value = 400.0
            self.assertEqual(service.sync_rewards(400000)["at_ms"], 400000)
            self.assertEqual(read.call_count, 2)

    def test_changed_input_inside_minute_replays_and_conflicts_never_reuse_old_snapshot(self):
        service = self.supervisor()
        current = copy.deepcopy(self.result)
        def source(config, now, *, source_observer=None):
            if source_observer is not None:
                source_observer(1)
            return {**copy.deepcopy(current), "at_ms": now}
        with mock.patch.object(resident, "rewards_current", side_effect=source) as read, \
             mock.patch.object(resident.time, "monotonic", return_value=100.0) as tick:
            service.sync_rewards(100000)
            before = (service.root / "rewards-status.json").read_bytes()
            self.oms.write_bytes(b'{"event":"new"}\n')
            current["rewards"][0]["realized_net_usd"] = "999"
            for value in (101.0, 102.0):
                tick.return_value = value
                with self.assertRaisesRegex(ContractError, "REWARD_ID_CONFLICT"):
                    service.sync_rewards(int(value * 1000))
            self.assertEqual(read.call_count, 3)
            self.assertEqual((service.root / "rewards-status.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
