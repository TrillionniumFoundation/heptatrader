"""A new, read-only prospective PRICE experiment; never an execution producer.

The plan fixes every slot before its first input exists. A scheduled slot and
the latest actual quote's timestamp are different fields. Every arm sees one
sealed causal input, stays loaded in the process, and receives the same local
deadline. Late/missing/crashed attempts remain in the original denominator.

Journal appends are hash chained and fsynced. A prediction is eligible only
after PREDICTION -> SEAL -> ARM_FINAL: the latter records that both preceding
records were durable before the deadline. A crash never reconstructs a seal
or repeats an old slot. These are local clock/durability receipts, not an
independent timestamp authority and not evidence of execution profitability.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import copy
from decimal import Decimal
import errno
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import stat
import sys
import time

from research.neuron_learning.data import make_sample
from research.neuron_learning.laya_cell.dataset import BIN_EDGES_BPS
from research.neuron_learning.laya_cell.integrity import canonical, digest
from research.neuron_learning.laya_cell.shadow_source import (
    SourceDecodeCache, read_source, strict,
)
from research.neuron_learning.laya_cell.source_read_budget import source_read_budget

PLAN_SCHEMA = "heptatrader.model-replacement-prospective-plan.v1"
EVENT_SCHEMA = "heptatrader.model-replacement-prospective-event.v1"
MAX_JSON = 256 * 1024**2
MAX_JOURNAL = 512 * 1024**2
MAX_LINE = 2 * 1024**2
INPUT_POLICY = {
    "selection": "LATEST_REAL_QUOTE_AT_OR_BEFORE_FIXED_SLOT",
    "available_cutoff": "FIXED_SLOT_NOT_READ_OR_PREDICTION_TIME",
    "minimum_quotes": 6, "maximum_quotes": 24, "minimum_spacing_ms": 10000,
    "maximum_gap_ms": 30000, "maximum_anchor_age_ms": 5000,
    "history_lookback_ms": 720000, "synthetic_market_observations": False,
}
OUTCOME_POLICY = {
    "horizon_ms": 300000, "endpoint": "FIRST_REAL_QUOTE_AT_OR_AFTER_ACTUAL_ANCHOR_PLUS_HORIZON",
    "maximum_endpoint_lateness_ms": 5000, "maximum_availability_lag_ms": 5000,
    "maximum_path_gap_ms": 30000, "settle_after_ms": 10000,
    "maximum_absolute_return_bps": 800,
    "source_error_retry_interval_ms": 5000, "source_error_grace_ms": 60000,
}
WARMUP_POLICY = "FIXED_NUMERICAL_INPUT_NO_MARKET_OBSERVATION_NO_SCORE_NO_TRAINING"
SEGMENT = re.compile(r"segment-([0-9]{16,20})-[A-Za-z0-9]+\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def slot_time(plan, slot):
    return plan["first_slot_at_ms"] + slot * plan["cadence_ms"]


def read_bytes(path, maximum=MAX_JSON):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size <= maximum, "FILE_BOUND")
        chunks, remaining = [], before.st_size
        while remaining:
            part = os.read(fd, min(remaining, 1024**2))
            need(part, "FILE_SHORT_READ")
            chunks.append(part)
            remaining -= len(part)
        after = os.fstat(fd)
        version = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(version(before) == version(after), "FILE_CHANGED_DURING_READ")
        return b"".join(chunks)
    finally:
        os.close(fd)


def pinned_json(pin, maximum=MAX_JSON):
    need(type(pin) is dict and type(pin.get("path")) is str
         and Path(pin["path"]).is_absolute() and HEX.fullmatch(pin.get("sha256", "")), "FILE_PIN")
    raw = read_bytes(pin["path"], maximum)
    need(sha_bytes(raw) == pin["sha256"], "PINNED_FILE_CHANGED")
    return strict(raw)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(raw)
        while view:
            size = os.write(fd, view)
            need(size > 0, "FILE_SHORT_WRITE")
            view = view[size:]
        os.fchmod(fd, 0o400)
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


class Clock:
    """Fail closed on a wall-clock discontinuity instead of making time earlier."""
    def __init__(self):
        self.wall, self.monotonic = time.time(), time.monotonic()

    def __call__(self):
        wall, monotonic = time.time(), time.monotonic()
        need(abs((wall - self.wall) - (monotonic - self.monotonic)) < 2, "CLOCK_DISCONTINUITY")
        self.wall, self.monotonic = wall, monotonic
        return int(wall * 1000)


def runtime_identity():
    """Verify once at seal/start, not on each observation or ordinary poll."""
    here = Path(__file__).resolve().parent
    research = here.parent
    paths = list(here.glob("*.py")) + list((research / "neuron_learning").glob("*.py"))
    paths += list((research / "neuron_learning" / "laya_cell").glob("*.py"))
    packages = {}
    for name in ("numpy", "torch", "chronos-forecasting", "peft", "transformers", "timestral", "catboost"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    executable = Path(sys.executable).resolve()
    return {"python_version": sys.version, "executable": str(executable),
            "executable_sha256": sha_bytes(read_bytes(executable)), "packages": packages,
            "thread_environment": {key: os.environ.get(key) for key in
                ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
            "files_sha256": {str(p.relative_to(research)): sha_bytes(read_bytes(p)) for p in sorted(paths)}}


def check_model_state(state, dataset_id, split_id):
    need(state.get("schema") == "heptatrader.model-replacement-state.v1"
         and state.get("model_state_id") == digest({k: v for k, v in state.items() if k != "model_state_id"}),
         "MODEL_STATE_CONTENT_ID")
    need(state.get("trade_permission") is False and state.get("instrument") == "EUR.USD"
         and state.get("horizon_sec") == 300 and state.get("class_order") == list(range(7))
         and state.get("bin_edges_bps") == list(BIN_EDGES_BPS), "MODEL_STATE_SCOPE")
    need(state.get("dataset_id") == dataset_id and state.get("split_id") == split_id,
         "ALL_ARMS_REQUIRE_IDENTICAL_DATA_AND_SPLIT")
    keys = ("created_at_ms", "usable_after_ms", "training_cutoff_ms", "validation_cutoff_ms", "calibration_cutoff_ms")
    need(all(type(state.get(k)) is int and state[k] > 0 for k in keys), "MODEL_TIME_PINS_REQUIRED")
    need(state["training_cutoff_ms"] <= state["validation_cutoff_ms"] <= state["calibration_cutoff_ms"]
         < state["usable_after_ms"] <= state["created_at_ms"], "MODEL_TIME_ORDER")
    if state.get("backbone"):
        need(state["backbone"]["config"]["device"] == "cpu", "CPU_ONLY_RESEARCH_PLAN")
    return {k: state[k] for k in keys}


def seal_plan(config, path, *, now=None, identity=None):
    clock = now or Clock()
    created = clock()
    need(type(config) is dict, "PLAN_CONFIG")
    allowed = {"feed_root", "period_sha256", "dataset", "split", "arms", "start_at_ms", "slot_count",
               "cadence_ms", "deadline_offset_ms", "max_censored", "reference_arm"}
    need(set(config) <= allowed, "UNKNOWN_PLAN_CONFIG_FIELD")
    start = config["start_at_ms"]
    count, cadence = config.get("slot_count", 101), config.get("cadence_ms", 300000)
    deadline, censored = config.get("deadline_offset_ms", 5000), config.get("max_censored", 2)
    need(type(start) is int and start >= created + 60000, "STRICT_FUTURE_START_60S_REQUIRED")
    need(type(count) is int and 1 <= count <= 10080 and type(cadence) is int and cadence >= 10000,
         "SLOT_SCHEDULE_BOUND")
    need(type(deadline) is int and 100 <= deadline < min(cadence, 295000), "DEADLINE_BOUND")
    need(type(censored) is int and 0 <= censored < count, "COVERAGE_RULE")
    need(HEX.fullmatch(config["period_sha256"]), "PERIOD_PIN")
    root = Path(config["feed_root"])
    need(root.is_absolute() and root.resolve() == root and root.is_dir(), "FEED_DIRECTORY")
    dataset, split = pinned_json(config["dataset"]), pinned_json(config["split"])
    for value, key in ((dataset, "dataset_id"), (split, "split_id")):
        need(value.get(key) == digest({k: v for k, v in value.items() if k != key}), "DATA_CONTENT_ID")
    need(split.get("dataset_id") == dataset["dataset_id"], "SPLIT_DATASET_ID")
    need(dataset.get("instrument") == "EUR.USD" and dataset.get("horizon_sec") == 300, "DATA_SCOPE")
    arms, names = [], set()
    need(type(config["arms"]) is list and 2 <= len(config["arms"]) <= 8, "ARM_COUNT")
    for item in config["arms"]:
        name = item.get("name", "")
        need(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name) and name not in names, "ARM_NAME")
        names.add(name)
        state = pinned_json(item["model_state"], 16 * 1024**2)
        times = check_model_state(state, dataset["dataset_id"], split["split_id"])
        need(max(times.values()) < created < start, "MODEL_MUST_PRECEDE_PLAN_AND_FUTURE_INPUT")
        arms.append({"name": name, "model_state": item["model_state"],
                     "model_state_id": state["model_state_id"], **times})
    reference = config.get("reference_arm", "prior")
    need(reference in names, "REFERENCE_ARM_REQUIRED")
    plan = {"schema": PLAN_SCHEMA, "created_at_ms": created, "start_at_ms": start,
            "first_slot_at_ms": start + INPUT_POLICY["maximum_anchor_age_ms"],
            "slot_count": count, "cadence_ms": cadence, "deadline_offset_ms": deadline,
            "instrument": "EUR.USD", "horizon_sec": 300, "period_sha256": config["period_sha256"],
            "feed_root": str(root), "dataset": {**config["dataset"], "dataset_id": dataset["dataset_id"]},
            "split": {**config["split"], "split_id": split["split_id"]}, "arms": arms,
            "reference_arm": reference, "input_policy": INPUT_POLICY, "outcome_policy": OUTCOME_POLICY,
            "coverage_rule": {"min_observed": count - censored, "max_censored": censored,
                              "denominator": "ALL_FIXED_SLOTS_PER_ARM"},
            "scoring_policy": {"primary_reference_arm": reference, "reference_output": "raw_probabilities",
                               "reported_outputs": ["raw_probabilities", "probabilities"],
                               "additional_raw_references": ["linear"] if "linear" in names else [],
                               "post_outcome_output_selection": False},
            "warmup_policy": WARMUP_POLICY,
            "arm_order": "ROTATE_FROZEN_ORDER_BY_SLOT_INDEX", "runtime": (identity or runtime_identity)(),
            "evaluation_kind": "NEW_PROSPECTIVE_PRICE_PROBABILITIES", "trade_permission": False,
            "broker_orders_sent": 0, "automatic_promotion": False,
            "better_than_laya_claim_allowed": False, "timestamp_authority": "LOCAL_HOST_CLOCK_AND_FSYNC"}
    plan["plan_id"] = digest(plan)
    raw = canonical(plan) + b"\n"
    write_new(path, raw)
    durable = clock()
    need(durable < start, "PLAN_SEAL_DEADLINE_MISSED")
    receipt = {"schema": PLAN_SCHEMA + ".seal", "plan_id": plan["plan_id"],
               "plan_sha256": sha_bytes(raw), "plan_durable_at_ms": durable}
    write_new(str(path) + ".seal.json", canonical(receipt) + b"\n")
    need(clock() < start, "PLAN_SEAL_DEADLINE_MISSED")
    return {"plan": str(path), "sha256": sha_bytes(raw), "plan_id": plan["plan_id"],
            "start_at_ms": start, "first_slot_at_ms": plan["first_slot_at_ms"],
            "fixed_slots": count, "arms": len(arms)}


def load_plan(path, expected_sha256, *, identity=None):
    plan = pinned_json({"path": str(Path(path).absolute()), "sha256": expected_sha256})
    need(plan.get("schema") == PLAN_SCHEMA and plan.get("plan_id") == digest(
        {k: v for k, v in plan.items() if k != "plan_id"}), "PLAN_CONTENT_ID")
    receipt = strict(read_bytes(str(path) + ".seal.json", 65536))
    need(receipt.get("plan_id") == plan["plan_id"] and receipt.get("plan_sha256") == expected_sha256
         and plan["created_at_ms"] <= receipt["plan_durable_at_ms"] < plan["start_at_ms"], "PLAN_SEAL")
    need(plan["input_policy"] == INPUT_POLICY and plan["outcome_policy"] == OUTCOME_POLICY
         and plan["first_slot_at_ms"] == plan["start_at_ms"] + INPUT_POLICY["maximum_anchor_age_ms"]
         and plan["scoring_policy"]["primary_reference_arm"] == plan["reference_arm"]
         and plan["scoring_policy"]["reference_output"] == "raw_probabilities"
         and plan["scoring_policy"]["reported_outputs"] == ["raw_probabilities", "probabilities"]
         and plan["warmup_policy"] == WARMUP_POLICY
         and plan["trade_permission"] is False and plan["automatic_promotion"] is False, "PLAN_POLICY")
    need(plan["runtime"] == (identity or runtime_identity)(), "FROZEN_RUNTIME_CHANGED")
    dataset, split = pinned_json(plan["dataset"]), pinned_json(plan["split"])
    need(dataset["dataset_id"] == plan["dataset"]["dataset_id"] and split["split_id"] == plan["split"]["split_id"],
         "FROZEN_DATA_CHANGED")
    for arm in plan["arms"]:
        state = pinned_json(arm["model_state"], 16 * 1024**2)
        times = check_model_state(state, dataset["dataset_id"], split["split_id"])
        need(state["model_state_id"] == arm["model_state_id"] and all(arm[k] == v for k, v in times.items()),
             "FROZEN_MODEL_CHANGED")
    return plan


def read_events(path, plan_id):
    raw = read_bytes(path, MAX_JOURNAL) if Path(path).exists() else b""
    end = raw.rfind(b"\n") + 1
    events, previous, last = [], "0" * 64, 0
    for sequence, line in enumerate(raw[:end].splitlines()):
        need(len(line) <= MAX_LINE, "EVENT_BOUND")
        event = strict(line)
        need(event.get("schema") == EVENT_SCHEMA and event.get("plan_id") == plan_id
             and event.get("sequence") == sequence and event.get("previous") == previous
             and event.get("sha256") == digest({k: v for k, v in event.items() if k != "sha256"}), "EVENT_CHAIN")
        need(type(event.get("at_ms")) is int and event["at_ms"] >= last, "EVENT_CLOCK")
        previous, last = event["sha256"], event["at_ms"]
        events.append(event)
    return events, raw[end:], end


class Journal:
    def __init__(self, directory, plan, plan_sha256, clock):
        self.directory, self.plan, self.clock = Path(directory), plan, clock
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        need(self.directory.resolve() == self.directory.absolute()
             and not self.directory.stat().st_mode & 0o077, "PRIVATE_RESEARCH_STATE_REQUIRED")
        self.lock = os.open(self.directory / "observer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lock)
            raise
        self.path = self.directory / "events.jsonl"
        self.fd = None
        try:
            self.events, tail, committed = read_events(self.path, plan["plan_id"])
            if self.events:
                need(self.events[0]["kind"] == "HEADER" and self.events[0]["data"]["plan_sha256"] == plan_sha256,
                     "JOURNAL_PLAN_CHANGED")
            self.fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            if tail:
                # Preserve the uncommitted bytes before removing only their partial line.
                saved = self.directory / ("uncommitted-tail-" + sha_bytes(tail) + ".bin")
                if not saved.exists():
                    write_new(saved, tail)
                else:
                    need(read_bytes(saved) == tail, "RECOVERY_TAIL_CHANGED")
                os.ftruncate(self.fd, committed)
                os.fsync(self.fd)
            if not self.events:
                self.append("HEADER", {"plan_sha256": plan_sha256, "fixed_slots": plan["slot_count"],
                                      "arms": [a["name"] for a in plan["arms"]], "trade_permission": False})
                fsync_directory(self.directory)
            if tail:
                self.append("RECOVER_UNCOMMITTED_TAIL", {"bytes": len(tail), "sha256": sha_bytes(tail),
                                                         "saved_path": str(saved)})
        except BaseException:
            self.close()
            raise

    def append(self, kind, data):
        at = self.clock()
        need(not self.events or at >= self.events[-1]["at_ms"], "CLOCK_REGRESSED")
        body = {"schema": EVENT_SCHEMA, "plan_id": self.plan["plan_id"], "sequence": len(self.events),
                "previous": self.events[-1]["sha256"] if self.events else "0" * 64,
                "at_ms": at, "kind": kind, "data": copy.deepcopy(data)}
        event = {**body, "sha256": digest(body)}
        raw = canonical(event) + b"\n"
        need(len(raw) <= MAX_LINE and os.fstat(self.fd).st_size + len(raw) <= MAX_JOURNAL, "JOURNAL_BOUND")
        remaining = memoryview(raw)
        while remaining:
            size = os.write(self.fd, remaining)
            need(size > 0, "EVENT_SHORT_WRITE")
            remaining = remaining[size:]
        os.fsync(self.fd)
        durable = self.clock()
        need(durable >= at, "CLOCK_REGRESSED_DURING_FSYNC")
        self.events.append(event)
        return event, durable

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None


class FeedSource:
    """Bounded recent original segments; full SHA with incremental JSON decode.

    Discovery reads only flat directory names. It does not scan file contents
    or capacity across the archive. Every selected prefix is fully verified.
    """
    def __init__(self, plan):
        self.root, self.period = Path(plan["feed_root"]), plan["period_sha256"]
        self.cache = SourceDecodeCache(max_entries=32, max_source_bytes=32 * 1024**2, max_records=100000)

    def poll(self, now_ms, since_ms, deadline_ms=None):
        status = strict(read_bytes(self.root / "status.json", 65536))
        need(status.get("schema") == "heptatrader.laya-feed-status.v1"
             and status.get("period_sha256") == self.period and status.get("broker_orders_sent") == 0,
             "FEED_STATUS_SCOPE")
        paths = []
        with os.scandir(self.root) as entries:
            for count, entry in enumerate(entries):
                need(count < 32768, "FEED_DIRECTORY_BOUND")
                match = SEGMENT.fullmatch(entry.name)
                if match and since_ms - 2400000 <= int(match[1]) // 1000000 <= now_ms + 60000:
                    need(not entry.is_symlink(), "SOURCE_DIRECTORY_SYMLINK")
                    paths.append(Path(entry.path) / "quotes.jsonl")
        current = status.get("active_segment") or status.get("last_segment")
        if current:
            segment = Path(current)
            need(segment.parent == self.root and SEGMENT.fullmatch(segment.name)
                 and segment.resolve() == segment, "FEED_SEGMENT_PATH")
            paths.append(segment / "quotes.jsonl")
        paths = sorted(set(paths))
        need(0 < len(paths) <= 32, "RECENT_SOURCE_SEGMENT_BOUND")
        unique, prefixes, receipts, total = {}, {}, {}, 0
        with source_read_budget(deadline_ms):
            for path in paths:
                header, quotes, prefix = read_source(path, decode_cache=self.cache)
                need(header["period_sha256"] == self.period, "SOURCE_PERIOD_CHANGED")
                total += prefix["bytes"]
                need(total <= 32 * 1024**2, "RECENT_SOURCE_BYTE_BOUND")
                prefixes[str(path)] = {**prefix, "collector_sha256": header.get("collector_sha256")}
                for quote in quotes:
                    if quote["t"] < since_ms or quote["known_at_ms"] > now_ms:
                        continue
                    old = unique.get(quote["t"])
                    if old:
                        need(all(Decimal(str(old[k])) == Decimal(str(quote[k])) for k in ("bid", "ask")),
                             "CONFLICTING_SOURCE_QUOTE")
                    if old is None or quote["known_at_ms"] < old["known_at_ms"]:
                        unique[quote["t"]] = quote
                    receipts[quote["receipt"]] = str(path)
        return {"quotes": [unique[t] for t in sorted(unique)], "prefixes": prefixes,
                "receipt_paths": receipts, "read_at_ms": now_ms,
                "read_counters": self.cache.counters()}


def source_proofs(batch, quotes):
    paths = sorted({batch["receipt_paths"][q["receipt"]] for q in quotes})
    return [batch["prefixes"][path] for path in paths]


def verify_prefixes(prefixes):
    for pin in prefixes:
        fd = os.open(pin["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            meta = os.fstat(fd)
            need(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.geteuid() and not meta.st_mode & 0o077
                 and 0 < pin["bytes"] <= min(meta.st_size, 64 * 1024**2), "ORIGINAL_PREFIX_BOUND")
            checksum, remaining = hashlib.sha256(), pin["bytes"]
            while remaining:
                part = os.read(fd, min(remaining, 65536))
                need(part, "ORIGINAL_PREFIX_SHORT_READ")
                checksum.update(part)
                remaining -= len(part)
            need(checksum.hexdigest() == pin["sha256"], "ORIGINAL_INPUT_PREFIX_CHANGED")
        finally:
            os.close(fd)


def causal_sample(plan, slot, batch):
    scheduled = slot_time(plan, slot)
    quotes = [q for q in batch["quotes"] if q["t"] <= scheduled and q["known_at_ms"] <= scheduled]
    need(quotes, "NO_CAUSAL_QUOTES")
    latest = quotes[-1]
    need(0 <= scheduled - latest["t"] <= INPUT_POLICY["maximum_anchor_age_ms"], "LATEST_QUOTE_STALE")
    model_cutoff = max(max(a["created_at_ms"], a["usable_after_ms"]) for a in plan["arms"])
    need(latest["t"] >= max(plan["start_at_ms"], model_cutoff), "INPUT_PRECEDES_NEW_PLAN_OR_MODEL")
    selected = [latest]
    for quote in reversed(quotes[:-1]):
        if selected[-1]["t"] - quote["t"] >= INPUT_POLICY["minimum_spacing_ms"]:
            selected.append(quote)
            if len(selected) == INPUT_POLICY["maximum_quotes"]:
                break
    selected.reverse()
    need(len(selected) >= INPUT_POLICY["minimum_quotes"], "CAUSAL_HISTORY_TOO_SHORT")
    need(all(b["t"] - a["t"] <= INPUT_POLICY["maximum_gap_ms"] for a, b in zip(selected, selected[1:])),
         "CAUSAL_HISTORY_GAP")
    sample = make_sample(selected, scheduled, plan["period_sha256"] + ":" + plan["instrument"],
                         "MODEL_REPLACEMENT_PROSPECTIVE", plan["plan_id"] + ":" + str(slot))
    return {"sample": sample, "source_prefixes": source_proofs(batch, selected),
            "source_read_at_ms": batch["read_at_ms"], "source_read_counters": batch.get("read_counters", {})}


def real_outcome(sample, batch, now_ms):
    anchor, due = sample["anchor_ms"], sample["anchor_ms"] + OUTCOME_POLICY["horizon_ms"]
    need(now_ms >= due + OUTCOME_POLICY["settle_after_ms"], "OUTCOME_NOT_MATURE")
    quotes = [q for q in batch["quotes"] if q["known_at_ms"] <= now_ms]
    times = [q["t"] for q in quotes]
    begin, end = bisect_left(times, anchor), bisect_left(times, due)
    need(begin < len(quotes) and times[begin] == anchor, "OUTCOME_ANCHOR_MISSING")
    original = sample["input_snapshot"]["quotes"][-1]
    need(all(Decimal(str(quotes[begin][k])) == Decimal(str(original[k])) for k in ("bid", "ask"))
         and quotes[begin]["receipt"] == original["receipt"], "OUTCOME_ANCHOR_CHANGED")
    need(end < len(quotes) and times[end] <= due + OUTCOME_POLICY["maximum_endpoint_lateness_ms"],
         "OUTCOME_ENDPOINT_MISSING")
    path = quotes[begin:end + 1]
    need(all(b["t"] - a["t"] <= OUTCOME_POLICY["maximum_path_gap_ms"] for a, b in zip(path, path[1:])),
         "OUTCOME_PATH_GAP")
    endpoint = quotes[end]
    need(endpoint["known_at_ms"] <= endpoint["t"] + OUTCOME_POLICY["maximum_availability_lag_ms"],
         "OUTCOME_AVAILABILITY_LATE")
    mid = lambda q: (Decimal(str(q["bid"])) + Decimal(str(q["ask"]))) / 2
    bps = 10000 * (mid(endpoint) / mid(original) - 1)
    need(abs(bps) <= OUTCOME_POLICY["maximum_absolute_return_bps"], "RETURN_OUT_OF_PROFILE")
    return {"status": "OBSERVED", "target_class": bisect_right(BIN_EDGES_BPS, float(bps)),
            "return_bps": str(bps), "anchor_ms": anchor, "due_at_ms": due,
            "outcome_observed_at_ms": endpoint["t"], "outcome_available_at_ms": endpoint["known_at_ms"],
            "source_receipt": endpoint["receipt"], "source_prefixes": source_proofs(batch, path),
            "path_quotes": len(path), "metric_scope": "PRICE_MID_RETURN_NOT_EXECUTION_PNL"}


def empty_slot():
    return {"input": None, "finals": {}, "outcome": None, "begun": False,
            "attempts": {}, "predictions": {}, "seals": {}, "outcome_deferred": None}


def replay(events, plan):
    slots = {}
    arms = {a["name"] for a in plan["arms"]}
    for event in events:
        data, kind = event["data"], event["kind"]
        if "slot" not in data:
            continue
        index = data["slot"]
        need(type(index) is int and 0 <= index < plan["slot_count"], "EVENT_SLOT")
        state = slots.setdefault(index, empty_slot())
        deadline = slot_time(plan, index) + plan["deadline_offset_ms"]
        if kind == "SLOT_BEGIN":
            need(not state["begun"] and data["scheduled_at_ms"] == slot_time(plan, index)
                 and data["deadline_at_ms"] == deadline, "DUPLICATE_SLOT_ATTEMPT_OR_SCHEDULE")
            state["begun"] = True
        elif kind == "INPUT":
            need(state["begun"] and state["input"] is None, "DUPLICATE_INPUT")
            sample = data["sample"]
            need(sample["sample_id"] == digest(sample["input_snapshot"])
                 and sample["known_at_ms"] == slot_time(plan, index)
                 and plan["start_at_ms"] <= sample["anchor_ms"] <= sample["known_at_ms"], "INPUT_EVENT_IDENTITY")
            state["input"] = event
        elif kind in ("ARM_ATTEMPT", "PREDICTION", "SEAL"):
            name = data["arm"]
            need(name in arms and state["input"] is not None and name not in state["finals"], "ARM_EVENT_ORDER")
            sample = state["input"]["data"]["sample"]
            if kind == "ARM_ATTEMPT":
                need(name not in state["attempts"] and data["input_event_sha256"] == state["input"]["sha256"]
                     and data["sample_id"] == sample["sample_id"] and data["deadline_at_ms"] == deadline,
                     "ARM_ATTEMPT_IDENTITY")
                state["attempts"][name] = event
            elif kind == "PREDICTION":
                need(name in state["attempts"] and name not in state["predictions"]
                     and data["attempt_sha256"] == state["attempts"][name]["sha256"]
                     and data["sample_id"] == sample["sample_id"]
                     and data["actual_anchor_ms"] == sample["anchor_ms"]
                     and data["scheduled_at_ms"] == slot_time(plan, index)
                     and state["attempts"][name]["at_ms"] <= data["finished_at_ms"] <= event["at_ms"],
                     "PREDICTION_EVENT_IDENTITY")
                arm = next(a for a in plan["arms"] if a["name"] == name)
                validate_prediction(data["result"], arm, sample)
                need(data["result"]["created_at_ms"] <= data["finished_at_ms"], "PREDICTION_FUTURE_CLOCK")
                state["predictions"][name] = event
            else:
                prediction = state["predictions"].get(name)
                need(prediction is not None and name not in state["seals"]
                     and data["prediction_sha256"] == prediction["sha256"]
                     and prediction["at_ms"] <= data["prediction_durable_at_ms"] <= min(event["at_ms"], deadline),
                     "PREDICTION_SEAL_IDENTITY_OR_DEADLINE")
                state["seals"][name] = event
        elif kind == "ARM_FINAL":
            name = data["arm"]
            need(state["begun"] and name in arms and name not in state["finals"], "DUPLICATE_ARM_FINAL")
            need(data.get("trade_permission") is False and type(data.get("attempted")) is bool
                 and data["attempted"] == (name in state["attempts"]), "ARM_FINAL_ATTEMPT_IDENTITY")
            if data["status"] == "SEALED":
                seal, prediction = state["seals"].get(name), state["predictions"].get(name)
                need(seal is not None and prediction is not None and data["seal_sha256"] == seal["sha256"]
                     and data["prediction_sha256"] == prediction["sha256"]
                     and data["prediction_durable_at_ms"] == seal["data"]["prediction_durable_at_ms"]
                     and seal["at_ms"] <= data["seal_durable_at_ms"] <= min(event["at_ms"], deadline)
                     and data["deadline_at_ms"] == deadline
                     and data["lead_to_outcome_ms"] == state["input"]["data"]["sample"]["anchor_ms"] + 300000
                         - data["seal_durable_at_ms"] and data["lead_to_outcome_ms"] > 0, "ARM_FINAL_SEAL_PROOF")
            else:
                need(data["status"] == "MISSING" and type(data.get("reason")) is str and data["reason"],
                     "ARM_FINAL_STATUS")
            state["finals"][name] = event
        elif kind == "OUTCOME_DEFERRED":
            need(state["begun"] and state["input"] is not None and state["outcome"] is None
                 and len(state["finals"]) == len(arms), "OUTCOME_DEFERRED_ORDER")
            mature = state["input"]["data"]["sample"]["anchor_ms"] + 300000 + OUTCOME_POLICY["settle_after_ms"]
            until = mature + OUTCOME_POLICY["source_error_grace_ms"]
            failed = data.get("failed_at_ms")
            need(type(failed) is int and mature <= failed < until and failed <= event["at_ms"]
                 and type(data.get("reason")) is str
                 and data.get("retry_until_ms") == until
                 and data.get("retry_after_ms") == min(failed + OUTCOME_POLICY["source_error_retry_interval_ms"], until),
                 "OUTCOME_DEFERRED_RETRY_BOUND")
            need(state["outcome_deferred"] is None
                 or failed >= state["outcome_deferred"]["data"]["retry_after_ms"], "OUTCOME_RETRY_TOO_EARLY")
            state["outcome_deferred"] = event
        elif kind == "OUTCOME":
            need(state["begun"] and state["outcome"] is None and len(state["finals"]) == len(arms),
                 "OUTCOME_BEFORE_ALL_ARM_FINALS_OR_DUPLICATE")
            need(data["status"] in ("OBSERVED", "CENSORED"), "OUTCOME_STATUS")
            if data["status"] == "OBSERVED":
                sample = state["input"]["data"]["sample"]
                need(event["at_ms"] >= sample["anchor_ms"] + 310000 and data["anchor_ms"] == sample["anchor_ms"]
                     and data["due_at_ms"] == sample["anchor_ms"] + 300000
                     and data["due_at_ms"] <= data["outcome_observed_at_ms"] <= data["due_at_ms"] + 5000
                     and data["outcome_observed_at_ms"] <= data["outcome_available_at_ms"] <= event["at_ms"]
                     and type(data["target_class"]) is int and 0 <= data["target_class"] <= 6,
                     "OUTCOME_TIME_OR_CLASS")
            state["outcome"] = event
        else:
            raise ValueError("UNKNOWN_SLOT_EVENT")
    return slots


def validate_prediction(result, arm, sample):
    need(result.get("model_state_id") == arm["model_state_id"] and result.get("trade_permission") is False
         and result.get("sample_ids") == [sample["sample_id"]], "PREDICTION_IDENTITY")
    for field in ("raw_probabilities", "probabilities"):
        rows = result.get(field)
        need(type(rows) is list and len(rows) == 1 and type(rows[0]) is list and len(rows[0]) == 7,
             "PREDICTION_SHAPE")
        need(all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in rows[0])
             and abs(sum(rows[0]) - 1) < 1e-6, "PREDICTION_PROBABILITIES")
    need(type(result.get("created_at_ms")) is int and result["created_at_ms"] >= sample["known_at_ms"]
         and type(result.get("prediction_seconds")) in (int, float)
         and math.isfinite(result["prediction_seconds"]) and result["prediction_seconds"] >= 0,
         "PREDICTION_CLOCK_OR_TIMING")


def numerical_warmup(predictor):
    """Prime kernels only. This fixed input is never a market sample.

    SavedPredictor deliberately refuses replaying old samples as fresh inputs.
    Prime its underlying immutable inference components without weakening that
    guard, creating fake quotes, or putting warmup output into the experiment.
    """
    state = getattr(predictor, "state", None)
    if state is None:  # Dependency-injected protocol test predictors.
        return {"status": "NO_COMPONENT_INTERFACE", "scored": False}
    import numpy as np
    started = time.perf_counter()
    adapter = getattr(predictor, "adapter", None)
    if adapter is not None:
        adapter.predict([np.linspace(1.0, 1.0023, 24, dtype=np.float32)], horizon_steps=30)
    predictor.model.predict_proba(np.zeros((1, len(state["feature_names"])), dtype=np.float64))
    return {"status": "READY", "seconds": time.perf_counter() - started,
            "scope": WARMUP_POLICY, "scored": False}


class Observer:
    def __init__(self, plan, plan_sha256, directory, *, clock=None, source=None,
                 predictor_factory=None, row_factory=None):
        self.plan, self.clock = plan, clock or Clock()
        self.boot_ms = self.clock()
        self.journal = Journal(directory, plan, plan_sha256, self.clock)
        self.source = source or FeedSource(plan)
        self.predictors, self.load_errors = {}, {}
        self.stop = False
        try:
            self.slots = replay(self.journal.events, plan)
        except BaseException:
            self.journal.close()
            raise
        self.arms = {arm["name"]: arm for arm in plan["arms"]}
        try:
            # No inference or new input construction for a slot touched before a crash.
            for slot, state in self.slots.items():
                for name in self.arms:
                    if name not in state["finals"]:
                        self.missing(slot, name, "PROCESS_RESTART_NO_RETRY", attempted=name in state["attempts"])
                if state["input"] is None and state["outcome"] is None:
                    self.outcome(slot, {"status": "CENSORED", "reason": "INPUT_UNAVAILABLE"})
            if predictor_factory is None or row_factory is None:
                from .experiment import SavedPredictor, prediction_row
                predictor_factory = predictor_factory or SavedPredictor
                row_factory = row_factory or prediction_row
            self.row_factory = row_factory
            for name, arm in self.arms.items():
                started = time.perf_counter()
                warmup, load_seconds = None, None
                try:
                    predictor = predictor_factory(arm["model_state"]["path"], arm["model_state"]["sha256"])
                    load_seconds = time.perf_counter() - started
                    warmup = numerical_warmup(predictor)
                    self.predictors[name] = predictor
                    status, reason = "READY", None
                except Exception as exc:
                    status, reason = "FAILED", exception_reason(exc)
                    self.load_errors[name] = reason
                self.journal.append("MODEL_LOAD", {"arm": name, "status": status, "reason": reason,
                    "seconds": time.perf_counter() - started, "load_seconds": load_seconds, "warmup": warmup,
                    "model_state_sha256": arm["model_state"]["sha256"],
                    "resident_cpu": True, "boot_at_ms": self.boot_ms})
            self.journal.append("SESSION_READY", {"boot_at_ms": self.boot_ms, "ready_at_ms": self.clock(),
                                                 "loaded_arms": sorted(self.predictors)})
        except BaseException:
            self.journal.close()
            raise

    def begin(self, slot):
        scheduled = slot_time(self.plan, slot)
        self.journal.append("SLOT_BEGIN", {"slot": slot, "scheduled_at_ms": scheduled,
                                         "deadline_at_ms": scheduled + self.plan["deadline_offset_ms"]})
        self.slots[slot] = {**empty_slot(), "begun": True}

    def missing(self, slot, arm, reason, *, attempted=False, detail=None):
        event, _ = self.journal.append("ARM_FINAL", {"slot": slot, "arm": arm, "status": "MISSING",
            "reason": reason, "attempted": attempted, "detail": detail, "trade_permission": False})
        self.slots[slot]["finals"][arm] = event

    def outcome(self, slot, result):
        event, _ = self.journal.append("OUTCOME", {"slot": slot, **result})
        self.slots[slot]["outcome"] = event

    def predict_slot(self, slot):
        self.begin(slot)
        scheduled = slot_time(self.plan, slot)
        deadline = scheduled + self.plan["deadline_offset_ms"]
        reason = "SLOT_ELAPSED_BEFORE_PROCESS_START" if scheduled < self.boot_ms else None
        if self.clock() > deadline:
            reason = reason or "SCHEDULE_DEADLINE_MISSED"
        sample, input_event = None, None
        if reason is None:
            try:
                batch = self.source.poll(self.clock(), scheduled - INPUT_POLICY["history_lookback_ms"], deadline)
                payload = causal_sample(self.plan, slot, batch)
                sample = payload["sample"]
            except Exception as exc:
                reason = "INPUT_UNAVAILABLE:" + exception_reason(exc)
        if sample is not None:
            input_event, _ = self.journal.append("INPUT", {"slot": slot, **payload})
            self.slots[slot]["input"] = input_event
        if sample is None:
            for name in self.arms:
                self.missing(slot, name, reason)
            self.outcome(slot, {"status": "CENSORED", "reason": reason})
            return
        names = list(self.arms)
        order = names[slot % len(names):] + names[:slot % len(names)]
        for name in order:
            if self.stop:
                self.missing(slot, name, "SERVICE_STOP_DURING_SLOT")
                continue
            if name not in self.predictors:
                self.missing(slot, name, "MODEL_LOAD_FAILED", detail=self.load_errors.get(name))
                continue
            if self.clock() > deadline:
                self.missing(slot, name, "DEADLINE_BEFORE_ARM_ATTEMPT")
                continue
            attempt, attempt_durable = self.journal.append("ARM_ATTEMPT", {
                "slot": slot, "arm": name, "sample_id": sample["sample_id"],
                "input_event_sha256": input_event["sha256"], "deadline_at_ms": deadline})
            self.slots[slot]["attempts"][name] = attempt
            if attempt_durable > deadline:
                self.missing(slot, name, "ATTEMPT_DURABILITY_DEADLINE_MISSED", attempted=True)
                continue
            try:
                row = self.row_factory(copy.deepcopy(sample), instrument=self.plan["instrument"], horizon_sec=300)
                started = time.perf_counter()
                result = self.predictors[name].predict([row])
                elapsed = time.perf_counter() - started
                validate_prediction(result, self.arms[name], sample)
                finished = self.clock()
                need(result["created_at_ms"] <= finished, "PREDICTION_FUTURE_CLOCK")
            except Exception as exc:
                self.missing(slot, name, "PREDICTION_FAILED", attempted=True, detail=exception_reason(exc))
                continue
            prediction, prediction_durable = self.journal.append("PREDICTION", {
                "slot": slot, "arm": name, "attempt_sha256": attempt["sha256"],
                "sample_id": sample["sample_id"], "actual_anchor_ms": sample["anchor_ms"],
                "scheduled_at_ms": scheduled, "finished_at_ms": finished,
                "observer_prediction_seconds": elapsed, "result": result})
            if prediction_durable > deadline:
                self.missing(slot, name, "PREDICTION_DURABILITY_DEADLINE_MISSED", attempted=True,
                             detail={"prediction_sha256": prediction["sha256"], "durable_at_ms": prediction_durable})
                continue
            seal, seal_durable = self.journal.append("SEAL", {"slot": slot, "arm": name,
                "prediction_sha256": prediction["sha256"], "prediction_durable_at_ms": prediction_durable})
            if seal_durable > deadline:
                self.missing(slot, name, "SEAL_DURABILITY_DEADLINE_MISSED", attempted=True,
                             detail={"seal_sha256": seal["sha256"], "durable_at_ms": seal_durable})
                continue
            final, _ = self.journal.append("ARM_FINAL", {"slot": slot, "arm": name, "status": "SEALED",
                "attempted": True, "prediction_sha256": prediction["sha256"], "seal_sha256": seal["sha256"],
                "prediction_durable_at_ms": prediction_durable, "seal_durable_at_ms": seal_durable,
                "deadline_at_ms": deadline, "lead_to_outcome_ms": sample["anchor_ms"] + 300000 - seal_durable,
                "trade_permission": False})
            self.slots[slot]["finals"][name] = final

    def settle(self):
        now = self.clock()
        pending = [(slot, state) for slot, state in self.slots.items() if state["outcome"] is None
                   and state["input"] is not None and now >= state["input"]["data"]["sample"]["anchor_ms"] + 310000
                   and (state["outcome_deferred"] is None
                        or now >= state["outcome_deferred"]["data"]["retry_after_ms"])]
        if not pending:
            return
        try:
            earliest = min(state["input"]["data"]["sample"]["anchor_ms"] for _, state in pending)
            batch = self.source.poll(now, earliest)
            error = None
        except Exception as exc:
            batch, error = None, exc
        for slot, state in pending:
            try:
                if error is not None:
                    raise error
                verify_prefixes(state["input"]["data"]["source_prefixes"])
                result = real_outcome(state["input"]["data"]["sample"], batch, now)
            except Exception as exc:
                # Retry the grader's temporary resource/read failure, never an
                # old prediction or a missing/late/invalid market observation.
                # Only original timestamp-valid bytes can eventually succeed.
                until = state["input"]["data"]["sample"]["anchor_ms"] + 310000 + OUTCOME_POLICY["source_error_grace_ms"]
                at = self.clock()
                if temporary_read_error(exc) and at < until:
                    event, _ = self.journal.append("OUTCOME_DEFERRED", {"slot": slot,
                        "reason": exception_reason(exc), "failed_at_ms": at, "retry_until_ms": until,
                        "retry_after_ms": min(at + OUTCOME_POLICY["source_error_retry_interval_ms"], until)})
                    state["outcome_deferred"] = event
                    continue
                reason = exception_reason(exc)
                if temporary_read_error(exc):
                    reason = "OUTCOME_RESOURCE_RETRY_EXHAUSTED:" + reason
                result = {"status": "CENSORED", "reason": reason}
            self.outcome(slot, result)

    def tick(self):
        # Due new predictions precede outcome reads; expensive backfill never wins a slot.
        now = self.clock()
        for slot in range(self.plan["slot_count"]):
            if self.stop or slot_time(self.plan, slot) > now:
                break
            if slot not in self.slots:
                self.predict_slot(slot)
                now = self.clock()
        if not self.stop:
            self.settle()
        complete = len(self.slots) == self.plan["slot_count"] and all(s["outcome"] for s in self.slots.values())
        if complete and not any(e["kind"] == "COMPLETE" for e in self.journal.events):
            self.journal.append("COMPLETE", {"fixed_slots": self.plan["slot_count"], "trade_permission": False})
        return complete

    def run(self, *, once=False):
        complete = False
        try:
            while not self.stop:
                complete = self.tick()
                if complete or once:
                    break
                time.sleep(.25)
            self.journal.append("SESSION_STOP", {"requested": self.stop, "complete": complete,
                                                  "restart_repeats_old_slots": False})
            return summarize(self.plan, self.journal.events)
        finally:
            self.journal.close()


def exception_reason(exc):
    value = str(exc).replace("\n", " ")[:240]
    return type(exc).__name__ + ":" + value


def temporary_read_error(exc):
    """An explicit bounded allowlist; integrity/clock/policy errors never retry."""
    return isinstance(exc, MemoryError) or isinstance(exc, OSError) and exc.errno in {
        errno.EAGAIN, errno.EINTR, errno.EMFILE, errno.ENFILE, errno.ENOMEM,
        errno.EBUSY, errno.ETIMEDOUT, errno.ESTALE, errno.EIO, errno.ENOENT,
    }


def summarize(plan, events):
    slots = replay(events, plan)
    by_hash = {e["sha256"]: e for e in events}
    reference = plan["reference_arm"]
    names = [a["name"] for a in plan["arms"]]
    results, comparable = {}, []
    for slot, state in sorted(slots.items()):
        if state["outcome"] and state["outcome"]["data"]["status"] == "OBSERVED" and all(
                state["finals"].get(n, {}).get("data", {}).get("status") == "SEALED" for n in names):
            comparable.append(slot)
    def metric(name, selected, field):
        brier, logloss = [], []
        for slot in selected:
            state = slots[slot]
            prediction = by_hash[state["finals"][name]["data"]["prediction_sha256"]]["data"]["result"]
            probabilities = prediction[field][0]
            target = state["outcome"]["data"]["target_class"]
            brier.append(sum((p - int(i == target)) ** 2 for i, p in enumerate(probabilities)))
            logloss.append(-math.log(max(1e-15, probabilities[target])))
        return {"n": len(selected), "mean_brier": sum(brier) / len(brier) if brier else None,
                "mean_log_loss": sum(logloss) / len(logloss) if logloss else None}
    def coverage(selected):
        intervals = sorted((slots[i]["input"]["data"]["sample"]["anchor_ms"],
                            slots[i]["outcome"]["data"]["outcome_observed_at_ms"]) for i in selected)
        union, end = 0, 0
        for start, right in intervals:
            union += max(0, right - max(start, end))
            end = max(end, right)
        return {"distinct_actual_anchors": len({a for a, _ in intervals}), "interval_union_ms": union,
                "first_anchor_ms": intervals[0][0] if intervals else None,
                "last_anchor_ms": intervals[-1][0] if intervals else None,
                "independent_sample_count": None}
    for name in names:
        finals = [s["finals"][name]["data"] for s in slots.values() if name in s["finals"]]
        observed = [i for i, s in slots.items() if s["outcome"] and s["outcome"]["data"]["status"] == "OBSERVED"
                    and s["finals"].get(name, {}).get("data", {}).get("status") == "SEALED"]
        censored = len([s for s in slots.values() if s["outcome"]]) - len(observed)
        prediction_times = [e["data"]["observer_prediction_seconds"] for e in events
                            if e["kind"] == "PREDICTION" and e["data"]["arm"] == name]
        ordered_times = sorted(prediction_times)
        seal_latencies = [s["finals"][name]["data"]["seal_durable_at_ms"] - slot_time(plan, i)
                          for i, s in slots.items() if s["finals"].get(name, {}).get("data", {}).get("status") == "SEALED"]
        completed = sum(s["outcome"] is not None for s in slots.values()) == plan["slot_count"]
        results[name] = {"fixed_denominator": plan["slot_count"], "sealed_predictions": sum(f["status"] == "SEALED" for f in finals),
            "attempted": sum(f["attempted"] for f in finals), "observed": len(observed), "censored": censored,
            "unresolved_or_future": plan["slot_count"] - len(observed) - censored,
            "missing_prediction_reasons": dict(Counter(f["reason"] for f in finals if f["status"] == "MISSING")),
            "own_observed_metrics": {"raw": metric(name, observed, "raw_probabilities"),
                                     "calibrated": metric(name, observed, "probabilities")},
            "observed_time_coverage": coverage(observed),
            "identical_all_arm_cohort_metrics": {"raw": metric(name, comparable, "raw_probabilities"),
                                                "calibrated": metric(name, comparable, "probabilities")},
            "warm_prediction_seconds": {"n": len(ordered_times),
                "median": statistics.median(ordered_times) if ordered_times else None,
                "p95": ordered_times[math.ceil(.95 * len(ordered_times)) - 1] if ordered_times else None},
            "eligible_full_chain_seal_latency_ms": {"n": len(seal_latencies),
                "median": statistics.median(seal_latencies) if seal_latencies else None,
                "maximum": max(seal_latencies) if seal_latencies else None},
            "coverage_status": "COMPLETE" if completed else "PENDING_FULL_WINDOW",
            "coverage_passed": completed and len(observed) >= plan["coverage_rule"]["min_observed"]
                               and censored <= plan["coverage_rule"]["max_censored"]}
    for name in names:
        comparisons = {}
        for base in [reference] + (["linear"] if "linear" in names and reference != "linear" else []):
            baseline = results[base]["identical_all_arm_cohort_metrics"]["raw"]["mean_brier"]
            values = results[name]["identical_all_arm_cohort_metrics"]
            comparisons[base] = {output + "_brier_improvement_vs_raw_reference":
                                baseline - values[output]["mean_brier"] if baseline is not None else None
                                for output in ("raw", "calibrated")}
        results[name]["common_cohort_comparisons_vs_raw_references"] = comparisons
    closed = sum(s["outcome"] is not None for s in slots.values())
    common_censored = closed - len(comparable)
    common_pass = (closed == plan["slot_count"] and len(comparable) >= plan["coverage_rule"]["min_observed"]
                   and common_censored <= plan["coverage_rule"]["max_censored"])
    return {"schema": PLAN_SCHEMA + ".status", "plan_id": plan["plan_id"], "fixed_slots": plan["slot_count"],
        "slots_started": len(slots), "outcomes_finalized": closed,
        "identical_all_arm_observed_slots": len(comparable), "reference_arm": reference,
        "identical_all_arm_censored_slots": common_censored,
        "identical_all_arm_unresolved_or_future": plan["slot_count"] - closed,
        "identical_all_arm_coverage_passed": common_pass,
        "identical_all_arm_time_coverage": coverage(comparable),
        "reference_output": "raw_probabilities", "arms": results,
        "model_loads": [e["data"] for e in events if e["kind"] == "MODEL_LOAD"],
        "outcome_censor_reasons": dict(Counter(s["outcome"]["data"].get("reason") for s in slots.values()
             if s["outcome"] and s["outcome"]["data"]["status"] == "CENSORED")),
        "pending_outcome_resource_deferrals": sum(s["outcome"] is None and s["outcome_deferred"] is not None
                                                  for s in slots.values()),
        "outcome_resource_retry_records": sum(e["kind"] == "OUTCOME_DEFERRED" for e in events),
        "chain_head": events[-1]["sha256"] if events else None, "event_count": len(events),
        "trade_permission": False, "automatic_promotion": False, "better_than_laya_claim_allowed": False,
        "efficacy_claim": False, "metric_scope": "PRICE_PROBABILITIES_NOT_EXECUTION_PNL"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--config", required=True)
    seal.add_argument("--plan", required=True)
    for command in ("run", "report"):
        child = sub.add_parser(command)
        child.add_argument("--plan", required=True)
        child.add_argument("--plan-sha256", required=True)
        child.add_argument("--state-root", required=True)
        if command == "run":
            child.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "seal":
        result = seal_plan(strict(read_bytes(args.config)), Path(args.plan).absolute())
    else:
        plan = load_plan(args.plan, args.plan_sha256)
        if args.command == "report":
            events, tail, _ = read_events(Path(args.state_root) / "events.jsonl", plan["plan_id"])
            result = {**summarize(plan, events), "uncommitted_tail_bytes": len(tail)}
        else:
            observer = Observer(plan, args.plan_sha256, Path(args.state_root).absolute())
            def stop(*_):
                observer.stop = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            result = observer.run(once=args.once)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
