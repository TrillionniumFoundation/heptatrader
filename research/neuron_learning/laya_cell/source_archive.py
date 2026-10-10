"""Append-only pages for complete PAPER quote segments; no source deletion.

A page is an immutable hash-linked receipt for 32 original, chain-verified
segments. Files stay at their original paths. Readers verify page links, all
original bytes, source chains and the exact directory inventory on each read.
Open segments are never sealed, truncated, or substituted by historical bars.
These hashes provide tamper *evidence*; no claim of external signing.
"""
from __future__ import annotations

import hashlib
import fcntl
import os
import stat
from pathlib import Path

from .integrity import ContractError, canonical, read_json
from .shadow_source import read_source, strict

PAGE_SIZE = 32
UNSEALED_BOUND = 128
PAGE_SCHEMA = "heptatrader.laya-source-archive-page.v1"
PIN_SCHEMA = "heptatrader.laya-source-archive-pin.v1"
GENESIS = "0" * 64


def directory(config):
    feed = Path(config["feed_directory"])
    if not feed.is_absolute() or feed.is_symlink() or feed.resolve() != feed:
        raise ContractError("SOURCE_FEED_DIRECTORY_IDENTITY")
    return feed / "source-archive-v1"


def _private(path, want_dir=False):
    if path.is_symlink():
        raise ContractError("SOURCE_ARCHIVE_SYMLINK")
    st = path.stat()
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        raise ContractError("SOURCE_ARCHIVE_OWNER_OR_MODE")
    if want_dir and not stat.S_ISDIR(st.st_mode):
        raise ContractError("SOURCE_ARCHIVE_DIRECTORY_INVALID")
    if not want_dir and not stat.S_ISREG(st.st_mode):
        raise ContractError("SOURCE_ARCHIVE_FILE_INVALID")
    return st



def _checkpoint_ledger(root):
    """Independent append-only checkpoints detect deleted pages and stale tails."""
    ledger = root / "ledger.jsonl"
    if not ledger.exists():
        return []
    st = _private(ledger)
    if st.st_size > 64 * 1024**2:
        raise ContractError("SOURCE_ARCHIVE_LEDGER_SIZE")
    rows = []
    previous = GENESIS
    with ledger.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n") or len(line) > 2048:
                raise ContractError("SOURCE_ARCHIVE_LEDGER_LINE")
            item = strict(line)
            if (set(item) != {"schema", "index", "page_sha256",
                              "previous_receipt_sha256", "receipt_sha256"}
                or item["schema"] != "heptatrader.laya-archive-ledger.v1"
                or type(item["index"]) is not int or item["index"] != len(rows)
                or item["previous_receipt_sha256"] != previous
                or line != canonical(item) + b"\n"
                or item["receipt_sha256"] != hashlib.sha256(canonical(
                    {k:v for k,v in item.items() if k!="receipt_sha256"})).hexdigest()):
                raise ContractError("SOURCE_ARCHIVE_LEDGER_CHAIN")
            previous = item["receipt_sha256"]
            rows.append(item)
    return rows


