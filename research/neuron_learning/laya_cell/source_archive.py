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
import errno
import os
import stat
import sys
from pathlib import Path

from .integrity import ContractError, canonical, read_json
from .shadow_source import read_source, strict

PAGE_SIZE = 32
UNSEALED_BOUND = 128
PAGE_SCHEMA = "heptatrader.laya-source-archive-page.v1"
PAGE_SCHEMA_V2 = "heptatrader.laya-source-archive-page.v2"
PIN_SCHEMA = "heptatrader.laya-source-archive-pin.v1"
GENESIS = "0" * 64


def _page_source_paths(page, feed):
    """One ordered original-feed interval, with immutable v2 unsealed holes.

    v1 keeps its exact original 32-pin bytes. A v2 page seals precisely 32
    ended originals but explicitly names every intervening not-yet-ended
    original. A skipped file is NEVER called sealed, even if it later grows.
    """
    schema = page.get("schema")
    keys = {"schema", "index", "previous_sha256", "period_sha256", "prefixes"}
    if schema == PAGE_SCHEMA:
        skipped = []
    elif schema == PAGE_SCHEMA_V2:
        keys.add("skipped_paths")
        skipped = page.get("skipped_paths")
        if (type(skipped) is not list or len(skipped) > UNSEALED_BOUND
            or any(type(p) is not str for p in skipped)):
            raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
    else:
        raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
    pins = page.get("prefixes")
    if set(page) != keys or type(pins) is not list or len(pins) != PAGE_SIZE:
        raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
    sealed_paths = []
    for pin in pins:
        if (type(pin) is not dict or type(pin.get("path")) is not str
            or pin.get("ended") is not True
            or pin.get("uncommitted_tail_bytes") != 0):
            raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
        sealed_paths.append(pin["path"])
    for name in sealed_paths + skipped:
        path = Path(name)
        if (not path.is_absolute() or str(path) != name
            or path.name != "quotes.jsonl" or path.parent.parent != feed
            or not path.parent.name.startswith("segment-")):
            raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
    if (sealed_paths != sorted(set(sealed_paths))
        or skipped != sorted(set(skipped))
        or any(path in sealed_paths for path in skipped)
        or (skipped and skipped[-1] > sealed_paths[-1])):
        raise ContractError("SOURCE_ARCHIVE_PAGE_LAYOUT")
    return sorted(sealed_paths + skipped)


def _covered_paths(pages, feed):
    return [name for page in pages for name in _page_source_paths(page, feed)]


def _sealed_by_path(prefixes):
    """Map a proved ended source by exact original name, never by index."""
    return {pin["path"]: pin for pin in prefixes}


def _unsealed_paths(paths, prefixes):
    pinned = _sealed_by_path(prefixes)
    return [path for path in paths if str(path) not in pinned]


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
        if (type(doc.get("index")) is not int
            or doc["index"] != i or doc.get("previous_sha256") != previous
            or doc.get("period_sha256") != config["period_sha256"]
            or raw != canonical(doc) + b"\n"):
            raise ContractError("SOURCE_ARCHIVE_PAGE_CHAIN")
        _page_source_paths(doc, Path(config["feed_directory"]))
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


def _open_prefix(pin, *, sealed=False):
    """Validate the original identity and return its open fd and stat."""
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
        return fd, st
    except BaseException:
        os.close(fd)
        raise


def prefix_version(pin, *, sealed=False):
    """Validated local file identity, not proof of its original bytes."""
    fd, st = _open_prefix(pin, sealed=sealed)
    try:
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                st.st_ctime_ns, st.st_mode, st.st_uid, st.st_gid, st.st_nlink)
    finally:
        os.close(fd)


