#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    collector = load(root / "scripts/hepta_shadow_watch_collector.py", "collector")
    exporter = load(root / "scripts/hepta_shadow_watch_exporter.py", "exporter")
    assert collector.FORBIDDEN_TOOLS == {
        "risk.preview_order", "risk.preview_flatten", "trade.place_order",
        "trade.cancel_order", "trade.flatten_position"}
    assert collector.READ_ORDER[-1] == "system.get_health"
    assert set(collector.READ_ORDER) == set(collector.REQUIRED_TOOLS)
    assert "account" not in collector.SAFE_FIELDS
    assert "subscription_id" not in collector.SAFE_FIELDS
    for field in (
            "gateway_ready", "remote_execution", "remote_execution_configured",
            "remote_execution_ready", "execution_mode",
            "execution_service_epoch", "execution_service_fencing_generation",
            "remote_execution_reason", "read_model", "paper_template_enabled"):
        assert field in collector.SAFE_FIELDS

    safe = collector._safe({
        "account": "DU-SECRET", "subscription_id": "IB:secret",
        "authoritative": True, "positions": [{
            "instrument": "EUR.USD", "quantity": 1, "conid": 12087792}],
    })
    assert safe == {
        "authoritative": True,
        "positions": [{"instrument": "EUR.USD", "quantity": 1}],
    }
    health = collector._safe({
        "gateway_ready": True, "remote_execution": True,
        "remote_execution_configured": True, "remote_execution_ready": True,
        "execution_mode": "IB_PAPER",
        "execution_service_epoch": "epoch-1",
        "execution_service_fencing_generation": 7,
        "remote_execution_reason": "", "read_model": "execution_authoritative_v1",
        "paper_template_enabled": False, "account": "DU-SECRET",
    })
    assert health == {
        "gateway_ready": True, "remote_execution": True,
        "remote_execution_configured": True, "remote_execution_ready": True,
        "execution_mode": "IB_PAPER",
        "execution_service_epoch": "epoch-1",
        "execution_service_fencing_generation": 7,
        "remote_execution_reason": "", "read_model": "execution_authoritative_v1",
        "paper_template_enabled": False,
    }
    authoritative_reads = {
        "system.get_health": {
            "gateway_ready": True, "remote_execution": True,
            "remote_execution_configured": True,
            "remote_execution_ready": True, "execution_mode": "SIMULATOR",
            "remote_execution_reason": "",
        },
        "account.get_summary": {
            "authoritative": True, "account_complete": True,
        },
        "portfolio.list_positions": {
            "authoritative": True, "positions": [],
        },
        "orders.list": {
            "authoritative": True, "active_order_ids": [],
        },
        "risk.get_limits": {
            "authoritative": True, "gross_absolute_position": 0,
        },
        "market.get_quote": {
            "authoritative": True, "stale": False,
            "observed_at_ms": 1, "stale_after_ms": 2,
        },
    }
    collector._validate_reads(authoritative_reads)
    paper_health = dict(authoritative_reads)
    paper_health["system.get_health"] = {
        **authoritative_reads["system.get_health"],
        "execution_mode": "PAPER",
    }
    try:
        collector._validate_reads(paper_health)
    except collector.CollectorError as error:
        assert str(error) == "WATCH_HEALTH_NOT_AUTHORITATIVE"
    else:
        raise AssertionError("PAPER health was accepted by SHADOW collector")
    non_authoritative_health = dict(authoritative_reads)
    non_authoritative_health["system.get_health"] = {
        **authoritative_reads["system.get_health"],
        "remote_execution_ready": False,
        "remote_execution_reason": "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH",
    }
    try:
        collector._validate_reads(non_authoritative_health)
    except collector.CollectorError as error:
        assert str(error) == "WATCH_HEALTH_NOT_AUTHORITATIVE"
    else:
        raise AssertionError("non-authoritative health was accepted")
    try:
        collector._call("/tmp/x", Path("/tmp/y"), "trade.place_order")
    except collector.CollectorError as error:
        assert str(error) == "WATCH_TOOL_FORBIDDEN"
    else:
        raise AssertionError("forbidden mutation tool was accepted")

    with tempfile.TemporaryDirectory(
            prefix="hepta-watch-missing-token-") as directory:
        fixture = Path(directory)
        original_config_loader = collector.load_runtime_config
        original_identity = collector._identity
        collector.load_runtime_config = lambda *_args, **_kwargs: {
            "domain_id": "alpha",
            "agent_uid": os.geteuid(),
            "agent_gid": os.getegid(),
            "token_directory": str(fixture),
            "socket_path": "/tmp/hepta-test.sock",
        }
        collector._identity = lambda _config: None
        try:
            try:
                collector.collect(
                    fixture / "domain.json",
                    fixture / "snapshot.json",
                    "EUR.USD",
                )
            except collector.CollectorError as error:
                assert str(error) == "WATCH_SESSION_AUTHORITY_NOT_FOUND"
            else:
                raise AssertionError(
                    "missing WATCH token was not terminal")
        finally:
            collector.load_runtime_config = original_config_loader
            collector._identity = original_identity

    with tempfile.TemporaryDirectory(prefix="hepta-watch-cadence-test-") as directory:
        fixture = Path(directory)
        original_config_loader = collector.load_runtime_config
        original_identity = collector._identity
        original_regular = collector._regular
        original_read_set = collector._read_set
        original_time_ns = collector.time.time_ns
        clock = [1_000_000_000]
        calls: list[str] = []

        def fake_time_ns() -> int:
            return clock[0]

        def fake_read_set(
                _socket_path: str, _token_path: Path,
                instrument: str) -> dict[str, object]:
            calls.append(instrument)
            read_times: dict[str, int] = {}
            for tool in collector.READ_ORDER:
                clock[0] += 2_000_000_000
                read_times[tool] = clock[0] // 1_000_000
            return {
                "schema": "hepta.watch-read-set.v1",
                "catalog": {
                    "tools": [
                        {"name": name} for name in collector.REQUIRED_TOOLS
                    ],
                },
                "descriptors": {
                    name: {"name": name} for name in collector.REQUIRED_TOOLS
                },
                "reads": json.loads(json.dumps(authoritative_reads)),
                "read_finished_at_ms": read_times,
            }

        collector.load_runtime_config = lambda *_args, **_kwargs: {
            "domain_id": "alpha",
            "agent_uid": os.geteuid(),
            "agent_gid": os.getegid(),
            "token_directory": str(fixture),
            "socket_path": "/tmp/hepta-test.sock",
        }
        collector._identity = lambda _config: None
        collector._regular = lambda *_args, **_kwargs: None
        collector._read_set = fake_read_set
        collector.time.time_ns = fake_time_ns
        try:
            snapshot = collector.collect(
                fixture / "domain.json",
                fixture / "snapshot.json",
                "EUR.USD",
            )
        finally:
            collector.load_runtime_config = original_config_loader
            collector._identity = original_identity
            collector._regular = original_regular
            collector._read_set = original_read_set
            collector.time.time_ns = original_time_ns
        assert calls == ["EUR.USD"]
        assert snapshot["collection_started_at_ms"] == 1_000
        assert snapshot["read_finished_at_ms"]["account.get_summary"] > 1_000
        assert snapshot["collection_finished_at_ms"] >= (
            snapshot["read_finished_at_ms"]["system.get_health"])

    good_envelope = json.dumps({
        "status": "ok", "tool": "system.get_health", "reason_code": "",
        "detail": "", "order_id": -1, "payload": {"gateway_ready": True},
    })
    original_run = collector.subprocess.run
    original_sleep = collector.time.sleep
    calls: list[int] = []
    sleeps: list[float] = []

    def transient_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        if len(calls) < collector.PROCESS_ATTEMPTS:
            return subprocess.CompletedProcess([], 1, "", "transient")
        return subprocess.CompletedProcess([], 0, good_envelope, "")

    collector.subprocess.run = transient_run
    collector.time.sleep = sleeps.append
    try:
        assert collector._call(
            "/tmp/x", Path("/tmp/y"), "system.get_health") == {
                "gateway_ready": True}
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(calls) == collector.PROCESS_ATTEMPTS
    assert sleeps == [collector.PROCESS_RETRY_SECONDS,
                      collector.PROCESS_RETRY_SECONDS * 2]

    rejected_calls: list[int] = []

    def rejected_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        rejected_calls.append(1)
        return subprocess.CompletedProcess([], 1, "", "rejected")

    collector.subprocess.run = rejected_run
    collector.time.sleep = lambda _seconds: None
    try:
        try:
            collector._call("/tmp/x", Path("/tmp/y"), "system.get_health")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_TOOL_PROCESS_REJECTED"
        else:
            raise AssertionError("permanent process rejection was accepted")
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(rejected_calls) == collector.PROCESS_ATTEMPTS

    envelope_calls: list[int] = []

    def bad_envelope_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        envelope_calls.append(1)
        return subprocess.CompletedProcess([], 0, "{}", "")

    collector.subprocess.run = bad_envelope_run
    try:
        try:
            collector._call("/tmp/x", Path("/tmp/y"), "system.get_health")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_TOOL_ENVELOPE_REJECTED"
        else:
            raise AssertionError("invalid envelope was accepted")
    finally:
        collector.subprocess.run = original_run
    assert len(envelope_calls) == 1

    expected_terminal_codes = {
        "SESSION_NOT_FOUND": "WATCH_SESSION_AUTHORITY_NOT_FOUND",
        "SESSION_REVOKED": "WATCH_SESSION_AUTHORITY_REVOKED",
        "SESSION_EXPIRED": "WATCH_SESSION_AUTHORITY_EXPIRED",
        "SESSION_ALREADY_EXPIRED": "WATCH_SESSION_AUTHORITY_EXPIRED",
        "SESSION_DISABLED": "WATCH_SESSION_AUTHORITY_DISABLED",
        "SESSION_LEASE_GENERATION_CHANGED":
            "WATCH_SESSION_GENERATION_CHANGED",
        "SESSION_OWNER_FENCED": "WATCH_SESSION_OWNER_FENCED",
        "SESSION_OWNER_FENCE_PENDING": "WATCH_SESSION_FENCE_PENDING",
        "SESSION_REMOTE_FENCE_PENDING": "WATCH_SESSION_FENCE_PENDING",
    }
    assert collector.TERMINAL_SESSION_CODES == expected_terminal_codes
    for source_reason, collector_reason in sorted(
            expected_terminal_codes.items()):
        terminal_calls: list[int] = []
        terminal_envelope = json.dumps({
            "status": "error", "tool": "account.get_summary",
            "reason_code": source_reason, "detail": "must-not-propagate",
            "order_id": -1, "payload": None,
        })

        def terminal_session(
                *_args: object,
                _envelope: str = terminal_envelope,
                **_kwargs: object) -> subprocess.CompletedProcess[str]:
            terminal_calls.append(1)
            return subprocess.CompletedProcess([], 1, _envelope, "")

        collector.subprocess.run = terminal_session
        try:
            try:
                collector._call(
                    "/tmp/x", Path("/tmp/y"), "account.get_summary")
            except collector.CollectorError as error:
                assert str(error) == collector_reason
                assert "must-not-propagate" not in str(error)
            else:
                raise AssertionError(
                    f"terminal session reason {source_reason} was accepted")
        finally:
            collector.subprocess.run = original_run
        assert len(terminal_calls) == 1

    identity_mismatch_envelope = json.dumps({
        "status": "error", "tool": "account.get_summary",
        "reason_code": "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH",
        "detail": "", "order_id": -1, "payload": None,
    })
    identity_calls: list[int] = []
    identity_sleeps: list[float] = []

    def identity_transition_then_ready(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        identity_calls.append(1)
        response = identity_mismatch_envelope if len(identity_calls) == 1 else json.dumps({
            "status": "ok", "tool": "account.get_summary", "reason_code": "",
            "detail": "", "order_id": -1,
            "payload": {"authoritative": True, "account_complete": True},
        })
        return subprocess.CompletedProcess([], 0, response, "")

    collector.subprocess.run = identity_transition_then_ready
    collector.time.sleep = identity_sleeps.append
    try:
        assert collector._call(
            "/tmp/x", Path("/tmp/y"), "account.get_summary") == {
                "authoritative": True, "account_complete": True}
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(identity_calls) == 2
    assert identity_sleeps == [collector.IDENTITY_RETRY_SECONDS]

    permanent_identity_calls: list[int] = []

    def permanent_identity_mismatch(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        permanent_identity_calls.append(1)
        return subprocess.CompletedProcess([], 0, identity_mismatch_envelope, "")

    collector.subprocess.run = permanent_identity_mismatch
    collector.time.sleep = lambda _seconds: None
    try:
        try:
            collector._call("/tmp/x", Path("/tmp/y"), "account.get_summary")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_EXECUTION_IDENTITY_MISMATCH"
        else:
            raise AssertionError("persistent daemon identity mismatch was accepted")
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(permanent_identity_calls) == collector.PROCESS_ATTEMPTS

    event_not_ready_envelope = json.dumps({
        "status": "error", "tool": "account.get_summary",
        "reason_code": "EXECUTION_EVENT_SERVICE_NOT_READY",
        "detail": "", "order_id": -1, "payload": None,
    })
    event_calls: list[int] = []
    event_sleeps: list[float] = []

    def event_transition_then_ready(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        event_calls.append(1)
        response = event_not_ready_envelope if len(event_calls) == 1 else json.dumps({
            "status": "ok", "tool": "account.get_summary", "reason_code": "",
            "detail": "", "order_id": -1,
            "payload": {"authoritative": True, "account_complete": True},
        })
        return subprocess.CompletedProcess([], 0, response, "")

    collector.subprocess.run = event_transition_then_ready
    collector.time.sleep = event_sleeps.append
    try:
        assert collector._call(
            "/tmp/x", Path("/tmp/y"), "account.get_summary") == {
                "authoritative": True, "account_complete": True}
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(event_calls) == 2
    assert event_sleeps == [collector.EVENT_READY_RETRY_SECONDS]

    permanent_event_calls: list[int] = []

    def permanent_event_not_ready(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        permanent_event_calls.append(1)
        return subprocess.CompletedProcess([], 0, event_not_ready_envelope, "")

    collector.subprocess.run = permanent_event_not_ready
    collector.time.sleep = lambda _seconds: None
    try:
        try:
            collector._call("/tmp/x", Path("/tmp/y"), "account.get_summary")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_EXECUTION_EVENT_NOT_READY"
        else:
            raise AssertionError("persistent event service not-ready was accepted")
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(permanent_event_calls) == collector.PROCESS_ATTEMPTS

    read_failed_envelope = json.dumps({
        "status": "error", "tool": "account.get_summary",
        "reason_code": "EXECUTION_EVENT_RESPONSE_READ_FAILED",
        "detail": "", "order_id": -1, "payload": None,
    })
    read_failed_calls: list[int] = []

    def read_failed_then_ready(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        read_failed_calls.append(1)
        response = read_failed_envelope if len(read_failed_calls) == 1 else json.dumps({
            "status": "ok", "tool": "account.get_summary", "reason_code": "",
            "detail": "", "order_id": -1,
            "payload": {"authoritative": True, "account_complete": True},
        })
        return subprocess.CompletedProcess([], 0, response, "")

    collector.subprocess.run = read_failed_then_ready
    collector.time.sleep = lambda _seconds: None
    try:
        assert collector._call(
            "/tmp/x", Path("/tmp/y"), "account.get_summary")["authoritative"] is True
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(read_failed_calls) == 2

    stale_envelope = json.dumps({
        "status": "ok", "tool": "market.get_quote",
        "reason_code": "AUTHORITATIVE_QUOTE_STALE", "detail": "",
        "order_id": -1, "payload": {},
    })
    quote_calls: list[int] = []
    quote_sleeps: list[float] = []

    def stale_then_fresh(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        quote_calls.append(1)
        response = stale_envelope if len(quote_calls) < 3 else json.dumps({
            "status": "ok", "tool": "market.get_quote", "reason_code": "",
            "detail": "", "order_id": -1,
            "payload": {"authoritative": True, "stale": False},
        })
        return subprocess.CompletedProcess(
            [], 0, response, "")

    collector.subprocess.run = stale_then_fresh
    collector.time.sleep = quote_sleeps.append
    try:
        assert collector._call(
            "/tmp/x", Path("/tmp/y"), "market.get_quote",
            ("instrument=EUR.USD",)) == {
                "authoritative": True, "stale": False}
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(quote_calls) == collector.PROCESS_ATTEMPTS
    assert quote_sleeps == [collector.QUOTE_RETRY_SECONDS,
                            collector.QUOTE_RETRY_SECONDS * 2]

    permanent_stale_calls: list[int] = []

    def permanent_stale(
            *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        permanent_stale_calls.append(1)
        return subprocess.CompletedProcess([], 75, stale_envelope, "")

    collector.subprocess.run = permanent_stale
    collector.time.sleep = lambda _seconds: None
    try:
        try:
            collector._call(
                "/tmp/x", Path("/tmp/y"), "market.get_quote",
                ("instrument=EUR.USD",))
        except collector.CollectorError as error:
            assert str(error) == "WATCH_QUOTE_STALE"
        else:
            raise AssertionError("permanently stale quote was accepted")
    finally:
        collector.subprocess.run = original_run
        collector.time.sleep = original_sleep
    assert len(permanent_stale_calls) == collector.PROCESS_ATTEMPTS

    read_set_document = json.dumps({
        "schema": "hepta.watch-read-set.v1",
        "catalog": {
            "tools": [
                {"name": name} for name in collector.REQUIRED_TOOLS
            ],
        },
        "descriptors": {
            name: {"name": name} for name in collector.REQUIRED_TOOLS
        },
        "reads": authoritative_reads,
        "read_finished_at_ms": {
            name: 1_000 + index
            for index, name in enumerate(collector.READ_ORDER)
        },
    })
    read_set_calls: list[list[str]] = []
    read_set_timeouts: list[float] = []

    def read_set_run(
            command: list[str], *_args: object,
            **_kwargs: object) -> subprocess.CompletedProcess[str]:
        read_set_calls.append(command)
        read_set_timeouts.append(float(_kwargs["timeout"]))
        return subprocess.CompletedProcess([], 0, read_set_document, "")

    collector.subprocess.run = read_set_run
    try:
        read_set = collector._read_set(
            "/tmp/x", Path("/tmp/y"), "EUR.USD")
    finally:
        collector.subprocess.run = original_run
    assert read_set["schema"] == "hepta.watch-read-set.v1"
    assert len(read_set_calls) == 1
    assert read_set_calls[0][-3:] == ["watch", "snapshot", "EUR.USD"]
    assert read_set_calls[0][5:7] == [
        "--io-timeout-ms", str(collector.READ_SET_IO_TIMEOUT_MS)]
    assert collector.READ_SET_IO_TIMEOUT_MS == 5000
    assert collector.READ_SET_DEADLINE_SECONDS == 8.5
    assert 0 < read_set_timeouts[0] <= collector.READ_SET_DEADLINE_SECONDS

    timeout_calls: list[int] = []

    def read_set_timeout(*_args: object, **_kwargs: object) -> None:
        timeout_calls.append(1)
        raise subprocess.TimeoutExpired("heptactl", 4.5)

    collector.subprocess.run = read_set_timeout
    try:
        try:
            collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_READ_SET_TIMEOUT"
        else:
            raise AssertionError("read-set timeout was accepted")
    finally:
        collector.subprocess.run = original_run
    assert len(timeout_calls) == 1

    expected_transport_codes = {
        "SOCKET_CONNECT_FAILED": "WATCH_TOOL_SOCKET_CONNECT_FAILED",
        "FRAME_WRITE_TIMEOUT": "WATCH_TOOL_FRAME_WRITE_TIMEOUT",
        "FRAME_HEADER_TIMEOUT": "WATCH_TOOL_FRAME_HEADER_TIMEOUT",
        "FRAME_BODY_TIMEOUT": "WATCH_TOOL_FRAME_BODY_TIMEOUT",
    }
    assert collector.TRANSPORT_PROCESS_CODES == expected_transport_codes
    for process_code, collector_reason in expected_transport_codes.items():
        read_set_rejections: list[int] = []

        def read_set_rejected(
                *_args: object, code: str = process_code,
                **_kwargs: object
                ) -> subprocess.CompletedProcess[str]:
            read_set_rejections.append(1)
            return subprocess.CompletedProcess([], 4, "", code + "\n")

        collector.subprocess.run = read_set_rejected
        try:
            try:
                collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
            except collector.CollectorError as error:
                assert str(error) == collector_reason
            else:
                raise AssertionError("read-set transport failure was accepted")
        finally:
            collector.subprocess.run = original_run
        assert len(read_set_rejections) == 1

    for rejected_stderr in (
            "UNKNOWN_TRANSPORT_FAILURE\n",
            "FRAME_HEADER_TIMEOUT\nUNTRUSTED_DETAIL\n",
            "FRAME_HEADER_TIMEOUT",
            "FRAME_HEADER_TIMEOUT \n"):
        read_set_rejections = []

        def read_set_rejected_unknown(
                *_args: object, stderr: str = rejected_stderr,
                **_kwargs: object
                ) -> subprocess.CompletedProcess[str]:
            read_set_rejections.append(1)
            return subprocess.CompletedProcess([], 4, "", stderr)

        collector.subprocess.run = read_set_rejected_unknown
        try:
            try:
                collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
            except collector.CollectorError as error:
                assert str(error) == "WATCH_TOOL_PROCESS_REJECTED"
            else:
                raise AssertionError("unknown process stderr was accepted")
        finally:
            collector.subprocess.run = original_run
        assert len(read_set_rejections) == 1

    for rejected_returncode, rejected_stdout in (
            (0, ""),
            (10, ""),
            (4, "unexpected-output")):
        read_set_rejections = []

        def read_set_rejected_context(
                *_args: object,
                returncode: int = rejected_returncode,
                stdout: str = rejected_stdout,
                **_kwargs: object
                ) -> subprocess.CompletedProcess[str]:
            read_set_rejections.append(1)
            return subprocess.CompletedProcess(
                [], returncode, stdout, "FRAME_HEADER_TIMEOUT\n")

        collector.subprocess.run = read_set_rejected_context
        try:
            try:
                collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
            except collector.CollectorError as error:
                assert str(error) == "WATCH_TOOL_PROCESS_REJECTED"
            else:
                raise AssertionError("ambiguous process stderr was accepted")
        finally:
            collector.subprocess.run = original_run
        assert len(read_set_rejections) == 1

    read_set_attempts: list[float] = []
    read_set_sleeps: list[float] = []
    original_monotonic = collector.time.monotonic
    read_set_clock = [100.0]
    composite_stale_envelope = json.dumps({
        "status": "error", "tool": collector.WATCH_SNAPSHOT_TOOL,
        "reason_code": "AUTHORITATIVE_QUOTE_STALE", "detail": "",
        "order_id": -1, "payload": None,
    })

    def semantic_read_set(
            *_args: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
        read_set_attempts.append(float(kwargs["timeout"]))
        if len(read_set_attempts) == 1:
            return subprocess.CompletedProcess(
                [], 75, composite_stale_envelope, "")
        return subprocess.CompletedProcess([], 0, read_set_document, "")

    def semantic_sleep(seconds: float) -> None:
        read_set_sleeps.append(seconds)
        read_set_clock[0] += seconds

    collector.subprocess.run = semantic_read_set
    collector.time.monotonic = lambda: read_set_clock[0]
    collector.time.sleep = semantic_sleep
    try:
        assert collector._read_set(
            "/tmp/x", Path("/tmp/y"), "EUR.USD")["schema"] == (
                "hepta.watch-read-set.v1")
    finally:
        collector.subprocess.run = original_run
        collector.time.monotonic = original_monotonic
        collector.time.sleep = original_sleep
    assert len(read_set_attempts) == 2
    assert read_set_attempts[1] < read_set_attempts[0]
    assert read_set_sleeps == [collector.QUOTE_RETRY_SECONDS]

    composite_terminal_envelope = json.dumps({
        "status": "error", "tool": collector.WATCH_SNAPSHOT_TOOL,
        "reason_code": "SESSION_REVOKED", "detail": "must-not-propagate",
        "order_id": -1, "payload": None,
    })
    composite_terminal_calls: list[int] = []

    def composite_terminal(
            *_args: object, **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
        composite_terminal_calls.append(1)
        return subprocess.CompletedProcess(
            [], 75, composite_terminal_envelope, "")

    collector.subprocess.run = composite_terminal
    try:
        try:
            collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_SESSION_AUTHORITY_REVOKED"
            assert "must-not-propagate" not in str(error)
        else:
            raise AssertionError("composite terminal session failure was accepted")
    finally:
        collector.subprocess.run = original_run
    assert len(composite_terminal_calls) == 1

    composite_identity_envelope = json.dumps({
        "status": "error", "tool": collector.WATCH_SNAPSHOT_TOOL,
        "reason_code": "EXECUTION_GATEWAY_DAEMON_IDENTITY_MISMATCH",
        "detail": "", "order_id": -1, "payload": None,
    })
    composite_identity_calls: list[int] = []
    composite_identity_sleeps: list[float] = []
    identity_clock = [200.0]

    def composite_identity(
            *_args: object, **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
        composite_identity_calls.append(1)
        return subprocess.CompletedProcess(
            [], 75, composite_identity_envelope, "")

    def composite_identity_sleep(seconds: float) -> None:
        composite_identity_sleeps.append(seconds)
        identity_clock[0] += seconds

    collector.subprocess.run = composite_identity
    collector.time.monotonic = lambda: identity_clock[0]
    collector.time.sleep = composite_identity_sleep
    try:
        try:
            collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_EXECUTION_IDENTITY_MISMATCH"
        else:
            raise AssertionError("persistent composite identity mismatch was accepted")
    finally:
        collector.subprocess.run = original_run
        collector.time.monotonic = original_monotonic
        collector.time.sleep = original_sleep
    assert len(composite_identity_calls) == collector.READ_SET_ATTEMPTS
    assert composite_identity_sleeps == [collector.IDENTITY_RETRY_SECONDS]

    unknown_composite_envelope = json.dumps({
        "status": "error", "tool": "watch.get_snapshots",
        "reason_code": "AUTHORITATIVE_QUOTE_STALE", "detail": "",
        "order_id": -1, "payload": None,
    })
    unknown_composite_calls: list[int] = []

    def unknown_composite(
            *_args: object, **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
        unknown_composite_calls.append(1)
        return subprocess.CompletedProcess(
            [], 75, unknown_composite_envelope, "")

    collector.subprocess.run = unknown_composite
    try:
        try:
            collector._read_set("/tmp/x", Path("/tmp/y"), "EUR.USD")
        except collector.CollectorError as error:
            assert str(error) == "WATCH_TOOL_ENVELOPE_REJECTED"
        else:
            raise AssertionError("unknown composite failure tool was accepted")
    finally:
        collector.subprocess.run = original_run
    assert len(unknown_composite_calls) == 1

    with tempfile.TemporaryDirectory(prefix="hepta-watch-test-") as directory:
        fixture = Path(directory)
        source = fixture / "private.json"
        destination = fixture / "export" / "snapshot.json"
        body = {
            "schema": "hepta.shadow-watch-snapshot.v1", "version": 1,
            "domain_id": "alpha", "agent_uid": os.geteuid(),
            "generated_at_ms": 1, "instrument": "EUR.USD",
            "catalog_sha256": "sha256:" + "0" * 64,
            "descriptor_sha256": {}, "reads": {},
            "paper_authorized": False, "live_authorized": False,
            "mutation_attempted": False, "direct_broker_access": False,
        }
        document = dict(body)
        document["body_sha256"] = "sha256:" + hashlib.sha256(
            exporter._canonical(body)).hexdigest()
        source.write_bytes(exporter._canonical(document))
        source.chmod(0o600)
        exported = exporter.export(
            source, destination, os.geteuid(), os.getegid(),
            os.geteuid(), os.getegid(), require_root=False)
        assert exported == document
        assert json.loads(destination.read_text(encoding="ascii")) == document
        assert destination.stat().st_mode & 0o777 == 0o440
        assert destination.stat().st_uid == os.geteuid()
        assert destination.stat().st_gid == os.getegid()

        v2_body = {
            **body,
            "schema": "hepta.shadow-watch-snapshot.v2",
            "version": 2,
            "generated_at_ms": 16,
            "collection_started_at_ms": 10,
            "collection_finished_at_ms": 16,
            "read_finished_at_ms": {
                tool: 10 + index
                for index, tool in enumerate(exporter.READ_ORDER, 1)
            },
            "reads": authoritative_reads,
        }
        v2_document = dict(v2_body)
        v2_document["body_sha256"] = "sha256:" + hashlib.sha256(
            exporter._canonical(v2_body)).hexdigest()
        source.write_bytes(exporter._canonical(v2_document))
        exported_v2 = exporter.export(
            source, destination, os.geteuid(), os.getegid(),
            os.geteuid(), os.getegid(), require_root=False)
        assert exported_v2 == v2_document
        assert exported_v2["read_finished_at_ms"]["system.get_health"] == 16

        lease_source = fixture / "root-private-watch-lease.json"
        lease_destination = (
            fixture / "export" / "shadow-watch-lease-receipt.json")
        lease_body = {
            "schema": "hepta.shadow-watch-lease-receipt.v1",
            "version": 1,
            "domain_id": "alpha",
            "agent_id": "alpha",
            "agent_uid": os.geteuid(),
            "boundary": "WATCH",
            "operation": "PROVISION",
            "lease_generation": 7,
            "previous_lease_generation": None,
            "previous_receipt_body_sha256": None,
            "accepted": True,
            "reason_code": "OK",
            "accepted_at_ms": 1_000,
            "ttl_seconds": 3_600,
            "expires_at_ms": 3_601_000,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        lease_document = {
            **lease_body,
            "body_sha256": "sha256:" + hashlib.sha256(
                exporter._canonical(lease_body)).hexdigest(),
        }
        lease_contents = exporter._canonical(lease_document)
        lease_source.write_bytes(lease_contents)
        lease_source.chmod(0o440)
        exported_lease = exporter.export_lease_receipt(
            lease_source,
            lease_destination,
            "alpha",
            os.geteuid(),
            os.getegid(),
            os.geteuid(),
            os.getegid(),
            require_root=False,
        )
        assert exported_lease == lease_document
        assert lease_destination.read_bytes() == lease_contents
        assert hashlib.sha256(lease_destination.read_bytes()).digest() == (
            hashlib.sha256(lease_contents).digest())
        assert stat.S_IMODE(lease_destination.stat().st_mode) == 0o440
        assert lease_destination.stat().st_uid == os.geteuid()
        assert lease_destination.stat().st_gid == os.getegid()

        binding_reads = json.loads(json.dumps(authoritative_reads))
        binding_reads["market.get_quote"].update({
            "observed_at_ms": 15,
            "stale_after_ms": 2_000,
        })
        binding_body = {
            **v2_body,
            "reads": binding_reads,
        }
        binding_document = {
            **binding_body,
            "body_sha256": "sha256:" + hashlib.sha256(
                exporter._canonical(binding_body)).hexdigest(),
        }
        source.write_bytes(exporter._canonical(binding_document))
        exported_binding_snapshot = exporter.export(
            source,
            destination,
            os.geteuid(),
            os.getegid(),
            os.geteuid(),
            os.getegid(),
            require_root=False,
        )
        assert exported_binding_snapshot == binding_document
        binding_destination = (
            fixture / "export" / "shadow-watch-export-receipt.json")
        binding_receipt = exporter.export_binding_receipt(
            destination,
            lease_destination,
            binding_destination,
            os.geteuid(),
            os.geteuid(),
            os.getegid(),
            exported_at_ms=1_001,
            require_root=False,
        )
        assert set(binding_receipt) == exporter.EXPORT_RECEIPT_FIELDS
        assert binding_receipt["snapshot_body_sha256"] == (
            binding_document["body_sha256"])
        assert binding_receipt["snapshot_file_sha256"] == (
            exporter._digest_bytes(destination.read_bytes()))
        assert binding_receipt["lease_receipt_body_sha256"] == (
            lease_document["body_sha256"])
        assert binding_receipt["lease_receipt_file_sha256"] == (
            exporter._digest_bytes(lease_destination.read_bytes()))
        assert binding_receipt["lease_generation"] == 7
        assert binding_receipt["reader_uid"] == os.geteuid()
        assert binding_receipt["reader_gid"] == os.getegid()
        assert binding_destination.read_bytes() == exporter._canonical(
            binding_receipt)
        assert stat.S_IMODE(binding_destination.stat().st_mode) == 0o440
        assert binding_destination.stat().st_nlink == 1
        assert binding_destination.stat().st_uid == os.geteuid()
        assert binding_destination.stat().st_gid == os.getegid()

        destination.chmod(0o600)
        try:
            exporter.export_binding_receipt(
                destination,
                lease_destination,
                fixture / "unsafe-export-receipt.json",
                os.geteuid(),
                os.geteuid(),
                os.getegid(),
                exported_at_ms=1_001,
                require_root=False,
            )
        except exporter.ExportError as error:
            assert str(error) == (
                "WATCH_BINDING_SNAPSHOT_METADATA_INVALID")
        else:
            raise AssertionError("reader-writable snapshot was bound")
        destination.chmod(0o440)

        bad_lease = dict(lease_document)
        bad_lease["lease_generation"] = 8
        lease_source.chmod(0o640)
        lease_source.write_bytes(exporter._canonical(bad_lease))
        lease_source.chmod(0o440)
        try:
            exporter.export_lease_receipt(
                lease_source,
                lease_destination,
                "alpha",
                os.geteuid(),
                os.getegid(),
                os.geteuid(),
                os.getegid(),
                require_root=False,
            )
        except exporter.ExportError as error:
            assert str(error) == "WATCH_LEASE_EXPORT_CONTRACT_INVALID"
        else:
            raise AssertionError("digest-drifted WATCH lease was exported")
        assert not lease_destination.exists()

        invalid_v2 = dict(v2_document)
        invalid_v2["read_finished_at_ms"] = {
            **v2_document["read_finished_at_ms"],
            "orders.list": 17,
        }
        invalid_v2_body = dict(invalid_v2)
        invalid_v2_body.pop("body_sha256")
        invalid_v2["body_sha256"] = "sha256:" + hashlib.sha256(
            exporter._canonical(invalid_v2_body)).hexdigest()
        source.write_bytes(exporter._canonical(invalid_v2))
        try:
            exporter.export(
                source, destination, os.geteuid(), os.getegid(),
                os.geteuid(), os.getegid(), require_root=False)
        except exporter.ExportError as error:
            assert str(error) == "WATCH_EXPORT_CONTRACT_INVALID"
        else:
            raise AssertionError("invalid v2 read timing was exported")

        document["mutation_attempted"] = True
        source.write_bytes(exporter._canonical(document))
        try:
            exporter.export(
                source, destination, os.geteuid(), os.getegid(),
                os.geteuid(), os.getegid(), require_root=False)
        except exporter.ExportError as error:
            assert str(error) == "WATCH_EXPORT_CONTRACT_INVALID"
        else:
            raise AssertionError("mutation-bearing snapshot was exported")

    with tempfile.TemporaryDirectory(
            prefix="hepta-watch-atomic-export-") as directory:
        fixture = Path(directory)
        source = fixture / "private-snapshot.json"
        lease_source = fixture / "private-lease.json"
        export_directory = fixture / "export"
        destinations = tuple(
            export_directory / name for name in exporter.EXPORT_FILES)
        now_ms = exporter.time.time_ns() // 1_000_000
        atomic_reads = json.loads(json.dumps(authoritative_reads))
        atomic_reads["market.get_quote"].update({
            "observed_at_ms": now_ms - 5,
            "stale_after_ms": now_ms + 600_000,
        })

        def write_snapshot(sample: int) -> dict[str, object]:
            generated = now_ms + sample
            started = generated - 20
            body = {
                "schema": "hepta.shadow-watch-snapshot.v2",
                "version": 2,
                "domain_id": "alpha",
                "agent_uid": os.geteuid(),
                "generated_at_ms": generated,
                "collection_started_at_ms": started,
                "collection_finished_at_ms": generated,
                "read_finished_at_ms": {
                    tool: started + index
                    for index, tool in enumerate(exporter.READ_ORDER, 1)
                },
                "instrument": "EUR.USD",
                "catalog_sha256": "sha256:" + "0" * 64,
                "descriptor_sha256": {},
                "reads": atomic_reads,
                "paper_authorized": False,
                "live_authorized": False,
                "mutation_attempted": False,
                "direct_broker_access": False,
            }
            document = {
                **body,
                "body_sha256": exporter._digest_bytes(
                    exporter._canonical(body)),
            }
            source.write_bytes(exporter._canonical(document))
            source.chmod(0o600)
            return document

        lease_body = {
            "schema": "hepta.shadow-watch-lease-receipt.v1",
            "version": 1,
            "domain_id": "alpha",
            "agent_id": "alpha",
            "agent_uid": os.geteuid(),
            "boundary": "WATCH",
            "operation": "PROVISION",
            "lease_generation": 1,
            "previous_lease_generation": None,
            "previous_receipt_body_sha256": None,
            "accepted": True,
            "reason_code": "OK",
            "accepted_at_ms": now_ms - 1_000,
            "ttl_seconds": 3_600,
            "expires_at_ms": now_ms - 1_000 + 3_600_000,
            "paper_authorized": False,
            "live_authorized": False,
            "mutation_authorized": False,
        }
        lease_document = {
            **lease_body,
            "body_sha256": exporter._digest_bytes(
                exporter._canonical(lease_body)),
        }
        lease_source.write_bytes(exporter._canonical(lease_document))
        lease_source.chmod(0o440)
        write_snapshot(0)
        first = exporter.publish_triplet(
            source,
            destinations[0],
            os.geteuid(),
            os.getegid(),
            os.geteuid(),
            os.getegid(),
            lease_source,
            destinations[1],
            destinations[2],
            require_root=False,
        )
        first_commit = first[3]
        current = export_directory / exporter.COMMIT_NAME
        first_current_contents = current.read_bytes()
        first_generation = (
            export_directory / exporter.GENERATIONS_NAME /
            str(first_commit["generation"]))
        assert first_generation.is_dir()
        assert set(path.name for path in first_generation.iterdir()) == set(
            exporter.EXPORT_FILES)
        assert not any(path.exists() for path in destinations)

        for stage in (
                "after_snapshot", "after_lease", "after_binding",
                "after_generation_fsync", "after_generation_commit"):
            os.environ["HEPTA_SHADOW_EXPORTER_FAULT_STAGE"] = stage
            try:
                exporter.publish_triplet(
                    source,
                    destinations[0],
                    os.geteuid(),
                    os.getegid(),
                    os.geteuid(),
                    os.getegid(),
                    lease_source,
                    destinations[1],
                    destinations[2],
                    require_root=False,
                )
            except exporter.ExportError as error:
                assert str(error).startswith("WATCH_EXPORT_FAULT_")
            else:
                raise AssertionError(f"publish fault {stage} was ignored")
            finally:
                os.environ.pop("HEPTA_SHADOW_EXPORTER_FAULT_STAGE", None)
            assert current.read_bytes() == first_current_contents
            assert first_generation.is_dir()
            assert all(
                (first_generation / name).is_file()
                for name in exporter.EXPORT_FILES)

        os.environ["HEPTA_SHADOW_EXPORTER_FAULT_STAGE"] = (
            "after_pointer_commit")
        try:
            exporter.publish_triplet(
                source,
                destinations[0],
                os.geteuid(),
                os.getegid(),
                os.geteuid(),
                os.getegid(),
                lease_source,
                destinations[1],
                destinations[2],
                require_root=False,
            )
        except exporter.ExportError as error:
            assert str(error) == "WATCH_EXPORT_FAULT_AFTER_POINTER_COMMIT"
        else:
            raise AssertionError("post-pointer crash was ignored")
        finally:
            os.environ.pop("HEPTA_SHADOW_EXPORTER_FAULT_STAGE", None)
        post_pointer = json.loads(current.read_text(encoding="ascii"))
        assert post_pointer["authority_status"] == "ACTIVE"
        assert post_pointer["body_sha256"] != first_commit["body_sha256"]
        assert (
            export_directory / exporter.GENERATIONS_NAME /
            post_pointer["generation"]).is_dir()

        write_snapshot(1)
        rotated = exporter.publish_triplet(
            source,
            destinations[0],
            os.geteuid(),
            os.getegid(),
            os.geteuid(),
            os.getegid(),
            lease_source,
            destinations[1],
            destinations[2],
            require_root=False,
        )[3]
        generation_names = os.listdir(
            export_directory / exporter.GENERATIONS_NAME)
        assert generation_names == [rotated["generation"]]
        assert rotated["commit_sequence"] == (
            post_pointer["commit_sequence"] + 1)
        assert exporter._valid_commit_document(rotated)

        closed_at_ms = exporter.time.time_ns() // 1_000_000
        closed_body = {
            **{
                key: value for key, value in rotated.items()
                if key != "body_sha256"
            },
            "authority_status": "CLOSED",
            "authority_changed_at_ms": closed_at_ms,
            "close_reason": "service-stop",
            "commit_sequence": rotated["commit_sequence"] + 1,
            "generation": None,
            "snapshot_body_sha256": None,
            "snapshot_file_sha256": None,
            "lease_receipt_file_sha256": None,
            "export_receipt_body_sha256": None,
            "export_receipt_file_sha256": None,
            "committed_at_ms": None,
        }
        closed = {
            **closed_body,
            "body_sha256": exporter._digest_bytes(
                exporter._canonical(closed_body)),
        }
        lock_fd = os.open(
            export_directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        started = threading.Event()
        done = threading.Event()
        blocked_errors: list[BaseException] = []

        def blocked_old_writer() -> None:
            started.set()
            try:
                exporter.publish_triplet(
                    source,
                    destinations[0],
                    os.geteuid(),
                    os.getegid(),
                    os.geteuid(),
                    os.getegid(),
                    lease_source,
                    destinations[1],
                    destinations[2],
                    require_root=False,
                )
            except BaseException as error:
                blocked_errors.append(error)
            finally:
                done.set()

        writer = threading.Thread(target=blocked_old_writer)
        writer.start()
        assert started.wait(1)
        assert not done.wait(0.05)
        exporter._reader_publish(
            current,
            exporter._canonical(closed),
            os.getegid(),
            require_root=False,
            prefix=".closed-current-",
        )
        lease_source.unlink()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        writer.join(timeout=2)
        assert not writer.is_alive()
        assert len(blocked_errors) == 1
        assert isinstance(blocked_errors[0], FileNotFoundError)
        assert current.read_bytes() == exporter._canonical(closed)

        # Even a late writer retaining a private copy of the old receipt may
        # not resurrect CLOSED authority.
        lease_source.write_bytes(exporter._canonical(lease_document))
        lease_source.chmod(0o440)
        try:
            exporter.publish_triplet(
                source,
                destinations[0],
                os.geteuid(),
                os.getegid(),
                os.geteuid(),
                os.getegid(),
                lease_source,
                destinations[1],
                destinations[2],
                require_root=False,
            )
        except exporter.ExportError as error:
            assert str(error) == "WATCH_EXPORT_AUTHORITY_ENDED_OR_STALE"
        else:
            raise AssertionError("stale writer resurrected CLOSED authority")
        assert current.read_bytes() == exporter._canonical(closed)

        new_lease_body = {
            **lease_body,
            "accepted_at_ms": closed_at_ms,
            "expires_at_ms": closed_at_ms + lease_body["ttl_seconds"] * 1000,
        }
        new_lease = {
            **new_lease_body,
            "body_sha256": exporter._digest_bytes(
                exporter._canonical(new_lease_body)),
        }
        lease_source.chmod(0o600)
        lease_source.write_bytes(exporter._canonical(new_lease))
        lease_source.chmod(0o440)
        next_campaign = exporter.publish_triplet(
            source,
            destinations[0],
            os.geteuid(),
            os.getegid(),
            os.geteuid(),
            os.getegid(),
            lease_source,
            destinations[1],
            destinations[2],
            require_root=False,
        )[3]
        assert next_campaign["authority_status"] == "ACTIVE"
        assert next_campaign["commit_sequence"] == (
            closed["commit_sequence"] + 1)
        assert next_campaign["lease_receipt_body_sha256"] == (
            new_lease["body_sha256"])

    with tempfile.TemporaryDirectory(
            prefix="hepta-watch-export-symlink-") as directory:
        fixture = Path(directory)
        outside = fixture / "outside"
        outside.mkdir(mode=0o700)
        outside.chmod(0o700)
        export_link = fixture / "export"
        export_link.symlink_to(outside, target_is_directory=True)
        try:
            exporter._ensure_owned_directory(
                export_link,
                owner_uid=os.geteuid(),
                reader_gid=os.getegid(),
                mode=0o750,
            )
        except exporter.ExportError as error:
            assert str(error) == "WATCH_EXPORT_DIRECTORY_UNSAFE"
        else:
            raise AssertionError("export directory symlink was repaired")
        assert stat.S_IMODE(outside.stat().st_mode) == 0o700

        created = fixture / "created"
        displaced = fixture / "displaced"
        real_fchown = exporter.os.fchown

        def rebind_after_open(descriptor: int, uid: int, gid: int) -> None:
            real_fchown(descriptor, uid, gid)
            created.rename(displaced)
            created.mkdir(mode=0o750)
            created.chmod(0o750)

        exporter.os.fchown = rebind_after_open
        try:
            try:
                exporter._ensure_owned_directory(
                    created,
                    owner_uid=os.geteuid(),
                    reader_gid=os.getegid(),
                    mode=0o750,
                )
            except exporter.ExportError as error:
                assert str(error) == "WATCH_EXPORT_DIRECTORY_UNSAFE"
            else:
                raise AssertionError("created export directory rebind passed")
        finally:
            exporter.os.fchown = real_fchown
        assert created.stat().st_ino != displaced.stat().st_ino

    service = (root / "systemd/hepta-shadow-watch-collector@.service").read_text()
    exporter_unit = (root / "systemd/hepta-shadow-watch-export@.service").read_text()
    timer = (root / "systemd/hepta-shadow-watch-collector@.timer").read_text()
    for forbidden in (
            "sudo", "risk.preview", "trade.", "campaignctl",
            "campaign-operator", "kill-switch"):
        assert forbidden not in service.lower()
    for required in (
            "User=hepta-agent-%i", "Group=hepta-agent-%i",
            "SupplementaryGroups=", "PrivateNetwork=yes",
            "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet="):
        assert required in service
    assert "EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env" in service
    assert "uid-${HEPTA_SHADOW_AGENT_UID}.json" in service
    assert "uid-%U.json" not in service
    assert "User=root" in exporter_unit
    assert "CAP_NET" not in exporter_unit
    assert "EnvironmentFile=/etc/heptatrader/trust-domains/%i.shadow-watch.env" in exporter_unit
    assert "--agent-uid 2104" not in exporter_unit
    assert "--reader-uid 1000" not in exporter_unit
    for required in (
            "--lease-receipt-source /run/hepta-agent-%i/sessions/"
            "shadow-watch-lease-receipt.json",
            "--lease-receipt-destination /run/hepta-shadow-watch-export-%i/"
            "shadow-watch-lease-receipt.json",
            "--export-receipt-destination /run/hepta-shadow-watch-export-%i/"
            "shadow-watch-export-receipt.json"):
        assert required in exporter_unit
    assert "0 if require_root else os.geteuid()" in (
        root / "scripts/hepta_shadow_watch_exporter.py").read_text()

    required_arguments = [
        "--source", "/private/snapshot.json",
        "--destination", "/export/snapshot.json",
        "--agent-uid", "2104", "--agent-gid", "2104",
        "--reader-uid", "1000", "--reader-gid", "1000",
    ]
    atomic_arguments = (
        ("--lease-receipt-source", "/private/lease.json"),
        ("--lease-receipt-destination", "/export/lease.json"),
        ("--export-receipt-destination", "/export/receipt.json"),
    )
    original_argv = sys.argv
    try:
        for included in range(0, 7):
            optional = [
                argument
                for index, pair in enumerate(atomic_arguments)
                if included & (1 << index)
                for argument in pair
            ]
            sys.argv = ["hepta-shadow-watch-exporter", *required_arguments,
                        *optional]
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                assert exporter.main() == 78
            assert "WATCH_ATOMIC_EXPORT_ARGUMENTS_REQUIRED" in \
                stderr.getvalue()
    finally:
        sys.argv = original_argv
    assert "Persistent=false" in timer and "[Install]" not in timer
    print("hepta_shadow_watch_collector_tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
