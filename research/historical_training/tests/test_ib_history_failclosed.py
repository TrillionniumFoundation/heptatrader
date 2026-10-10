"""Historical downloader must not issue another broker request after any failed callback."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from historical_training.common import EvidenceError, digest, sha, write_once
from historical_training.ib_history import (
    CONTRACT, completion_state, global_history_lock, plans, preflight_existing, sync_dir, verified_receipt,
)


class HistoricalFailClosedTests(unittest.TestCase):
    def test_error_162_stops_even_when_ib_signals_completion(self):
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Request Timed Out"}], False),
            ("ERROR", "REQUEST_ERROR_NO_AUTOMATIC_RETRY"),
        )
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Request Timed Out"}], True),
            ("ERROR", "REQUEST_ERROR_NO_AUTOMATIC_RETRY"),
        )

    def test_no_end_callback_is_not_success(self):
        self.assertEqual(
            completion_state(True, [], False),
            ("TIMEOUT", "REQUEST_TIMEOUT_NO_AUTOMATIC_RETRY"),
        )
        self.assertEqual(
            completion_state(False, [], True),
            ("TIMEOUT", "REQUEST_TIMEOUT_NO_AUTOMATIC_RETRY"),
        )

    def test_pacing_error_takes_priority(self):
        self.assertEqual(
            completion_state(True, [{"code": 162, "message": "Historical pacing violation"}], False),
            ("ERROR", "PACING_STOP"),
        )
        self.assertEqual(completion_state(True, [], True), ("RECEIVED", None))

    @staticmethod
    def fixture(root, plan, state):
        key = digest({"contract": CONTRACT, **plan})
        chunk = root / "chunks" / key
        response = {
            "bars": [], "errors": [] if state == "RECEIVED" else [
                {"code": 162, "message": "Historical service timeout"}],
            "head": "20260311 00:00:00" if plan["kind"] == "head" else None,
            "schedule": None,
            "end_callback": None,
        }
        write_once(chunk / "request.json", plan)
        write_once(chunk / "response.json", response)
        write_once(chunk / "receipt.json", {
            "request": plan, "state": state,
            "response_sha256": sha(chunk / "response.json"),
            "rows": 0,
        })
        return chunk

    def test_successful_receipt_is_reusable_without_reissue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            self.assertEqual(verified_receipt(chunk, plan)["state"], "RECEIVED")
            preflight_existing(root, config, end)

    def test_failed_receipt_requires_distinct_root_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            chunk = self.fixture(root, plan, "ERROR")
            original = (chunk / "receipt.json").read_bytes()
            with self.assertRaisesRegex(EvidenceError, "FAILED_CHUNK_IMMUTABLE"):
                preflight_existing(root, config, end)
            self.assertEqual(original, (chunk / "receipt.json").read_bytes())

    def test_late_failed_chunk_rejected_before_resume_network(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            requests = plans(config, end)
            self.fixture(root, requests[0], "RECEIVED")
            self.fixture(root, requests[-1], "TIMEOUT")
            with self.assertRaisesRegex(EvidenceError, "FAILED_CHUNK_IMMUTABLE"):
                preflight_existing(root, config, end)

    def test_orphan_request_unknown_dispatch_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = {"chunks": 1}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans(config, end)[0]
            key = digest({"contract": CONTRACT, **plan})
            chunk = root / "chunks" / key
            write_once(chunk / "request.json", plan)
            original = (chunk / "request.json").read_bytes()
            with self.assertRaisesRegex(EvidenceError, "UNFINALIZED_CHUNK_UNKNOWN_OUTCOME"):
                preflight_existing(root, config, end)
            self.assertEqual(original, (chunk / "request.json").read_bytes())

    def test_tampered_request_refused_even_with_valid_response(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({"chunks": 1}, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            (chunk / "request.json").chmod(0o600)
            (chunk / "request.json").write_text('{"kind":"wrong"}\n')
            with self.assertRaisesRegex(EvidenceError, "RESUME_CHUNK_CHANGED"):
                verified_receipt(chunk, plan)

    def test_pinned_history_client_globally_fenced_across_roots(self):
        with tempfile.TemporaryDirectory() as td:
            left = Path(td) / "first"
            right = Path(td) / "second"
            with global_history_lock(left):
                with self.assertRaises(BlockingIOError):
                    with global_history_lock(right):
                        self.fail("second history session acquired the same pinned client")
            with global_history_lock(right):
                self.assertTrue((Path(td) / "ib-history-client-8984.lock").exists())

    def test_synced_orphan_is_still_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "history"
            plan = plans({"chunks": 1}, datetime(2026, 10, 8, tzinfo=timezone.utc))[0]
            chunk = root / "chunks" / digest({"contract": CONTRACT, **plan})
            write_once(chunk / "request.json", plan)
            for path in (chunk, chunk.parent, root, root.parent):
                sync_dir(path)
            with self.assertRaisesRegex(EvidenceError, "UNFINALIZED_CHUNK_UNKNOWN_OUTCOME"):
                verified_receipt(chunk, plan)

    def test_mismatched_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({"chunks": 1}, end)[0]
            chunk = self.fixture(root, plan, "RECEIVED")
            (chunk / "response.json").chmod(0o600)
            (chunk / "response.json").write_text('{"changed":true}\n')
            with self.assertRaisesRegex(EvidenceError, "RESUME_CHUNK_CHANGED"):
                verified_receipt(chunk, plan)

    def test_received_state_without_ib_end_callback_is_not_reusable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({'chunks': 1}, end)[3]  # MIDPOINT bars
            chunk = self.fixture(root, plan, 'RECEIVED')
            with self.assertRaisesRegex(EvidenceError,
                                        'RECEIVED_CHUNK_COMPLETION_UNPROVEN'):
                verified_receipt(chunk, plan)

    def test_error_payload_cannot_be_relabelled_success_and_resumed(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            plan = plans({'chunks': 1}, end)[0]  # Head timestamp
            chunk = self.fixture(root, plan, 'RECEIVED')
            response_path = chunk / 'response.json'
            receipt_path = chunk / 'receipt.json'
            response = json.loads(response_path.read_text())
            response['errors'] = [{'request_id': 7800, 'code': 162,
                                   'message': 'Request Timed Out'}]
            response_path.chmod(0o600)
            response_path.write_text(json.dumps(response))
            receipt = json.loads(receipt_path.read_text())
            receipt['response_sha256'] = sha(response_path)
            receipt_path.chmod(0o600)
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(EvidenceError,
                                        'RECEIVED_CHUNK_PAYLOAD_INVALID'):
                verified_receipt(chunk, plan)



class HistoricalSessionIOTests(unittest.TestCase):
    """Exercise an entire sealed request lifecycle without IB socket access."""

    class FakeHistoryApp:
        def __init__(self, fail_first=False):
            from types import SimpleNamespace
            import threading
            self.fail_first = fail_first
            self.paper = True
            self.dead = False
            self.ready = threading.Event()
            self.ready.set()
            self.account_event = threading.Event()
            self.account_event.set()
            self.guard = SimpleNamespace(last=None, register=lambda *args: None)
            self.own, self.active = set(), set()
            self.events, self.rows = {}, {}
            self.heads, self.ends, self.schedules, self.errors = {}, {}, {}, []
            self.sent = {}
            self.calls = []
            self.disconnected = False

        def connect(self, host, port, client_id):
            self.calls.append(('CONNECT', host, port, client_id))

        def run(self):
            return None

        def serverVersion(self):
            return 187

        def reqHeadTimeStamp(self, rid, contract, what, use_rth, fmt):
            self.calls.append(('HEAD', rid, what))
            if self.fail_first:
                self.errors.append({'request_id': rid, 'code': 162,
                                    'message': 'Request Timed Out'})
            else:
                self.heads[rid] = '20260311 00:00:00'
            self.events[rid].set()

        def reqHistoricalData(self, rid, contract, end, duration, bar_size,
                              what, use_rth, fmt, keep_up, options):
            self.calls.append(('HISTORY', rid, what))
            if what == 'SCHEDULE':
                self.schedules[rid] = {'sessions': []}
            else:
                self.ends[rid] = {'start': end, 'end': end}
                self.rows[rid] = [{'time': '20261008-00:00:00',
                                   'open': '1.1', 'close': '1.1'}]
            self.events[rid].set()

        def cancel_own(self, rid, head=False):
            return None

        def disconnect(self):
            self.disconnected = True

    def test_successful_broker_callbacks_seal_receipts_and_resume(self):
        from unittest.mock import patch
        from historical_training.ib_history import _run_locked
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'history-v2'
            config = {'chunks': 1, 'spacing_sec': 0, 'host': '127.0.0.1', 'port': 4002, 'client_id': 8984}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            app = self.FakeHistoryApp()
            with patch('historical_training.ib_history.make_app',
                       return_value=(app, object())):
                session = _run_locked(config, end, root)
            self.assertNotIn('exception', session)
            self.assertEqual(len(session['results']), 7)
            self.assertTrue(all(row['state'] == 'RECEIVED'
                                for row in session['results']))
            self.assertEqual(len([x for x in app.calls if x[0] != 'CONNECT']), 7)
            self.assertTrue(app.disconnected)
            for plan in plans(config, end):
                key = digest({'contract': CONTRACT, **plan})
                chunk = root / 'chunks' / key
                receipt = verified_receipt(chunk, plan)
                self.assertEqual(receipt['response_sha256'],
                                 sha(chunk / 'response.json'))
            # A second process may reuse the immutable receipts, not dispatch.
            preflight_existing(root, config, end)
            replay = self.FakeHistoryApp()
            with patch('historical_training.ib_history.make_app',
                       return_value=(replay, object())):
                next_session = _run_locked(config, end, root)
            self.assertNotIn('exception', next_session)
            self.assertEqual(len([x for x in replay.calls
                                  if x[0] != 'CONNECT']), 0)
            self.assertTrue(all(row['resumed'] for row in next_session['results']))

    def test_first_error_162_seals_and_stops_without_reissuing(self):
        from unittest.mock import patch
        from historical_training.ib_history import _run_locked
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'failure-v2'
            config = {'chunks': 1, 'spacing_sec': 0, 'host': '127.0.0.1', 'port': 4002, 'client_id': 8984}
            end = datetime(2026, 10, 8, tzinfo=timezone.utc)
            app = self.FakeHistoryApp(fail_first=True)
            with patch('historical_training.ib_history.make_app',
                       return_value=(app, object())):
                session = _run_locked(config, end, root)
            self.assertIn('REQUEST_ERROR_NO_AUTOMATIC_RETRY',
                          session['exception'])
            self.assertEqual(len(session['results']), 1)
            self.assertEqual(session['results'][0]['state'], 'ERROR')
            self.assertEqual(len([x for x in app.calls
                                  if x[0] != 'CONNECT']), 1)
            first = plans(config, end)[0]
            chunk = root / 'chunks' / digest({'contract': CONTRACT, **first})
            self.assertTrue((chunk / 'receipt.json').exists())
            self.assertEqual((__import__('json').loads(
                (chunk / 'receipt.json').read_text()))['error_codes'], [162])
            with self.assertRaisesRegex(EvidenceError,
                                        'FAILED_CHUNK_IMMUTABLE'):
                preflight_existing(root, config, end)


class CashHistoryFarmGateTests(unittest.TestCase):
    """Test market-farm notification ordering without opening an IB connection."""

    def test_cash_history_down_up_and_demand_idle_are_distinct(self):
        from historical_training.ib_history import observe_cash_hmds
        step=observe_cash_hmds
        state='UNKNOWN'
        state=step(state,-1,2105,'历史市场数据场连接中断:cashhmds')
        self.assertEqual(state,'DISCONNECTED')
        # An unrelated historical farm cannot clear the cashhmds disconnect.
        self.assertEqual(step(state,-1,2106,'historical farm OK:apachmds'),state)
        self.assertEqual(step(state,7800,2106,'cashhmds'),state)
        state=step(state,-1,2107,'cashhmds inactive')
        self.assertEqual(state,'INACTIVE')
        state=step(state,-1,2106,'cashhmds connected')
        self.assertEqual(state,'CONNECTED')
        self.assertEqual(step(state,-1,2105,'unrelatedhmds'),'CONNECTED')
        # A request-scoped 162 is already handled by the immutable request receipt.
        self.assertEqual(step(state,7800,162,'Request Timed Out'),'CONNECTED')

    def test_2105_cash_farm_disconnected_stops_before_first_request(self):
        from unittest.mock import patch
        from historical_training.ib_history import _run_locked
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'unavailable-cash-hmds'
            c={'chunks':1,'spacing_sec':12,'host':'127.0.0.1',
               'port':4002,'client_id':8984}
            end=datetime(2026,10,8,tzinfo=timezone.utc)
            app=HistoricalSessionIOTests.FakeHistoryApp()
            app.cash_hmds_state='DISCONNECTED'
            app.errors.append({'request_id':-1,'code':2105,
                               'message':'cashhmds disconnected'})
            with patch('historical_training.ib_history.make_app',
                       return_value=(app,object())):
                session=_run_locked(c,end,root)
            self.assertIn('CASH_HMDS_DISCONNECTED_NO_HISTORY_DISPATCH',
                          session['exception'])
            self.assertEqual(session['requests_sent_this_run'],0)
            self.assertEqual(session['broker_orders_sent'],0)
            self.assertEqual(session['cash_hmds_state'],'DISCONNECTED')
            self.assertEqual([x[0] for x in app.calls],['CONNECT'])
            self.assertFalse((root/'chunks').exists())
            self.assertTrue((root/'session.json').exists())

    def test_cash_farm_2107_idle_does_not_block_request(self):
        from unittest.mock import patch
        from historical_training.ib_history import _run_locked
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'idle-history'
            c={'chunks':1,'spacing_sec':0,'host':'127.0.0.1',
               'port':4002,'client_id':8984}
            end=datetime(2026,10,8,tzinfo=timezone.utc)
            app=HistoricalSessionIOTests.FakeHistoryApp()
            app.cash_hmds_state='INACTIVE'
            with patch('historical_training.ib_history.make_app',
                       return_value=(app,object())):
                session=_run_locked(c,end,root)
            self.assertNotIn('exception',session)
            self.assertEqual(session['requests_sent_this_run'],7)
            self.assertEqual(session['cash_hmds_state'],'INACTIVE')

    def test_disconnect_during_pacing_refuses_dispatch(self):
        from unittest.mock import patch
        from historical_training.ib_history import _run_locked
        import time
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'disconnect-on-pacing'
            c={'chunks':1,'spacing_sec':12,'host':'127.0.0.1',
               'port':4002,'client_id':8984}
            end=datetime(2026,10,8,tzinfo=timezone.utc)
            app=HistoricalSessionIOTests.FakeHistoryApp()
            app.cash_hmds_state='CONNECTED'
            app.guard.last=time.monotonic()
            def lose_farm(_seconds):
                app.cash_hmds_state='DISCONNECTED'
            with patch('historical_training.ib_history.make_app',
                       return_value=(app,object())), patch(
                           'historical_training.ib_history.time.sleep',
                           side_effect=lose_farm):
                session=_run_locked(c,end,root)
            self.assertIn('CASH_HMDS_DISCONNECTED_NO_HISTORY_DISPATCH',
                          session['exception'])
            self.assertEqual(session['requests_sent_this_run'],0)
            self.assertEqual([x[0] for x in app.calls],['CONNECT'])


if __name__ == "__main__":
    unittest.main()
