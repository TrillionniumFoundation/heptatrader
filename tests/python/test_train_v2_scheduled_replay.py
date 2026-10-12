"""Exact original prefix audit after append-only price source continues."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from laya_cell.shadow_source import (
    SCHEMA as SOURCE_SCHEMA,
    read_source, canonical, digest as source_digest,
)
from train_v2.contracts import ContractError
from train_v2.scheduled_replay import recover_original_prefix
from test_train_v2_phase2 import OriginalSourceFixture


class CapturedPrefixReplayTests(OriginalSourceFixture):
    def test_original_complete_prefix_reproduced_exactly(self):
        header, quotes, receipt = read_source(self.path)
        recovered = recover_original_prefix(
            self.path, receipt["chain_head"], receipt["sha256"])
        self.assertEqual(recovered, self.path.read_bytes())
        self.assertEqual(hashlib.sha256(recovered).hexdigest(),
                         receipt["sha256"])
        self.assertEqual(len(quotes),7)

    def test_later_valid_append_does_not_modify_original_prefix(self):
        _, _, original = read_source(self.path)
        previous = original["chain_head"]
        record = {
            "schema":SOURCE_SCHEMA, "sequence":8, "previous":previous,
            "at_ms":self.now, "kind":"END",
            "data":{"broker_orders_sent":0},
        }
        record["sha256"] = source_digest(record)
        original_raw=self.path.read_bytes()
        with self.path.open("ab") as stream:
            stream.write(canonical(record)+b"\n")
        _, _, extended = read_source(self.path)
        self.assertNotEqual(original["sha256"],extended["sha256"])
        restored = recover_original_prefix(
            self.path, original["chain_head"], original["sha256"])
        self.assertEqual(restored,original_raw)
        self.assertNotIn(b'"kind":"END"',restored)

    def test_altered_original_price_prefix_hash_is_rejected(self):
        _, _, original = read_source(self.path)
        raw = self.path.read_bytes()
        self.assertIn(b"1.10002",raw)
        self.path.write_bytes(raw.replace(b"1.10002",b"1.10003",1))
        with self.assertRaisesRegex(
                ContractError,"REPLAY_ORIGINAL_PREFIX_BYTES_CHANGED"):
            recover_original_prefix(
                self.path, original["chain_head"], original["sha256"])

    def test_missing_original_event_head_not_interpreted_as_new_source(self):
        _, _, receipt = read_source(self.path)
        with self.assertRaisesRegex(
                ContractError,"REPLAY_ORIGINAL_CHAIN_HEAD_MISSING"):
            recover_original_prefix(self.path,"c"*64,receipt["sha256"])

    def test_symlink_original_source_rejected(self):
        _,_,receipt=read_source(self.path)
        symlink=self.root/"source-link.jsonl"
        symlink.symlink_to(self.path)
        with self.assertRaisesRegex(
                ContractError,"REPLAY_ORIGINAL_SOURCE_UNSAFE"):
            recover_original_prefix(
                symlink,receipt["chain_head"],receipt["sha256"])

    def test_truncated_original_prefix_not_rescued_from_suffix(self):
        _,_,receipt=read_source(self.path)
        lines=self.path.read_bytes().splitlines(keepends=True)
        self.path.write_bytes(b"".join(lines[:4]))
        with self.assertRaisesRegex(
                ContractError,"REPLAY_ORIGINAL_CHAIN_HEAD_MISSING"):
            recover_original_prefix(
                self.path,receipt["chain_head"],receipt["sha256"])

    def test_bogus_prefix_sha_rejected_even_when_head_matches(self):
        _,_,receipt=read_source(self.path)
        with self.assertRaisesRegex(
                ContractError,"REPLAY_ORIGINAL_PREFIX_BYTES_CHANGED"):
            recover_original_prefix(
                self.path,receipt["chain_head"],"f"*64)