class _UncachedPrefixRead:
    """A read hint for sealed SHA-only passes, never a source integrity shortcut.

    Linux RWF_DONTCACHE prunes only cache instantiated by this read; it does
    not evict ranges already cached by another consumer. Unsupported kernels
    and filesystems use the ordinary read path and expose that fallback.
    """
    def __init__(self, fd, stats):
        self.fd, self.stats = fd, stats
        self.enabled = sys.platform.startswith("linux") and callable(getattr(os, "preadv", None))
        if not self.enabled:
            self._count("uncached_unavailable_files", 1)

    def _count(self, name, amount):
        if self.stats is not None:
            self.stats[name] = self.stats.get(name, 0) + amount

    def read(self, count):
        if self.enabled:
            buffer = bytearray(count)
            try:
                # Linux UAPI RWF_DONTCACHE; Python releases predating its named
                # constant still expose preadv2 via os.preadv(..., flags).
                got = os.preadv(self.fd, [buffer], -1, getattr(os, "RWF_DONTCACHE", 0x80))
            except NotImplementedError:
                # Some Python builds expose preadv but not its flags form.
                self.enabled = False
                self._count("uncached_unavailable_files", 1)
            except OSError as exc:
                if exc.errno not in (errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS):
                    raise
                self.enabled = False
                self._count("uncached_unsupported_files", 1)
            else:
                self._count("uncached_read_calls", 1)
                self._count("uncached_read_bytes", got)
                return bytes(memoryview(buffer)[:got])
        data = os.read(self.fd, count)
        self._count("fallback_read_calls", 1)
        self._count("fallback_read_bytes", len(data))
        return data


def verify_prefix(pin, *, sealed=False, prefer_uncached=False, read_stats=None):
    """Verify exact prefix bytes with O_NOFOLLOW; sealed files cannot grow."""
    if type(prefer_uncached) is not bool or prefer_uncached and not sealed:
        raise ContractError("UNCACHED_READ_REQUIRES_SEALED_PREFIX")
    fd, _ = _open_prefix(pin, sealed=sealed)
    try:
        reader = _UncachedPrefixRead(fd, read_stats) if prefer_uncached else None
        hash_ = hashlib.sha256()
        remaining = pin["bytes"]
        final_byte = b""
        while remaining:
            count = min(1024 * 1024, remaining)
            chunk = reader.read(count) if reader is not None else os.read(fd, count)
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
    if (data != canonical(doc) + b"\n"
        or type(doc.get("index")) is not int
        or doc.get("index") != pin["index"]
        or doc.get("previous_sha256") != pin["previous_sha256"]
        or doc.get("period_sha256") != pin["period_sha256"]):
        raise ContractError("SOURCE_ARCHIVE_PIN_COHORT")
    _page_source_paths(doc, path.parent.parent)
    for prefix in doc["prefixes"]:
        verify_prefix(prefix, sealed=True)
    return doc



def verify_archive_pin_sequence(pins):
    """Bind a frozen dataset to the complete on-disk archive registry.

    Checking isolated page hashes is insufficient: a caller could omit an
    earlier valid page and present a later page as its whole source proof.
    Match a genesis-rooted contiguous page prefix against the independently
    validated archive ledger, including page files added after the freeze.
    """
    if not isinstance(pins, list) or not pins:
        raise ContractError("SOURCE_ARCHIVE_PIN_SEQUENCE_REQUIRED")
    first = pins[0]
    if not isinstance(first, dict) or first.get("schema") != PIN_SCHEMA:
        raise ContractError("SOURCE_ARCHIVE_PIN_SEQUENCE_REQUIRED")
    if type(first.get("path")) is not str:
        raise ContractError("SOURCE_ARCHIVE_PIN_ROOT_INVALID")
    path = Path(first["path"])
    period = first.get("period_sha256")
    if (path.parent.name != "source-archive-v1" or not path.is_absolute()
        or path.resolve() != path
        or type(period) is not str or len(period) != 64
        or any(c not in "0123456789abcdef" for c in period)):
        raise ContractError("SOURCE_ARCHIVE_PIN_ROOT_INVALID")
    if first.get("index") != 0 or first.get("previous_sha256") != GENESIS:
        raise ContractError("SOURCE_ARCHIVE_PAGE_PROOF_DISCONTINUITY")
    feed = path.parent.parent
    _, _, recorded = _load_pages({"feed_directory": str(feed),
                                   "period_sha256": period})
    if len(recorded) < len(pins):
        raise ContractError("SOURCE_ARCHIVE_PIN_NOT_CHECKPOINTED")
    skipped_paths = set()
    for i, pin in enumerate(pins):
        if pin != recorded[i]:
            raise ContractError("SOURCE_ARCHIVE_PAGE_PROOF_DISCONTINUITY")
        page = verify_page_pin(pin)
        skipped_paths.update(page.get("skipped_paths", []))
    return skipped_paths