def _append_checkpoint(root, index, page_sha):
    ledger = root / "ledger.jsonl"
    rows = _checkpoint_ledger(root)
    if len(rows) != index:
        raise ContractError("SOURCE_ARCHIVE_LEDGER_RACE")
    previous = rows[-1]["receipt_sha256"] if rows else GENESIS
    item = {"schema":"heptatrader.laya-archive-ledger.v1", "index":index,
            "page_sha256":page_sha,"previous_receipt_sha256":previous}
    item["receipt_sha256"] = hashlib.sha256(canonical(item)).hexdigest()
    fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise ContractError("SOURCE_ARCHIVE_LEDGER_OWNER")
        with os.fdopen(fd, "ab") as output:
            fd = -1
            output.write(canonical(item) + b"\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        if fd != -1: os.close(fd)
    return item["receipt_sha256"]

def _load_pages(config):
    root = directory(config)
    if not root.exists():
        return [], [], []
    _private(root, want_dir=True)
    discovered = sorted(root.glob("page-*.json"))
    pages, prefixes, pins = [], [], []
    checkpoint_rows = _checkpoint_ledger(root)
    if len(checkpoint_rows) != len(discovered):
        raise ContractError("SOURCE_ARCHIVE_PAGE_CHECKPOINT_COUNT")
    previous = GENESIS
    for i, path in enumerate(discovered):
        if path.name != f"page-{i:08d}.json":
            raise ContractError("SOURCE_ARCHIVE_PAGE_SEQUENCE")
        size = _private(path).st_size
        if size > 256 * 1024:
            raise ContractError("SOURCE_ARCHIVE_PAGE_SIZE")
        raw = path.read_bytes()
        doc = read_json(path, 256 * 1024)
        if (set(doc) != {"schema", "index", "previous_sha256", "period_sha256", "prefixes"}
            or doc["schema"] != PAGE_SCHEMA or type(doc["index"]) is not int
            or doc["index"] != i or doc["previous_sha256"] != previous
            or doc["period_sha256"] != config["period_sha256"]
            or type(doc["prefixes"]) is not list
            or len(doc["prefixes"]) != PAGE_SIZE
            or raw != canonical(doc) + b"\n"):
            raise ContractError("SOURCE_ARCHIVE_PAGE_CHAIN")
        page_sha = hashlib.sha256(raw).hexdigest()
        if checkpoint_rows[i]["page_sha256"] != page_sha:
            raise ContractError("SOURCE_ARCHIVE_PAGE_CHECKPOINT_HASH")
        pin = {"schema": PIN_SCHEMA, "path": str(path), "bytes": len(raw),
               "sha256": page_sha, "index": i, "previous_sha256": previous,
               "period_sha256": config["period_sha256"]}
        pages.append(doc)
        prefixes.extend(doc["prefixes"])
        pins.append(pin)
        previous = page_sha
    return pages, prefixes, pins


def verify_prefix(pin, *, sealed=False):
    """Verify exact prefix bytes with O_NOFOLLOW; sealed files cannot grow."""
    if type(pin) is not dict or not {"path", "bytes", "sha256"} <= pin.keys():
        raise ContractError("SOURCE_PREFIX_INVALID")
    path = Path(pin["path"])
    size = pin["bytes"]
    if (not path.is_absolute() or path.resolve() != path or
        type(size) is not int or not 0 < size <= 64 * 1024**2 or
        type(pin["sha256"]) is not str or len(pin["sha256"]) != 64):
        raise ContractError("SOURCE_PREFIX_IDENTITY")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or
            st.st_mode & 0o077 or st.st_size < size or
            (sealed and (pin.get("ended") is not True or st.st_size != size))):
            raise ContractError("SOURCE_PREFIX_FILE_CHANGED")
        hash_ = hashlib.sha256()
        remaining = size
        final_byte = b""
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise ContractError("SOURCE_PREFIX_SHORT_READ")
            hash_.update(chunk)
            final_byte = chunk[-1:]
            remaining -= len(chunk)
        if final_byte != b"\n" or hash_.hexdigest() != pin["sha256"]:
            raise ContractError("SOURCE_PREFIX_HASH_CHANGED")
    finally:
        os.close(fd)


