#!/usr/bin/env python3
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / "scripts/apply_priority_remediation_batch1.py"), run_name="__main__")
path = ROOT / "scripts/hepta_oms_lifecycle.py"
value = path.read_text(encoding="utf-8")
old = '''    required = {
        "generation", "parent_generation", "parent_manifest_sha256", "history_records", "segment_records",
        "command_records", "send_attempt_records", "hot_replay_records",
        "segment_sha256", "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes", "active_tail_header_sha256",
        "authorization_effect", "paper_authorized", "live_authorized",
    }
    if set(runtime) != required or runtime["generation"] != generation or runtime["authorization_effect"] != "NONE" or runtime["paper_authorized"] != "0" or runtime["live_authorized"] != "0":
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
'''
new = '''    required_legacy = {
        "generation", "parent_generation", "parent_manifest_sha256", "history_records", "segment_records",
        "command_records", "send_attempt_records", "hot_replay_records",
        "segment_sha256", "checkpoint_sha256", "command_index_sha256",
        "runtime_command_index_sha256", "send_attempt_index_sha256",
        "hot_replay_sha256", "active_tail_header_bytes", "active_tail_header_sha256",
        "authorization_effect", "paper_authorized", "live_authorized",
    }
    required_sorted = set(required_legacy)
    required_sorted.add("send_attempt_index_order")
    sorted_send_index = set(runtime) == required_sorted
    if (set(runtime) not in {frozenset(required_legacy), frozenset(required_sorted)} or
            (sorted_send_index and runtime["send_attempt_index_order"] != "account-domain-time-v1") or
            runtime["generation"] != generation or runtime["authorization_effect"] != "NONE" or
            runtime["paper_authorized"] != "0" or runtime["live_authorized"] != "0"):
        raise v1.GenerationError("OMS_GENERATION_RUNTIME_MANIFEST_INVALID")
'''
if value.count(old) != 1:
    raise RuntimeError("runtime manifest verifier block not found")
value = value.replace(old, new, 1)
old2 = '''    send_lines = list(_iter_private_lines(root / "send-attempt-index.tsv"))
    if len(send_lines) != manifest.get("send_attempt_records"):
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")
'''
new2 = '''    send_lines = list(_iter_private_lines(root / "send-attempt-index.tsv"))
    if len(send_lines) != manifest.get("send_attempt_records"):
        raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_COUNT_MISMATCH")
    if sorted_send_index:
        previous_send = None
        for line in send_lines:
            fields = line.rstrip(b"\\n").decode("ascii").split("\\t")
            if len(fields) != 7:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID")
            try:
                key = (fields[0], fields[1], int(fields[2]), int(fields[6]),
                       fields[3], fields[4], fields[5])
            except ValueError as error:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_INVALID") from error
            if previous_send is not None and key <= previous_send:
                raise v1.GenerationError("OMS_GENERATION_SEND_INDEX_ORDER_INVALID")
            previous_send = key
'''
if value.count(old2) != 1:
    raise RuntimeError("send index verifier block not found")
path.write_text(value.replace(old2, new2, 1), encoding="utf-8")
print("priority remediation batch 1 verifier fix applied")
