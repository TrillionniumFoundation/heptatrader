"""Independent, single-pass multirange custody for frozen PRICE campaigns.

Reads only an operator's already-copied, naturally ended original PAPER source.
One complete segment/archive validation; three disjoint bounded source windows.
No native Train V2/Pocket4 price reader, network, model, or broker API.
"""
from __future__ import annotations

from pathlib import Path
import re

from paged_crosshost_custody import (
    CustodyError, GENESIS, LEDGER_SCHEMA, MAX_ARCHIVE_PAGES,
    MAX_GLOBAL_RECEIPTS, NUMERIC_NAME, PAGE_SCHEMA,
    canonical, original_segment, private_file, require, sha, strict,
)


def copied_original_multiwindow(copy_root, original_root, period, windows):
    """Source hashes/chain/ledger checked ONCE; bounded, disjoint windows.

    Windows are selected by the original local *availability* timestamp,
    exactly as the previous independent three-inventory implementation.
    No tuple is manufactured from a later source version.
    """
    root = Path(copy_root)
    require(root.is_dir() and not root.is_symlink()
            and root.stat().st_mode & 0o077 == 0, "BATCH_COPY_SOURCE_ROOT_UNSAFE")
    require(isinstance(original_root, str) and original_root.startswith("/")
            and ".." not in Path(original_root).parts,
            "BATCH_ORIGINAL_ABSOLUTE_ROOT")
    require(type(period) is str and len(period) == 64
            and all(c in "0123456789abcdef" for c in period),
            "BATCH_PERIOD_IDENTITY")
    require(isinstance(windows, (tuple, list)) and len(windows) == 3,
            "BATCH_EXACT_THREE_WINDOWS")
    previous_end = None
    for pair in windows:
        require(isinstance(pair, (tuple, list)) and len(pair) == 2,
                "BATCH_WINDOW_SHAPE")
        a, b = pair
        require(type(a) is int and type(b) is int
                and 0 < a < b and b - a <= 7_200_000,
                "BATCH_WINDOW_BOUND")
        if previous_end is not None:
            require(a > previous_end, "BATCH_WINDOWS_OVERLAP_OR_UNSORTED")
        previous_end = b
    archive = root / "source-archive-v1"
    require(archive.is_dir() and not archive.is_symlink(),
            "BATCH_ORIGINAL_ARCHIVE_MISSING")
    numeric = []
    for folder in root.iterdir():
        if not folder.name.startswith("segment-"):
            continue
        require(not folder.is_symlink() and folder.is_dir(),
                "BATCH_SEGMENT_COPY_UNSAFE")
        found = NUMERIC_NAME.fullmatch(folder.name)
        require(found is not None and folder.stat().st_mode & 0o077 == 0,
                "BATCH_SEGMENT_NAME_OR_MODE")
        numeric.append((int(found.group(1)), folder))
    numeric.sort()
    require(numeric and len({x for x, _ in numeric}) == len(numeric),
            "BATCH_NUMERIC_SEGMENT_IDENTITY")
    all_proofs = []
    pinned = {}
    all_source_ids = set()
    by_event = [{} for _ in windows]
    selected_counts = [0] * len(windows)
    total_quote_count = 0
    total_bytes = 0
    for start, folder in numeric:
        actual_file = folder / "quotes.jsonl"
        original_file = f"{original_root}/{folder.name}/quotes.jsonl"
        proof, rows = original_segment(actual_file, original_file, period)
        all_proofs.append({
            "segment_start_ns": start, "original_sha256": proof["sha256"],
            "quote_count": len(rows),
        })
        pinned[original_file] = proof
        total_bytes += proof["bytes"]
        total_quote_count += len(rows)
        require(total_quote_count <= MAX_GLOBAL_RECEIPTS,
                "BATCH_GLOBAL_SOURCE_RECEIPT_BUDGET")
        for q in rows:
            require(q[3] not in all_source_ids,
                    "BATCH_ORIGINAL_RECEIPT_DUPLICATED_ACROSS_SEGMENTS")
            all_source_ids.add(q[3])
            for i, (a, b) in enumerate(windows):
                if not a <= q[0] <= b:
                    continue
                old = by_event[i].get(q[1])
                if old is not None:
                    require(old[4] == q[4],
                            "BATCH_CONFLICTING_EVENT_PRICE")
                    if q[0] < old[0]:
                        by_event[i][q[1]] = q
                else:
                    by_event[i][q[1]] = q
                selected_counts[i] += 1
                require(selected_counts[i] <= 18000,
                        "BATCH_SELECTED_WINDOW_ROW_BUDGET")

    # The original archived page/ledger format is validated byte-for-byte.
    # Every archived segment must match its original absolute path and pinned
    # original hash, *including segments outside all three selected windows*.
    pages = sorted(archive.glob("page-*.json"))
    require(len(pages) <= MAX_ARCHIVE_PAGES
            and [p.name for p in pages] ==
                [f"page-{i:08d}.json" for i in range(len(pages))],
            "BATCH_ARCHIVE_PAGE_SEQUENCE")
    chain = private_file(archive / "ledger.jsonl", 64 * 1024 * 1024)
    require(chain.endswith(b"\n"), "BATCH_ARCHIVE_LEDGER_TRUNCATED")
    lines = chain.splitlines()
    require(len(lines) == len(pages), "BATCH_ARCHIVE_LEDGER_COUNT")
    prior_page = GENESIS
    prior_ledger = GENESIS
    prior_pins = []
    for index, path in enumerate(pages):
        raw = private_file(path, 256 * 1024)
        data = strict(raw)
        require(raw == canonical(data) + b"\n"
                and set(data) == {
                    "schema", "index", "previous_sha256",
                    "period_sha256", "prefixes",
                }
                and data.get("schema") == PAGE_SCHEMA
                and data.get("index") == index
                and data.get("period_sha256") == period
                and data.get("previous_sha256") == prior_page
                and isinstance(data.get("prefixes"), list)
                and len(data["prefixes"]) == 32,
                "BATCH_ARCHIVE_PAGE_HASH_CHAIN")
        ledger = strict(lines[index])
        require(lines[index] == canonical(ledger)
                and set(ledger) == {
                    "schema", "index", "page_sha256",
                    "previous_receipt_sha256", "receipt_sha256",
                }
                and ledger.get("schema") == LEDGER_SCHEMA
                and ledger.get("index") == index
                and ledger.get("page_sha256") == sha(raw)
                and ledger.get("previous_receipt_sha256") == prior_ledger
                and ledger.get("receipt_sha256") ==
                    sha(canonical({
                        k: v for k, v in ledger.items()
                        if k != "receipt_sha256"
                    })),
                "BATCH_ARCHIVE_LEDGER_CHAIN")
        prior_page = sha(raw)
        prior_ledger = ledger["receipt_sha256"]
        prior_pins.extend(data["prefixes"])
    require(len(prior_pins) <= len(all_proofs),
            "BATCH_ARCHIVE_REFERENCES_MISSING")
    for index, entry in enumerate(prior_pins):
        original_name = (
            original_root + "/" + numeric[index][1].name + "/quotes.jsonl"
        )
        require(isinstance(entry, dict)
                and entry.get("path") == original_name
                and pinned.get(original_name) == entry,
                "BATCH_ARCHIVED_SEGMENT_CHANGED")

    return {
        "proofs": all_proofs,
        "windows": [
            {
                "from_ms": a,
                "until_ms": b,
                "window_rows": sorted(by_event[index].values(),
                                      key=lambda row: (row[1], row[0], row[3])),
                "original_selected_quote_receipts": selected_counts[index],
            }
            for index, (a, b) in enumerate(windows)
        ],
        "numeric_segments": len(numeric),
        "archived_segments": len(prior_pins),
        "archive_pages": len(pages),
        "source_fingerprint_sha256": sha(canonical(all_proofs)),
        "original_source_bytes": total_bytes,
        "original_total_unique_quote_receipts": total_quote_count,
        "original_archive_checkpoint_chain_head": prior_ledger,
        "frozen_prefix_sha256": lambda count: sha(canonical(all_proofs[:count])),
        "complete_original_source_scans": 1,
        "zero_authorization_effect": True,
    }