def inventory_index(config, paths):
    """Verify the complete ledger and ordered paths, NOT original source bytes.

    Consumers must verify every returned original pin before using its data.
    The ordinary inventory() API below continues doing both checks.
    """
    pages, prefixes, pins = _load_pages(config)
    feed = Path(config["feed_directory"])
    covered = _covered_paths(pages, feed)
    if len(covered) > len(paths):
        raise ContractError("SOURCE_ARCHIVE_ORIGINAL_MISSING")
    if covered != [str(path) for path in paths[:len(covered)]]:
        raise ContractError("SOURCE_ARCHIVE_REORDERED_OR_MISSING")
    if len(paths) - len(prefixes) > UNSEALED_BOUND:
        raise ContractError("SOURCE_UNSEALED_SEGMENT_BOUND")
    return prefixes, pins


def inventory(config, paths):
    """Verify every page and every ordered original sealed segment."""
    prefixes, pins = inventory_index(config, paths)
    for prefix in prefixes:
        verify_prefix(prefix, sealed=True)
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


def _recover_complete_orphan(config, root, paths):
    """Checkpoint only a complete, independently verified, already-fsynced page.

    Never recreate a missing END or silently turn a v2 skipped path into a
    sealed source. A partial page/ledger remains quarantined.
    """
    receipts = _checkpoint_ledger(root)
    discovered = sorted(root.glob("page-*.json"))
    if len(discovered) == len(receipts):
        return 0
    if len(discovered) != len(receipts) + 1:
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_COUNT")
    previous = GENESIS
    feed = Path(config["feed_directory"])
    prior_covered = []
    for i, receipt in enumerate(receipts):
        path = discovered[i]
        if path.name != f"page-{i:08d}.json":
            raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_SEQUENCE")
        if _private(path).st_size > 256 * 1024:
            raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_SIZE")
        raw = path.read_bytes()
        page = read_json(path, 256 * 1024)
        h = hashlib.sha256(raw).hexdigest()
        if (raw != canonical(page) + b"\n"
            or h != receipt["page_sha256"]
            or type(page.get("index")) is not int or page["index"] != i
            or page.get("previous_sha256") != previous
            or page.get("period_sha256") != config["period_sha256"]):
            raise ContractError("SOURCE_ARCHIVE_ORPHAN_PRIOR_PROOF")
        prior_covered.extend(_page_source_paths(page, feed))
        for pin in page["prefixes"]:
            verify_prefix(pin, sealed=True)
        previous = h
    if [str(p) for p in paths[:len(prior_covered)]] != prior_covered:
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_PRIOR_PROOF")
    index = len(receipts)
    path = discovered[index]
    if path.name != f"page-{index:08d}.json":
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_SEQUENCE")
    if _private(path).st_size > 256 * 1024:
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_SIZE")
    raw = path.read_bytes()
    page = read_json(path, 256 * 1024)
    if (raw != canonical(page) + b"\n"
        or type(page.get("index")) is not int or page["index"] != index
        or page.get("previous_sha256") != previous
        or page.get("period_sha256") != config["period_sha256"]):
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_PAGE_INVALID")
    covered = _page_source_paths(page, feed)
    offset = len(prior_covered)
    if (len(paths) - offset < len(covered)
        or [str(p) for p in paths[offset:offset + len(covered)]] != covered
        or any(Path(name).parent.name == "segment-recovery-live"
               for name in covered)):
        raise ContractError("SOURCE_ARCHIVE_ORPHAN_SOURCE_REORDERED")
    for pin in page["prefixes"]:
        head, _, observed = read_source(Path(pin["path"]))
        if (head.get("period_sha256") != config["period_sha256"]
            or observed != pin or observed.get("ended") is not True
            or observed.get("uncommitted_tail_bytes") != 0):
            raise ContractError("SOURCE_ARCHIVE_ORPHAN_SOURCE_CHANGED")
        verify_prefix(pin, sealed=True)
    # Skipped originals remain *unsealed*. They are present in the exact
    # ordered source interval; no END status is asserted or fabricated.
    _append_checkpoint(root, index, hashlib.sha256(raw).hexdigest())
    return 1