def verify_page_pin(pin):
    if (type(pin) is not dict or set(pin) != {
        "schema", "path", "bytes", "sha256", "index", "previous_sha256", "period_sha256"
    } or pin.get("schema") != PIN_SCHEMA):
        raise ContractError("SOURCE_ARCHIVE_PIN_INVALID")
    path = Path(pin["path"])
    if (type(pin["index"]) is not int or pin["index"] < 0 or
        not path.is_absolute() or path.resolve() != path or
        path.name != f"page-{pin['index']:08d}.json" or
        path.parent.name != "source-archive-v1" or
        type(pin["bytes"]) is not int or pin["bytes"] <= 0 or
        type(pin["sha256"]) is not str or len(pin["sha256"]) != 64):
        raise ContractError("SOURCE_ARCHIVE_PIN_PATH")
    _private(path.parent, want_dir=True)
    _private(path)
    data = path.read_bytes()
    if len(data) != pin["bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
        raise ContractError("SOURCE_ARCHIVE_PIN_HASH")
    doc = read_json(path, 256 * 1024)
    if (data != canonical(doc) + b"\n" or doc.get("schema") != PAGE_SCHEMA
        or doc.get("index") != pin["index"]
        or doc.get("previous_sha256") != pin["previous_sha256"]
        or doc.get("period_sha256") != pin["period_sha256"]
        or len(doc.get("prefixes", [])) != PAGE_SIZE):
        raise ContractError("SOURCE_ARCHIVE_PIN_COHORT")
    for prefix in doc["prefixes"]:
        verify_prefix(prefix, sealed=True)
    return doc


def inventory(config, paths):
    """Read every page, check no archived segment has been omitted/reordered."""
    pages, prefixes, pins = _load_pages(config)
    feed = Path(config["feed_directory"])
    if len(prefixes) > len(paths):
        raise ContractError("SOURCE_ARCHIVE_ORIGINAL_MISSING")
    for i, prefix in enumerate(prefixes):
        path = paths[i]
        if prefix.get("path") != str(path) or path.parent.parent != feed:
            raise ContractError("SOURCE_ARCHIVE_REORDERED_OR_MISSING")
        verify_prefix(prefix, sealed=True)
    if len(paths) - len(prefixes) > UNSEALED_BOUND:
        raise ContractError("SOURCE_UNSEALED_SEGMENT_BOUND")
    return prefixes, pins


def _write_once(path, document):
    raw = canonical(document) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # Preserve an incomplete page as evidence. A subsequent read fails closed.
        raise
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return hashlib.sha256(raw).hexdigest()


def _seal_locked(config, maximum_new_pages=8):
    """Explicit, idempotent archive operation. Never deletes or changes feeds."""
    if not 1 <= maximum_new_pages <= 8:
        raise ContractError("SOURCE_ARCHIVE_PAGE_BUDGET")
    feed = Path(config["feed_directory"])
    root = directory(config)
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    _private(root, want_dir=True)
    paths = sorted(feed.glob("segment-*/quotes.jsonl"))
    pages, prior_prefixes, pins = _load_pages(config)
    if len(prior_prefixes) > len(paths):
        raise ContractError("SOURCE_ARCHIVE_ORIGINAL_MISSING")
    for i, old in enumerate(prior_prefixes):
        if old.get("path") != str(paths[i]):
            raise ContractError("SOURCE_ARCHIVE_REORDERED_OR_MISSING")
        verify_prefix(old, sealed=True)
    index = len(pages)
    offset = len(prior_prefixes)
    previous = pins[-1]["sha256"] if pins else GENESIS
    added = 0
    while len(paths) - offset >= PAGE_SIZE and added < maximum_new_pages:
        subset = paths[offset:offset + PAGE_SIZE]
        current = []
        for path in subset:
            # Stable lexical inventory: recovery-live is intentionally last.
            # Never seal it, even after END, since a later numeric segment
            # would otherwise appear before the sealed recovery witness.
            if path.parent.name == "segment-recovery-live":
                break
            head, _, prefix = read_source(path)
            if head["period_sha256"] != config["period_sha256"]:
                raise ContractError("CROSS_PERIOD_FEED")
            if not prefix["ended"] or prefix["uncommitted_tail_bytes"]:
                break
            verify_prefix(prefix, sealed=True)
            current.append(prefix)
        if len(current) != PAGE_SIZE:
            break
        doc = {"schema": PAGE_SCHEMA, "index": index,
               "previous_sha256": previous, "period_sha256": config["period_sha256"],
               "prefixes": current}
        target = root / f"page-{index:08d}.json"
        previous = _write_once(target, doc)
        _append_checkpoint(root, index, previous)
        added += 1
        index += 1
        offset += PAGE_SIZE
    return {"schema": "heptatrader.laya-archive-seal-receipt.v1",
            "period_sha256": config["period_sha256"],
            "pages_added": added, "total_pages": index,
            "sealed_segments": offset, "unsealed_segments": len(paths) - offset,
            "latest_page_sha256": previous, "source_files_removed": 0,
            "broker_requests_sent": 0, "orders_sent": 0}



def seal(config, maximum_new_pages=8):
    """Only one append-only page writer may operate on the archive."""
    root = directory(config)
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    _private(root, want_dir=True)
    fd = os.open(root / "seal.lock", os.O_RDWR | os.O_CREAT |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise ContractError("SOURCE_ARCHIVE_SEAL_LOCK_OWNER")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError("SOURCE_ARCHIVE_CONCURRENT_SEAL") from exc
        return _seal_locked(config, maximum_new_pages)
    finally:
        os.close(fd)

def main():
    import argparse
    from .integrity import read_json
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--max-new-pages", type=int, default=8)
    args = ap.parse_args()
    os.umask(0o077)
    result = seal(read_json(args.config, 1024**2), args.max_new_pages)
    import json
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
