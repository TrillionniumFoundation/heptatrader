#!/usr/bin/env python3

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from typing import Any


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "hepta_shadow_market_history.py"
SPEC = importlib.util.spec_from_file_location(
    "hepta_shadow_market_history",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)
sys.path.insert(0, str(ROOT / "scripts"))
import hepta_market_context_builder as context_builder


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def write(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))
    path.chmod(0o600)


def write_receipt(path: Path, value: Any) -> None:
    if path.exists():
        path.chmod(0o600)
    path.write_bytes(canonical(value))
    path.chmod(0o400)


def lease_receipt(
    generation: int,
    accepted_at_ms: int,
    *,
    operation: str = "PROVISION",
    previous_generation: int | None = None,
    previous_receipt_body_sha256: str | None = None,
    ttl_seconds: int = 3_600,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.shadow-watch-lease-receipt.v1",
        "version": 1,
        "domain_id": "alpha",
        "agent_id": "alpha",
        "agent_uid": 2104,
        "boundary": "WATCH",
        "operation": operation,
        "lease_generation": generation,
        "previous_lease_generation": previous_generation,
        "previous_receipt_body_sha256":
            previous_receipt_body_sha256,
        "accepted": True,
        "reason_code": "OK",
        "accepted_at_ms": accepted_at_ms,
        "ttl_seconds": ttl_seconds,
        "expires_at_ms": accepted_at_ms + ttl_seconds * 1_000,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_authorized": False,
    }
    return {**body, "body_sha256": digest(body)}


def export_receipt(
    snapshot_value: dict[str, Any],
    lease_value: dict[str, Any],
    *,
    exported_at_ms: int | None = None,
) -> dict[str, Any]:
    body = {
        "schema": "hepta.shadow-watch-export-receipt.v1",
        "version": 1,
        "domain_id": snapshot_value["domain_id"],
        "agent_uid": snapshot_value["agent_uid"],
        "reader_uid": os.geteuid(),
        "reader_gid": os.getegid(),
        "boundary": "WATCH_EXPORT",
        "lease_generation": lease_value["lease_generation"],
        "lease_receipt_body_sha256": lease_value["body_sha256"],
        "lease_receipt_file_sha256": digest(lease_value),
        "snapshot_body_sha256": snapshot_value["body_sha256"],
        "snapshot_file_sha256": digest(snapshot_value),
        "snapshot_generated_at_ms": snapshot_value["generated_at_ms"],
        "exported_at_ms": (
            snapshot_value["generated_at_ms"] + 1
            if exported_at_ms is None else exported_at_ms
        ),
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest(body)}


def snapshot(
    started_at_ms: int,
    *,
    price_index: int = 0,
    epoch: str = "epoch-shadow-history-1",
    fencing_generation: int = 7,
    catalog_digit: str = "1",
    descriptor_digit: str = "2",
    quote_observed_at_ms: int | None = None,
    quote_stale_after_ms: int | None = None,
) -> dict[str, Any]:
    read_times = {
        tool: started_at_ms + 50 + index * 50
        for index, tool in enumerate(history.READ_ORDER)
    }
    bid = round(1.10000 + price_index * 0.00001, 8)
    ask = round(bid + 0.00004, 8)
    quote_observed_at_ms = (
        started_at_ms + 200
        if quote_observed_at_ms is None else
        quote_observed_at_ms
    )
    quote_stale_after_ms = (
        quote_observed_at_ms + 5_000
        if quote_stale_after_ms is None else
        quote_stale_after_ms
    )
    reads = {
        "account.get_summary": {
            "source": "SIMULATOR",
            "authoritative": True,
            "account_complete": True,
        },
        "portfolio.list_positions": {
            "source": "SIMULATOR",
            "authoritative": True,
            "positions": [],
        },
        "orders.list": {
            "source": "SIMULATOR",
            "authoritative": True,
            "active_order_ids": [],
        },
        "risk.get_limits": {
            "source": "SIMULATOR",
            "authoritative": True,
            "gross_absolute_position": 0,
        },
        "market.get_quote": {
            "source": "SIMULATOR",
            "authoritative": True,
            "instrument": "EUR.USD",
            "bid": bid,
            "ask": ask,
            "observed_at_ms": quote_observed_at_ms,
            "stale_after_ms": quote_stale_after_ms,
            "stale": False,
        },
        "system.get_health": {
            "gateway_ready": True,
            "remote_execution": True,
            "remote_execution_configured": True,
            "remote_execution_ready": True,
            "execution_mode": "SIMULATOR",
            "execution_service_epoch": epoch,
            "execution_service_fencing_generation": fencing_generation,
            "remote_execution_reason": "",
        },
    }
    body = {
        "schema": "hepta.shadow-watch-snapshot.v2",
        "version": 2,
        "domain_id": "alpha",
        "agent_uid": 2104,
        "collection_started_at_ms": started_at_ms,
        "collection_finished_at_ms": started_at_ms + 400,
        "read_finished_at_ms": read_times,
        "generated_at_ms": started_at_ms + 450,
        "instrument": "EUR.USD",
        "catalog_sha256": "sha256:" + catalog_digit * 64,
        "descriptor_sha256": {
            tool: "sha256:" + descriptor_digit * 64
            for tool in history.READ_ORDER
        },
        "reads": reads,
        "paper_authorized": False,
        "live_authorized": False,
        "mutation_attempted": False,
        "direct_broker_access": False,
    }
    return {**body, "body_sha256": digest(body)}


class ShadowMarketHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="hepta-shadow-market-history-")
        self.root = Path(self.temporary.name)
        self.receipts: dict[
            tuple[Path, int],
            tuple[Path, dict[str, Any]],
        ] = {}
        self.root_trust_uid = history.ROOT_TRUST_UID
        history.ROOT_TRUST_UID = os.geteuid()

    def tearDown(self) -> None:
        history.ROOT_TRUST_UID = self.root_trust_uid
        self.temporary.cleanup()

    def receipt_for(
        self,
        history_directory: Path,
        generation: int,
        started_at_ms: int,
    ) -> tuple[Path, dict[str, Any]]:
        key = (history_directory, generation)
        cached = self.receipts.get(key)
        if cached is not None:
            return cached
        previous_candidates = [
            (candidate_generation, value)
            for (directory, candidate_generation), value
            in self.receipts.items()
            if directory == history_directory and
            candidate_generation < generation
        ]
        if not previous_candidates:
            value = lease_receipt(
                generation,
                started_at_ms - 1_000,
            )
        else:
            previous_generation, (_, previous_value) = max(
                previous_candidates,
                key=lambda item: item[0],
            )
            value = lease_receipt(
                generation,
                started_at_ms - 1_000,
                operation="ROTATE",
                previous_generation=generation - 1,
                previous_receipt_body_sha256=
                    previous_value["body_sha256"],
            )
            self.assertLess(previous_generation, generation)
        path = self.root / (
            f"lease-{history_directory.name}-{generation}.json")
        write_receipt(path, value)
        self.receipts[key] = (path, value)
        return path, value

    def append(
        self,
        history_directory: Path,
        value: dict[str, Any],
        *,
        cadence_ms: int = 10_000,
        maximum_jitter_ms: int = 1_000,
        watch_generation: int = 1,
        receipt_value: dict[str, Any] | None = None,
        receipt_path: Path | None = None,
        export_receipt_value: dict[str, Any] | None = None,
        export_receipt_path: Path | None = None,
        maximum_history_bytes: int =
            history.DEFAULT_MAXIMUM_HISTORY_BYTES,
        minimum_free_bytes: int =
            history.DEFAULT_MINIMUM_FREE_BYTES,
        name: str = "snapshot.json",
        previous_segment_history_directory: Path | None = None,
    ) -> dict[str, Any]:
        source = self.root / name
        write_receipt(source, value)
        if receipt_path is None:
            if receipt_value is None:
                receipt_path, receipt_value = self.receipt_for(
                    history_directory,
                    watch_generation,
                    value["collection_started_at_ms"],
                )
            else:
                receipt_path = self.root / f"receipt-{name}"
                write_receipt(receipt_path, receipt_value)
        if receipt_value is None:
            receipt_value = json.loads(
                receipt_path.read_text(encoding="ascii"))
        if export_receipt_path is None:
            if export_receipt_value is None:
                export_receipt_value = export_receipt(
                    value,
                    receipt_value,
                )
            export_receipt_path = self.root / f"export-receipt-{name}"
            write_receipt(export_receipt_path, export_receipt_value)
        return history.append_snapshot(
            history_directory,
            source,
            cadence_ms=cadence_ms,
            maximum_jitter_ms=maximum_jitter_ms,
            watch_lease_receipt_path=receipt_path,
            watch_export_receipt_path=export_receipt_path,
            maximum_history_bytes=maximum_history_bytes,
            minimum_free_bytes=minimum_free_bytes,
            previous_segment_history_directory=
                previous_segment_history_directory,
        )

    def test_rotated_lease_can_start_segment_only_with_previous_tail(
        self,
    ) -> None:
        first_directory = self.root / "segment-1"
        second_directory = self.root / "segment-2"
        base = 1_800_000_000_000
        first_receipt_path, first_receipt = self.receipt_for(
            first_directory, 1, base)
        self.append(
            first_directory,
            snapshot(base),
            receipt_path=first_receipt_path,
            receipt_value=first_receipt,
            name="segment-1.json",
        )
        rotated = lease_receipt(
            accepted_at_ms=base + 5_000,
            generation=2,
            operation="ROTATE",
            previous_generation=1,
            previous_receipt_body_sha256=first_receipt["body_sha256"],
        )
        rotated_path = self.root / "rotated.json"
        write_receipt(rotated_path, rotated)
        rotated_snapshot = snapshot(base + 20_000, price_index=1)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_SEGMENT_MUST_START_WITH_PROVISION"):
            self.append(
                second_directory,
                rotated_snapshot,
                receipt_path=rotated_path,
                receipt_value=rotated,
                name="segment-2-rejected.json",
            )
        appended = self.append(
            second_directory,
            rotated_snapshot,
            receipt_path=rotated_path,
            receipt_value=rotated,
            name="segment-2.json",
            previous_segment_history_directory=first_directory,
        )
        self.assertEqual(appended["status"], "appended")
        audit = history.audit_history(
            second_directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
            previous_segment_history_directory=first_directory,
        )
        self.assertEqual(audit["record_count"], 1)

    def test_append_is_hash_chained_atomic_and_idempotent(self) -> None:
        directory = self.root / "history"
        base = 1_800_000_000_000
        first = self.append(directory, snapshot(base), name="first.json")
        second_value = snapshot(base + 10_000, price_index=1)
        second = self.append(
            directory,
            second_value,
            name="second.json",
        )
        duplicate = self.append(
            directory,
            second_value,
            name="second.json",
        )
        self.assertEqual(first["status"], "appended")
        self.assertEqual(second["status"], "appended")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(duplicate["record_count"], 2)
        self.assertEqual(first["watch_generation"], 1)
        self.assertRegex(
            first["watch_lease_receipt_body_sha256"],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertRegex(
            first["watch_lease_receipt_file_sha256"],
            r"^sha256:[0-9a-f]{64}$",
        )
        records = history.load_history(
            directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(len(records), 2)
        self.assertIsNone(records[0]["previous_record_sha256"])
        self.assertEqual(
            records[1]["previous_record_sha256"],
            records[0]["record_sha256"],
        )
        for path in directory.iterdir():
            metadata = path.stat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)

    def test_concurrent_duplicate_append_serializes_to_one_record(self) -> None:
        directory = self.root / "concurrent-history"
        source = self.root / "concurrent-snapshot.json"
        started_at_ms = 1_800_000_000_000
        snapshot_value = snapshot(started_at_ms)
        write_receipt(source, snapshot_value)
        receipt_path, receipt_value = self.receipt_for(
            directory,
            1,
            started_at_ms,
        )
        export_path = self.root / "concurrent-export-receipt.json"
        write_receipt(
            export_path,
            export_receipt(snapshot_value, receipt_value),
        )

        def append_once() -> dict[str, Any]:
            return history.append_snapshot(
                directory,
                source,
                cadence_ms=10_000,
                maximum_jitter_ms=1_000,
                watch_lease_receipt_path=receipt_path,
                watch_export_receipt_path=export_path,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: append_once(), range(2)))
        self.assertEqual(
            sorted(result["status"] for result in results),
            ["appended", "duplicate"],
        )
        self.assertEqual(len(list(directory.iterdir())), 2)
        self.assertEqual(
            len(history.load_history(directory, cadence_ms=10_000)),
            1,
        )

    def test_root_snapshot_export_boundary_rejects_unsafe_inputs(self) -> None:
        started_at_ms = 1_800_000_000_000
        snapshot_value = snapshot(started_at_ms)
        lease_value = lease_receipt(1, started_at_ms - 1_000)

        def fixture(label: str) -> tuple[Path, Path, Path]:
            snapshot_path = self.root / f"{label}-snapshot.json"
            lease_path = self.root / f"{label}-lease.json"
            export_path = self.root / f"{label}-export.json"
            write_receipt(snapshot_path, snapshot_value)
            write_receipt(lease_path, lease_value)
            write_receipt(
                export_path,
                export_receipt(snapshot_value, lease_value),
            )
            return snapshot_path, lease_path, export_path

        def append_paths(
            label: str,
            snapshot_path: Path,
            lease_path: Path,
            export_path: Path,
        ) -> dict[str, Any]:
            return history.append_snapshot(
                self.root / f"{label}-history",
                snapshot_path,
                cadence_ms=10_000,
                watch_lease_receipt_path=lease_path,
                watch_export_receipt_path=export_path,
                maximum_jitter_ms=1_000,
            )

        snapshot_path, lease_path, export_path = fixture("mode")
        snapshot_path.chmod(0o600)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_SNAPSHOT_METADATA_INVALID"):
            append_paths("mode", snapshot_path, lease_path, export_path)

        snapshot_path, lease_path, export_path = fixture("owner")
        original_uid = history.ROOT_TRUST_UID
        history.ROOT_TRUST_UID = os.geteuid() + 1
        try:
            with self.assertRaisesRegex(
                    history.HistoryError,
                    "MARKET_HISTORY_SNAPSHOT_METADATA_INVALID"):
                append_paths("owner", snapshot_path, lease_path, export_path)
        finally:
            history.ROOT_TRUST_UID = original_uid

        target_path, lease_path, export_path = fixture("symlink")
        symlink_path = self.root / "symlink-snapshot-link.json"
        symlink_path.symlink_to(target_path)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_SNAPSHOT_READ_FAILED"):
            append_paths("symlink", symlink_path, lease_path, export_path)

        snapshot_path, lease_path, export_path = fixture("hardlink")
        hardlink_path = self.root / "hardlink-snapshot-link.json"
        os.link(snapshot_path, hardlink_path)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_SNAPSHOT_METADATA_INVALID"):
            append_paths("hardlink", snapshot_path, lease_path, export_path)

        snapshot_path, lease_path, export_path = fixture("toctou")
        real_fstat = history.os.fstat
        calls = 0

        def drift_fstat(descriptor: int) -> Any:
            nonlocal calls
            calls += 1
            metadata = real_fstat(descriptor)
            if calls != 2:
                return metadata
            return SimpleNamespace(
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino,
                st_mode=metadata.st_mode,
                st_nlink=metadata.st_nlink,
                st_uid=metadata.st_uid,
                st_gid=metadata.st_gid,
                st_size=metadata.st_size + 1,
                st_mtime_ns=metadata.st_mtime_ns,
                st_ctime_ns=metadata.st_ctime_ns,
            )

        with mock.patch.object(history.os, "fstat", side_effect=drift_fstat):
            with self.assertRaisesRegex(
                    history.HistoryError,
                    "MARKET_HISTORY_SNAPSHOT_METADATA_INVALID"):
                append_paths(
                    "toctou", snapshot_path, lease_path, export_path)

        snapshot_path, lease_path, export_path = fixture("fabricated")
        fabricated_snapshot = snapshot(
            started_at_ms,
            price_index=1,
        )
        write_receipt(
            export_path,
            export_receipt(fabricated_snapshot, lease_value),
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_EXPORT_RECEIPT_BINDING_INVALID"):
            append_paths(
                "fabricated", snapshot_path, lease_path, export_path)

        snapshot_path, lease_path, export_path = fixture("receipt-mode")
        export_path.chmod(0o600)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_EXPORT_RECEIPT_METADATA_INVALID"):
            append_paths(
                "receipt-mode", snapshot_path, lease_path, export_path)

    def test_gap_and_authority_binding_drift_fail_closed(self) -> None:
        base = 1_800_000_000_000
        variants = (
            (
                "gap",
                snapshot(base + 30_000, price_index=1),
                "MARKET_HISTORY_CADENCE_GAP",
            ),
            (
                "epoch",
                snapshot(
                    base + 10_000,
                    price_index=1,
                    epoch="epoch-shadow-history-2",
                ),
                "MARKET_HISTORY_AUTHORITY_BINDING_DRIFT",
            ),
            (
                "fence",
                snapshot(
                    base + 10_000,
                    price_index=1,
                    fencing_generation=8,
                ),
                "MARKET_HISTORY_AUTHORITY_BINDING_DRIFT",
            ),
            (
                "catalog",
                snapshot(
                    base + 10_000,
                    price_index=1,
                    catalog_digit="3",
                ),
                "MARKET_HISTORY_AUTHORITY_BINDING_DRIFT",
            ),
            (
                "descriptor",
                snapshot(
                    base + 10_000,
                    price_index=1,
                    descriptor_digit="4",
                ),
                "MARKET_HISTORY_AUTHORITY_BINDING_DRIFT",
            ),
        )
        for label, candidate, reason in variants:
            with self.subTest(label=label):
                directory = self.root / f"history-{label}"
                self.append(
                    directory,
                    snapshot(base),
                    name=f"{label}-first.json",
                )
                with self.assertRaisesRegex(history.HistoryError, reason):
                    self.append(
                        directory,
                        candidate,
                        name=f"{label}-candidate.json",
                    )
                self.assertEqual(len(list(directory.iterdir())), 2)

    def test_watch_generation_is_monotonic_and_bound_to_duplicates(self) -> None:
        directory = self.root / "watch-generation-history"
        base = 1_800_000_000_000
        first_value = snapshot(base)
        self.append(
            directory,
            first_value,
            watch_generation=4,
            name="watch-generation-first.json",
        )
        self.append(
            directory,
            snapshot(base + 10_000, price_index=1),
            watch_generation=5,
            name="watch-generation-second.json",
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "WATCH_GENERATION_DRIFT"):
            self.append(
                directory,
                snapshot(base + 20_000, price_index=2),
                watch_generation=4,
                name="watch-generation-regression.json",
            )
        with self.assertRaisesRegex(
                history.HistoryError,
                "TIME_NOT_MONOTONIC"):
            self.append(
                directory,
                first_value,
                watch_generation=6,
                name="watch-generation-first.json",
            )
        with self.assertRaisesRegex(
                history.HistoryError,
                "WATCH_GENERATION_DRIFT"):
            self.append(
                directory,
                snapshot(base + 30_000, price_index=3),
                watch_generation=7,
                name="watch-generation-skipped.json",
            )

    def test_receipt_is_root_owned_exact_digest_bound_and_fresh(self) -> None:
        base = 1_800_000_000_000
        valid = lease_receipt(1, base - 1_000)
        forged = dict(valid)
        forged["token"] = "forbidden"
        forged_body = dict(forged)
        forged_body.pop("body_sha256")
        forged["body_sha256"] = digest(forged_body)
        tampered = dict(valid)
        tampered["body_sha256"] = "sha256:" + "0" * 64
        stale = lease_receipt(
            1,
            base - 2_000,
            ttl_seconds=1,
        )
        cases = (
            ("extension", forged, "LEASE_RECEIPT_FIELDS_INVALID"),
            ("tampered", tampered, "LEASE_RECEIPT_DIGEST_INVALID"),
            ("stale", stale, "LEASE_RECEIPT_STALE"),
        )
        for label, receipt_value, reason in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(history.HistoryError, reason):
                    self.append(
                        self.root / f"receipt-{label}-history",
                        snapshot(base),
                        receipt_value=receipt_value,
                        name=f"receipt-{label}-snapshot.json",
                    )

        receipt_path = self.root / "untrusted-owner-receipt.json"
        write_receipt(receipt_path, valid)
        receipt_path.chmod(0o600)
        with self.assertRaisesRegex(
                history.HistoryError,
                "LEASE_RECEIPT_METADATA_INVALID"):
            self.append(
                self.root / "untrusted-owner-history",
                snapshot(base),
                receipt_path=receipt_path,
                name="untrusted-owner-snapshot.json",
            )

    def test_rotate_receipt_chain_and_gap_are_fail_closed(self) -> None:
        base = 1_800_000_000_000
        bad_chain_directory = self.root / "bad-rotate-chain-history"
        self.append(
            bad_chain_directory,
            snapshot(base),
            name="bad-rotate-first.json",
        )
        bad_rotate = lease_receipt(
            2,
            base + 9_000,
            operation="ROTATE",
            previous_generation=1,
            previous_receipt_body_sha256="sha256:" + "f" * 64,
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "ROTATION_CHAIN_INVALID"):
            self.append(
                bad_chain_directory,
                snapshot(base + 10_000, price_index=1),
                watch_generation=2,
                receipt_value=bad_rotate,
                name="bad-rotate-second.json",
            )

        gap_directory = self.root / "rotate-gap-history"
        self.append(
            gap_directory,
            snapshot(base),
            name="rotate-gap-first.json",
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "ROTATION_SEGMENT_REQUIRED"):
            self.append(
                gap_directory,
                snapshot(base + 15_001, price_index=1),
                watch_generation=2,
                name="rotate-gap-second.json",
            )
        self.assertEqual(len(list(gap_directory.iterdir())), 2)

    def test_rotation_jitter_never_completes_an_incomplete_bar(self) -> None:
        directory = self.root / "rotation-incomplete-history"
        base = 1_800_000_000_000
        starts = (0, 10_000, 20_000)
        for index, offset in enumerate(starts):
            self.append(
                directory,
                snapshot(base + offset, price_index=index),
                name=f"rotation-before-{index}.json",
            )
        self.append(
            directory,
            snapshot(base + 35_000, price_index=3),
            watch_generation=2,
            name="rotation-point.json",
        )
        for index, offset in enumerate((45_000, 55_000, 65_000), start=4):
            self.append(
                directory,
                snapshot(base + offset, price_index=index),
                watch_generation=2,
                name=f"rotation-after-{index}.json",
            )
        document = history.materialize_bars(
            directory,
            self.root / "rotation-incomplete-bars.json",
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        first = document["one_minute_bars"][0]
        self.assertFalse(first["complete"])
        self.assertIn("CAPTURE_GAP_EXCEEDED", first["reason_codes"])

    def test_history_quota_and_free_space_guards_fail_before_publish(
            self) -> None:
        base = 1_800_000_000_000
        quota_directory = self.root / "quota-history"
        with self.assertRaisesRegex(
                history.HistoryError,
                "BYTE_QUOTA_EXCEEDED"):
            self.append(
                quota_directory,
                snapshot(base),
                maximum_history_bytes=1,
                minimum_free_bytes=0,
                name="quota-snapshot.json",
            )
        self.assertEqual(len(list(quota_directory.iterdir())), 0)

        free_directory = self.root / "free-space-history"
        fake_statvfs = SimpleNamespace(f_bavail=0, f_frsize=4_096)
        with mock.patch.object(
                history.os,
                "statvfs",
                return_value=fake_statvfs):
            with self.assertRaisesRegex(
                    history.HistoryError,
                    "FREE_SPACE_GUARD"):
                self.append(
                    free_directory,
                    snapshot(base),
                    maximum_history_bytes=1_000_000,
                    minimum_free_bytes=1,
                    name="free-space-snapshot.json",
                )
        self.assertEqual(len(list(free_directory.iterdir())), 0)

    def test_crash_recovery_rebuilds_head_after_full_chain_audit(self) -> None:
        directory = self.root / "recovery-history"
        base = 1_800_000_000_000
        self.append(
            directory,
            snapshot(base),
            name="recovery-first.json",
        )
        with mock.patch.object(
                history,
                "_atomic_replace_head",
                side_effect=history.HistoryError(
                    "INJECTED_HEAD_COMMIT_FAILURE")):
            with self.assertRaisesRegex(
                    history.HistoryError,
                    "INJECTED_HEAD_COMMIT_FAILURE"):
                self.append(
                    directory,
                    snapshot(base + 10_000, price_index=1),
                    name="recovery-second.json",
                )
        with self.assertRaisesRegex(
                history.HistoryError,
                "HEAD_RECOVERY_REQUIRED"):
            self.append(
                directory,
                snapshot(base + 10_000, price_index=1),
                name="recovery-second.json",
            )
        recovered = history.recover_history_head(
            directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(recovered["record_count"], 2)
        duplicate = self.append(
            directory,
            snapshot(base + 10_000, price_index=1),
            name="recovery-second.json",
        )
        self.assertEqual(duplicate["status"], "duplicate")
        audit = history.audit_history(
            directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(audit["status"], "valid")
        self.assertEqual(audit["record_count"], 2)

    def test_legacy_directory_requires_new_provision_segment(self) -> None:
        directory = self.root / "legacy-migration-history"
        base = 1_800_000_000_000
        first = self.append(
            directory,
            snapshot(base),
            name="legacy-migration-first.json",
        )
        current = directory / history._record_name(1)
        legacy = directory / (
            "record-00000000000000000001-" +
            first["record_sha256"].removeprefix("sha256:") +
            ".json"
        )
        current.rename(legacy)
        with self.assertRaisesRegex(
                history.HistoryError,
                "LEGACY_SEGMENT_MIGRATION_REQUIRED"):
            history.recover_history_head(
                directory,
                cadence_ms=10_000,
                maximum_jitter_ms=1_000,
            )

    def test_incremental_checkpoint_detects_cursor_record_tamper(
            self) -> None:
        directory = self.root / "incremental-tamper-history"
        base = 1_800_000_000_000
        for index in range(4):
            self.append(
                directory,
                snapshot(base + index * 10_000, price_index=index),
                name=f"incremental-tamper-{index}.json",
            )
        head = json.loads(
            (directory / history.HEAD_NAME).read_text(encoding="ascii"))
        cursor_path = directory / history._record_name(
            head["audit_cursor_sequence"])
        record = json.loads(cursor_path.read_text(encoding="ascii"))
        record["quote"]["bid"] += 0.00001
        cursor_path.write_bytes(canonical(record))
        cursor_path.chmod(0o600)
        with self.assertRaisesRegex(
                history.HistoryError,
                "RECORD_DIGEST_INVALID"):
            self.append(
                directory,
                snapshot(base + 40_000, price_index=4),
                name="incremental-tamper-next.json",
            )

    def test_rolling_output_and_hot_append_work_are_bounded(self) -> None:
        directory = self.root / "bounded-soak-history"
        base = 1_800_000_000_000
        limit = history._materialization_record_limit(
            10_000,
            history.DEFAULT_MATERIALIZATION_WINDOW_MS,
        )
        first_output = self.root / "bounded-first-bars.json"
        first_quotes = self.root / "bounded-first-quotes.json"
        first_bars = self.root / "bounded-first-5m.json"
        final_count = limit + 200
        with mock.patch.object(history.os, "fsync", return_value=None):
            for index in range(final_count):
                result = self.append(
                    directory,
                    snapshot(base + index * 10_000, price_index=index),
                    watch_generation=1 + index // 300,
                    name="bounded-soak-snapshot.json",
                )
                complexity = result["complexity"]
                self.assertEqual(complexity["directory_entries_scanned"], 0)
                self.assertLessEqual(complexity["history_head_reads"], 1)
                self.assertLessEqual(complexity["history_record_reads"], 3)
                self.assertLessEqual(
                    complexity["checkpoint_records_validated"],
                    history.INCREMENTAL_AUDIT_RECORDS,
                )
                if index + 1 == limit:
                    history.materialize_bars(
                        directory,
                        first_output,
                        cadence_ms=10_000,
                        quote_history_output=first_quotes,
                        bar_history_output=first_bars,
                    )
        final_output = self.root / "bounded-final-bars.json"
        final_quotes = self.root / "bounded-final-quotes.json"
        final_bars = self.root / "bounded-final-5m.json"
        final_document = history.materialize_bars(
            directory,
            final_output,
            cadence_ms=10_000,
            quote_history_output=final_quotes,
            bar_history_output=final_bars,
        )
        first_quote_document = json.loads(
            first_quotes.read_text(encoding="ascii"))
        final_quote_document = json.loads(
            final_quotes.read_text(encoding="ascii"))
        self.assertEqual(len(first_quote_document["quotes"]), limit)
        self.assertEqual(len(final_quote_document["quotes"]), limit)
        self.assertEqual(
            final_document["source_total_record_count"],
            final_count,
        )
        self.assertEqual(final_document["source_record_count"], limit)
        self.assertTrue(final_document["source_window_truncated"])
        self.assertRegex(
            final_document["source_predecessor_record_sha256"],
            r"^sha256:[0-9a-f]{64}$",
        )
        self.assertLess(
            abs(final_output.stat().st_size - first_output.stat().st_size),
            16_384,
        )
        self.assertLess(
            abs(final_quotes.stat().st_size - first_quotes.stat().st_size),
            16_384,
        )

    def test_snapshot_contract_rejects_legacy_stale_and_mutation(self) -> None:
        base = 1_800_000_000_000
        cases: list[tuple[str, dict[str, Any], str]] = []
        legacy = snapshot(base)
        legacy["schema"] = "hepta.shadow-watch-snapshot.v1"
        legacy["version"] = 1
        legacy_body = dict(legacy)
        legacy_body.pop("body_sha256")
        legacy["body_sha256"] = digest(legacy_body)
        cases.append(("legacy", legacy, "SNAPSHOT_SCHEMA_INVALID"))
        stale = snapshot(base)
        stale["reads"]["market.get_quote"]["stale"] = True
        stale_body = dict(stale)
        stale_body.pop("body_sha256")
        stale["body_sha256"] = digest(stale_body)
        cases.append(("stale", stale, "QUOTE_NOT_AUTHORITATIVE"))
        mutation = snapshot(base)
        mutation["mutation_attempted"] = True
        mutation_body = dict(mutation)
        mutation_body.pop("body_sha256")
        mutation["body_sha256"] = digest(mutation_body)
        cases.append(("mutation", mutation, "SNAPSHOT_BOUNDARY_INVALID"))
        for label, value, reason in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(history.HistoryError, reason):
                    self.append(
                        self.root / f"invalid-{label}",
                        value,
                        name=f"invalid-{label}.json",
                    )

    def test_cadence_bounds_are_strict(self) -> None:
        base = 1_800_000_000_000
        for cadence in (4_999, 15_001):
            with self.subTest(cadence=cadence):
                with self.assertRaisesRegex(
                        history.HistoryError,
                        "MARKET_HISTORY_CADENCE_INVALID"):
                    self.append(
                        self.root / f"invalid-cadence-{cadence}",
                        snapshot(base),
                        cadence_ms=cadence,
                        name=f"invalid-cadence-{cadence}.json",
                    )
        for cadence in (5_000, 15_000):
            with self.subTest(cadence=cadence):
                directory = self.root / f"valid-cadence-{cadence}"
                self.append(
                    directory,
                    snapshot(base),
                    cadence_ms=cadence,
                    name=f"valid-cadence-{cadence}-first.json",
                )
                self.append(
                    directory,
                    snapshot(base + cadence, price_index=1),
                    cadence_ms=cadence,
                    name=f"valid-cadence-{cadence}-second.json",
                )

    def test_quote_phase_sweep_accepts_only_identical_fresh_repeats(
        self,
    ) -> None:
        base = 1_800_000_000_000
        for duplicate_position in range(1, 5):
            with self.subTest(duplicate_position=duplicate_position):
                directory = self.root / f"phase-{duplicate_position}"
                for capture_index in range(6):
                    capture_started_at_ms = (
                        base + capture_index * 10_000 +
                        (1_000 if capture_index >= duplicate_position else 0)
                    )
                    quote_index = (
                        capture_index
                        if capture_index < duplicate_position else
                        capture_index - 1
                    )
                    observed = base + quote_index * 10_000 + 200
                    self.append(
                        directory,
                        snapshot(
                            capture_started_at_ms,
                            price_index=quote_index,
                            quote_observed_at_ms=observed,
                            quote_stale_after_ms=observed + 30_000,
                        ),
                        name=(
                            f"phase-{duplicate_position}-"
                            f"{capture_index}.json"
                        ),
                    )
                records = history.load_history(
                    directory,
                    cadence_ms=10_000,
                    maximum_jitter_ms=1_000,
                )
                self.assertEqual(
                    [record["quote_changed"] for record in records],
                    [
                        index != duplicate_position
                        for index in range(6)
                    ],
                )
                quote_document = history._builder_quote_history(
                    records,
                    cadence_ms=10_000,
                )
                self.assertEqual(quote_document["maximum_gap_ms"], 11_000)
                config = json.loads((
                    ROOT /
                    "strategies/eurusd-confirmed-momentum-shadow-v2.json"
                ).read_text(encoding="utf-8"))
                config["feature_windows"]["quote_lookback_seconds"] = 30
                timeline, metadata = context_builder._validate_quotes(
                    quote_document,
                    config,
                    records[-1]["generated_at_ms"],
                )
                independent = context_builder._independent_quotes(
                    timeline, metadata)
                self.assertEqual(len(timeline), 6)
                self.assertEqual(len(independent), 5)
                self.assertEqual(metadata["independent_quote_count"], 5)
                if duplicate_position == 2:
                    legacy = json.loads(json.dumps(quote_document))
                    legacy["schema"] = (
                        "hepta.authoritative-quote-history.v2")
                    legacy["version"] = 2
                    legacy.pop("source_window_truncated")
                    for quote in legacy["quotes"]:
                        quote.pop("captured_at_ms")
                        quote.pop("quote_changed")
                    legacy_body = dict(legacy)
                    legacy_body.pop("body_sha256")
                    legacy["body_sha256"] = digest(legacy_body)
                    with self.assertRaisesRegex(
                            context_builder.ContractError,
                            "CONTEXT_QUOTE_TIME_INVALID"):
                        context_builder._validate_quotes(
                            legacy,
                            config,
                            records[-1]["generated_at_ms"],
                        )

                    changed = json.loads(json.dumps(quote_document))
                    changed["quotes"][duplicate_position][
                        "quote_changed"] = True
                    changed_body = dict(changed)
                    changed_body.pop("body_sha256")
                    changed["body_sha256"] = digest(changed_body)
                    with self.assertRaisesRegex(
                            context_builder.ContractError,
                            "CONTEXT_QUOTE_MUTATION"):
                        context_builder._validate_quotes(
                            changed,
                            config,
                            records[-1]["generated_at_ms"],
                        )

    def test_same_timestamp_mutation_unique_gap_and_stale_crossing_fail(
        self,
    ) -> None:
        base = 1_800_000_000_000
        observed = base + 200

        mutation_directory = self.root / "quote-mutation"
        self.append(
            mutation_directory,
            snapshot(
                base,
                quote_observed_at_ms=observed,
                quote_stale_after_ms=observed + 30_000,
            ),
            name="quote-mutation-first.json",
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_QUOTE_MUTATION"):
            self.append(
                mutation_directory,
                snapshot(
                    base + 10_000,
                    price_index=1,
                    quote_observed_at_ms=observed,
                    quote_stale_after_ms=observed + 30_000,
                ),
                name="quote-mutation-second.json",
            )

        gap_directory = self.root / "unique-quote-gap"
        for capture_index, quote_index in ((0, 0), (1, 0)):
            quote_time = base + quote_index * 10_000 + 200
            self.append(
                gap_directory,
                snapshot(
                    base + capture_index * 10_000,
                    price_index=quote_index,
                    quote_observed_at_ms=quote_time,
                    quote_stale_after_ms=quote_time + 30_000,
                ),
                name=f"unique-gap-{capture_index}.json",
            )
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_QUOTE_GAP"):
            self.append(
                gap_directory,
                snapshot(
                    base + 20_000,
                    price_index=2,
                    quote_observed_at_ms=base + 20_200,
                    quote_stale_after_ms=base + 50_200,
                ),
                name="unique-gap-2.json",
            )

        stale_directory = self.root / "stale-repeat"
        self.append(
            stale_directory,
            snapshot(
                base,
                quote_observed_at_ms=observed,
                quote_stale_after_ms=observed + 5_000,
            ),
            name="stale-repeat-first.json",
        )
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_QUOTE_STALE_AT_READ"):
            self.append(
                stale_directory,
                snapshot(
                    base + 10_000,
                    quote_observed_at_ms=observed,
                    quote_stale_after_ms=observed + 5_000,
                ),
                name="stale-repeat-second.json",
            )

    def test_changed_quote_accepts_exact_fifteen_second_boundary(
        self,
    ) -> None:
        base = 1_800_000_000_000
        directory = self.root / "exact-quote-gap"
        receipt = lease_receipt(1, base - 10_000)
        receipt_path = self.root / "exact-quote-gap-lease.json"
        write_receipt(receipt_path, receipt)
        first_observed = base - 5_000
        self.append(
            directory,
            snapshot(
                base,
                quote_observed_at_ms=first_observed,
                quote_stale_after_ms=first_observed + 60_000,
            ),
            receipt_value=receipt,
            receipt_path=receipt_path,
            name="exact-quote-gap-first.json",
        )
        second_observed = first_observed + 15_000
        self.append(
            directory,
            snapshot(
                base + 10_000,
                price_index=1,
                quote_observed_at_ms=second_observed,
                quote_stale_after_ms=second_observed + 60_000,
            ),
            receipt_value=receipt,
            receipt_path=receipt_path,
            name="exact-quote-gap-second.json",
        )
        records = history.load_history(
            directory,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(
            records[1]["quote"]["observed_at_ms"] -
            records[0]["quote"]["observed_at_ms"],
            history.BUILDER_MAXIMUM_QUOTE_GAP_MS,
        )
        document = history._builder_quote_history(
            records,
            cadence_ms=10_000,
        )
        self.assertEqual(
            document["maximum_gap_ms"],
            history.BUILDER_MAXIMUM_QUOTE_GAP_MS,
        )

    def test_twenty_second_capture_skip_and_legacy_v2_head_fail_closed(
        self,
    ) -> None:
        base = 1_800_000_000_000
        skip_directory = self.root / "capture-skip"
        self.append(skip_directory, snapshot(base), name="skip-first.json")
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_CADENCE_GAP"):
            self.append(
                skip_directory,
                snapshot(base + 20_000, price_index=1),
                name="skip-second.json",
            )

        legacy_directory = self.root / "legacy-v2-head"
        self.append(legacy_directory, snapshot(base), name="legacy-first.json")
        head_path = legacy_directory / history.HEAD_NAME
        head = json.loads(head_path.read_text(encoding="ascii"))
        head["record_schema"] = "hepta.shadow-market-history-record.v2"
        head_body = dict(head)
        head_body.pop("body_sha256")
        head["body_sha256"] = digest(head_body)
        write(head_path, head)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_HEAD_SCHEMA_INVALID"):
            self.append(
                legacy_directory,
                snapshot(base + 10_000, price_index=1),
                name="legacy-second.json",
            )

    def test_materializes_deterministic_closed_one_and_five_minute_bars(
            self) -> None:
        directory = self.root / "materialize-history"
        base = 1_800_000_000_000
        for index in range(61):
            self.append(
                directory,
                snapshot(
                    base + index * 10_000,
                    price_index=index,
                ),
                name=f"materialize-{index:03d}.json",
            )
        output = self.root / "bars.json"
        quote_output = self.root / "quote-history-v2.json"
        bar_output = self.root / "bar-history-v2.json"
        document = history.materialize_bars(
            directory,
            output,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
            quote_history_output=quote_output,
            bar_history_output=bar_output,
        )
        first_bytes = output.read_bytes()
        repeated = history.materialize_bars(
            directory,
            output,
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
            quote_history_output=quote_output,
            bar_history_output=bar_output,
        )
        self.assertEqual(first_bytes, output.read_bytes())
        self.assertEqual(document, repeated)
        self.assertEqual(len(document["one_minute_bars"]), 10)
        self.assertEqual(len(document["five_minute_bars"]), 2)
        self.assertTrue(all(
            bar["complete"] is True
            for bar in document["one_minute_bars"]
        ))
        self.assertTrue(all(
            bar["complete"] is True
            for bar in document["five_minute_bars"]
        ))
        first = document["one_minute_bars"][0]
        self.assertEqual(first["sample_count"], 6)
        self.assertEqual(first["expected_sample_count"], 6)
        self.assertEqual(first["coverage_ppm"], 1_000_000)
        self.assertEqual(first["source_count"], 6)
        self.assertRegex(first["source_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(first["bar_sha256"], r"^sha256:[0-9a-f]{64}$")
        five = document["five_minute_bars"][0]
        self.assertEqual(five["source_kind"], "ONE_MINUTE_BARS")
        self.assertEqual(five["source_count"], 5)
        self.assertEqual(five["sample_count"], 30)
        body = dict(document)
        claimed = body.pop("body_sha256")
        self.assertEqual(claimed, digest(body))
        self.assertEqual(
            stat.S_IMODE(output.stat().st_mode),
            0o600,
        )
        quote_document = json.loads(quote_output.read_text(encoding="ascii"))
        bar_document = json.loads(bar_output.read_text(encoding="ascii"))
        self.assertEqual(
            quote_document["schema"],
            "hepta.authoritative-quote-history.v3",
        )
        self.assertEqual(quote_document["version"], 3)
        self.assertFalse(quote_document["source_window_truncated"])
        self.assertTrue(all(
            quote["quote_changed"] is True
            for quote in quote_document["quotes"]
        ))
        self.assertEqual(
            bar_document["schema"],
            "hepta.authoritative-bar-history.v2",
        )
        self.assertEqual(quote_document["quotes"][-1]["watch_generation"], 1)
        self.assertEqual(bar_document["interval_ms"], 300_000)
        self.assertTrue(bar_document["complete"])
        config = json.loads(
            (ROOT / "strategies" /
             "eurusd-confirmed-momentum-shadow-v2.json").read_text(
                 encoding="utf-8"))
        config["feature_windows"]["quote_lookback_seconds"] = 600
        evaluated_at_ms = base + 600_450
        quotes, quote_metadata = context_builder._validate_quotes(
            quote_document,
            config,
            evaluated_at_ms,
        )
        bars, bar_metadata = context_builder._validate_bars(
            bar_document,
            config,
            evaluated_at_ms,
        )
        self.assertEqual(len(quotes), 61)
        self.assertTrue(quote_metadata["provenance_provable"])
        self.assertEqual(len(bars), 2)
        self.assertTrue(bar_metadata["provenance_provable"])

    def test_partial_boundary_is_explicitly_incomplete(self) -> None:
        directory = self.root / "partial-history"
        base = 1_800_000_000_000
        for index in range(4):
            self.append(
                directory,
                snapshot(
                    base + 30_000 + index * 10_000,
                    price_index=index,
                ),
                name=f"partial-{index}.json",
            )
        document = history.materialize_bars(
            directory,
            self.root / "partial-bars.json",
            cadence_ms=10_000,
            maximum_jitter_ms=1_000,
        )
        self.assertEqual(len(document["one_minute_bars"]), 1)
        bar = document["one_minute_bars"][0]
        self.assertFalse(bar["complete"])
        self.assertIn("START_BOUNDARY_UNCOVERED", bar["reason_codes"])
        self.assertIn("INSUFFICIENT_SAMPLES", bar["reason_codes"])

    def test_prior_record_tamper_breaks_chain_validation(self) -> None:
        directory = self.root / "tamper-history"
        base = 1_800_000_000_000
        self.append(directory, snapshot(base), name="tamper-first.json")
        self.append(
            directory,
            snapshot(base + 10_000, price_index=1),
            name="tamper-second.json",
        )
        first_path = directory / history._record_name(1)
        document = json.loads(first_path.read_text(encoding="ascii"))
        document["quote"]["bid"] += 0.00001
        first_path.write_bytes(canonical(document))
        first_path.chmod(0o600)
        with self.assertRaisesRegex(
                history.HistoryError,
                "MARKET_HISTORY_RECORD_DIGEST_INVALID"):
            history.load_history(
                directory,
                cadence_ms=10_000,
                maximum_jitter_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