def _seal_locked(config, maximum_new_pages=8):
    """Append new, complete 32-END pages. Keep every original source byte."""
    if not 1 <= maximum_new_pages <= 8:
        raise ContractError("SOURCE_ARCHIVE_PAGE_BUDGET")
    feed = Path(config["feed_directory"])
    root = directory(config)
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    _private(root, want_dir=True)
    paths = sorted(feed.glob("segment-*/quotes.jsonl"))
    recovered = _recover_complete_orphan(config, root, paths)
    pages, prior_prefixes, pins = _load_pages(config)
    covered = _covered_paths(pages, feed)
    if len(covered) > len(paths):
        raise ContractError("SOURCE_ARCHIVE_ORIGINAL_MISSING")
    if covered != [str(path) for path in paths[:len(covered)]]:
        raise ContractError("SOURCE_ARCHIVE_REORDERED_OR_MISSING")
    for pin in prior_prefixes:
        verify_prefix(pin, sealed=True)
    index = len(pages)
    offset = len(covered)
    previous = pins[-1]["sha256"] if pins else GENESIS
    added = 0
    sealed_count = len(prior_prefixes)
    while len(paths) - offset >= PAGE_SIZE and added < maximum_new_pages:
        current = []
        skipped = []
        consumed = 0
        for path in paths[offset:]:
            # Keep the long-lived recovery witness outside the archive:
            # additional numeric segments may still sort before it.
            if path.parent.name == "segment-recovery-live":
                break
            head, _, prefix = read_source(path)
            if head["period_sha256"] != config["period_sha256"]:
                raise ContractError("CROSS_PERIOD_FEED")
            if prefix["ended"] and prefix["uncommitted_tail_bytes"] == 0:
                verify_prefix(prefix, sealed=True)
                current.append(prefix)
            else:
                skipped.append(str(path))
                if len(skipped) > UNSEALED_BOUND:
                    break
            consumed += 1
            if len(current) == PAGE_SIZE:
                break
        if len(current) != PAGE_SIZE:
            break
        doc = {"schema": PAGE_SCHEMA_V2 if skipped else PAGE_SCHEMA,
               "index": index, "previous_sha256": previous,
               "period_sha256": config["period_sha256"], "prefixes": current}
        if skipped:
            doc["skipped_paths"] = skipped
        _page_source_paths(doc, feed)
        target = root / f"page-{index:08d}.json"
        previous = _write_once(target, doc)
        _append_checkpoint(root, index, previous)
        added += 1
        index += 1
        offset += consumed
        sealed_count += PAGE_SIZE
    return {"schema": "heptatrader.laya-archive-seal-receipt.v1",
            "period_sha256": config["period_sha256"],
            "pages_added": added, "total_pages": index,
            "sealed_segments": sealed_count,
            "unsealed_segments": len(paths) - sealed_count,
            "latest_page_sha256": previous, "source_files_removed": 0,
            "recovered_fsynced_orphan_pages": recovered,
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
