"""Bounded decoded byte stream shared by offline OMS maintenance/diagnostics.

Standard gzip concatenation is supported. Padding, trailing garbage, torn
members and checksum failures are rejected, not skipped. Caller pins metadata.
"""
from __future__ import annotations

import os
import zlib

CHUNK = 8192
MAX_MEMBERS = 1_000_000


class ArchiveError(ValueError):
    pass


def decoded_chunks(fd: int, size: int, max_bytes: int):
    compressed = os.pread(fd, 2, 0) == b"\x1f\x8b"
    if size < 0 or size > (2 * max_bytes + 1048576 if compressed else max_bytes):
        raise ArchiveError("OMS_REPLAY_STORAGE_LIMIT" if compressed else "OMS_REPLAY_BYTE_LIMIT")
    offset = total = members = 0
    decoder = zlib.decompressobj(31) if compressed else None
    at_end = False
    pending = b""
    while True:
        if not pending and offset < size:
            pending = os.pread(fd, min(CHUNK, size - offset), offset)
            if not pending:
                raise ArchiveError("OMS_REPLAY_IO_OR_IDENTITY_FAILURE")
            offset += len(pending)
        if decoder is None:
            if not pending:
                return
            total += len(pending)
            if total > max_bytes:
                raise ArchiveError("OMS_REPLAY_BYTE_LIMIT")
            yield pending
            pending = b""
            continue
        if at_end:
            if not pending and offset == size:
                return
            decoder = zlib.decompressobj(31)
            at_end = False
        previous_size = len(pending)
        try:
            out = decoder.decompress(pending, CHUNK)
        except zlib.error as error:
            raise ArchiveError("OMS_REPLAY_ARCHIVE_INVALID") from error
        total += len(out)
        if total > max_bytes:
            raise ArchiveError("OMS_REPLAY_BYTE_LIMIT")
        if out:
            yield out
        if decoder.eof:
            members += 1
            if members > MAX_MEMBERS:
                raise ArchiveError("OMS_REPLAY_ARCHIVE_MEMBER_LIMIT")
            pending = decoder.unused_data
            at_end = True
        else:
            pending = decoder.unconsumed_tail
            if not out and len(pending) == previous_size:
                raise ArchiveError("OMS_REPLAY_ARCHIVE_INVALID")
