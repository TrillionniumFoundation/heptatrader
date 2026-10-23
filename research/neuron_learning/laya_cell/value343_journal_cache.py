"""Incremental, original-hash-verified EUR/USD REQUEST source for Pocket4.

Read-only. A first scan authenticates the ENTIRE registered journal history.
Later inotify wakes verify only bytes appended since last complete record.
A separate periodic full replay confirms frozen-prefix integrity. Any journal
rewrite, mismatched hash, missing sequence or producer ID fails closed.
No broker access, trading authority, model fit or historical relabeling.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import stat

from hepta_decision_attribution import strict, digest

MAX_FILE_BYTES=16*1024*1024
MAX_TOTAL_BYTES=256*1024*1024
MAX_FILES=128
WIRE="heptatrader.exact-decision-attribution.v1"


def private_stat(path):
    st=path.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid()
            or st.st_mode & 0o077 or st.st_size>MAX_FILE_BYTES):
        raise ValueError("ORIGINAL_JOURNAL_FILE_SCOPE")
    return st


class VerifiedIncrementalJournal:
    """Only a pinned original, private EUR/USD research journal."""
    def __init__(self,manifest,manifest_sha):
        if (not isinstance(manifest,dict) or
                manifest.get("instrument")!="EUR.USD" or
                manifest.get("execution_domain")!="PAPER" or
                type(manifest.get("journal_directory")) is not str or
                type(manifest.get("period_sha256")) is not str or
                type(manifest_sha) is not str or len(manifest_sha)!=64):
            raise ValueError("ORIGINAL_INCREMENTAL_SCOPE")
        self.manifest=manifest
        self.manifest_sha=manifest_sha
        self.root=Path(manifest["journal_directory"])
        st=self.root.lstat()
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid()
                or st.st_mode & 0o077):
            raise ValueError("ORIGINAL_INCREMENTAL_DIRECTORY_SCOPE")
        self.files={}
        self.chains={}
        self.events=[]
        self.requests={}
        self.verified_new_records=0
        self.incremental_bytes=0
        self.new_file_count=0

    def _files(self):
        paths=sorted(self.root.glob("*.jsonl"))
        if len(paths)>MAX_FILES:
            raise ValueError("ORIGINAL_INCREMENTAL_TOO_MANY_FILES")
        return paths

    def update(self):
        """Accept only new COMPLETE, cryptographically valid journal records.

        File identity, inode, mtime/ctime must not change unless size grows.
        Existing historical data are never regenerated. Partial tails stay
        pending until the original append completes with a newline.
        """
        paths=self._files()
        total=0
        for path in paths:
            info=private_stat(path)
            total+=info.st_size
            if total>MAX_TOTAL_BYTES:
                raise ValueError("ORIGINAL_INCREMENTAL_TOTAL_BYTES_CAP")
            name=str(path)
            old=self.files.get(name)
            if old is not None:
                if (old["inode"]!=info.st_ino or
                        info.st_size<old["size"]):
                    raise ValueError("ORIGINAL_JOURNAL_ROTATED_OR_TRUNCATED")
                if info.st_size==old["size"]:
                    if (info.st_ctime_ns!=old["ctime_ns"] or
                            info.st_mtime_ns!=old["mtime_ns"]):
                        raise ValueError("ORIGINAL_JOURNAL_SAME_SIZE_MODIFIED")
                    continue
                if old["foreign"]:
                    self.files[name]={**old,"size":info.st_size,
                                      "mtime_ns":info.st_mtime_ns,
                                      "ctime_ns":info.st_ctime_ns}
                    continue
            start=0 if old is None else old["offset"]
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            try:
                vst=os.fstat(fd)
                # The original PAPER producer may legitimately append AFTER
                # our directory stat but BEFORE fd open. Read exactly the
                # already-captured prefix boundary; the added suffix is
                # authenticated on the next inotify wakeup. Rewrites at the
                # SAME size remain fatal.
                if (vst.st_ino!=info.st_ino or vst.st_size<info.st_size or
                        (vst.st_size==info.st_size and
                         vst.st_ctime_ns!=info.st_ctime_ns)):
                    raise ValueError("ORIGINAL_JOURNAL_CHANGED_DURING_READ")
                os.lseek(fd,start,os.SEEK_SET)
                fragments=[]
                remaining=info.st_size-start
                while remaining:
                    chunk=os.read(fd,min(remaining,1024*1024))
                    if not chunk:raise ValueError("ORIGINAL_JOURNAL_SHORT_READ")
                    fragments.append(chunk);remaining-=len(chunk)
                suffix=b"".join(fragments)
            finally:
                os.close(fd)
            end=suffix.rfind(b"\n")
            if end<0:
                # Do not mark a partially written first HEADER as registered.
                # The next actual journal append will finish the record.
                continue
            complete=suffix[:end+1]
            if not complete:
                continue
            lines=complete.splitlines(keepends=True)
            running=hashlib.sha256() if old is None else old["sha"].copy()
            offset=start
            is_foreign=False if old is None else old["foreign"]
            staged=[]
            staged_requests=[]
            chains=dict(self.chains)
            # On the very first record in a file, verify the producer HEADER
            # before reading future events. Subsequent segments may belong to
            # the same producer but must preserve its global sequence.
            for index,line in enumerate(lines):
                if len(line)>262144:
                    raise ValueError("ORIGINAL_INCREMENTAL_OVERSIZE_RECORD")
                record=strict(line)
                if old is None and index==0:
                    if record.get("kind")!="HEADER":
                        raise ValueError("ORIGINAL_INCREMENTAL_MISSING_HEADER")
                    header=record.get("data") or {}
                    if header.get("manifest_sha256")!=self.manifest_sha:
                        is_foreign=True
                    elif (header.get("instrument")!="EUR.USD" or
                          header.get("period_sha256")!=self.manifest["period_sha256"] or
                          header.get("execution_domain")!="PAPER"):
                        raise ValueError("ORIGINAL_INCREMENTAL_HEADER_SCOPE")
                if is_foreign:
                    break
                producer=record.get("producer_id")
                if (record.get("schema")!=WIRE or type(producer) is not str or
                        record.get("sha256")!=digest(
                            {k:v for k,v in record.items() if k!="sha256"}) or
                        type(record.get("sequence")) is not int or
                        type(record.get("dropped_total")) is not int or
                        record["dropped_total"]!=0):
                    raise ValueError("ORIGINAL_INCREMENTAL_RECORD_INVALID")
                expected=chains.get(producer,(0,"0"*64))
                if (record["sequence"]!=expected[0] or
                        record.get("previous")!=expected[1]):
                    raise ValueError("ORIGINAL_INCREMENTAL_CHAIN_BREAK")
                chains[producer]=(expected[0]+1,record["sha256"])
                running.update(line)
                offset+=len(line)
                record["_witness"]={"path":name,"bytes":offset,
                                    "sha256":running.hexdigest()}
                staged.append(record)
                if record["kind"]=="REQUEST":
                    rid=record["data"].get("request_id")
                    if not isinstance(rid,str) or rid in self.requests or any(
                            rid==row[0] for row in staged_requests):
                        raise ValueError("ORIGINAL_INCREMENTAL_REQUEST_ID")
                    staged_requests.append((rid,record))
            if is_foreign:
                self.files[name]={"inode":info.st_ino,"size":info.st_size,
                    "offset":info.st_size,"sha":None,"foreign":True,
                    "mtime_ns":info.st_mtime_ns,"ctime_ns":info.st_ctime_ns}
                continue
            self.chains=chains
            self.events.extend(staged)
            self.requests.update(staged_requests)
            self.files[name]={"inode":info.st_ino,"size":info.st_size,
                "offset":offset,"sha":running,"foreign":False,
                "mtime_ns":info.st_mtime_ns,"ctime_ns":info.st_ctime_ns}
            self.incremental_bytes+=len(complete)
            self.verified_new_records+=len(staged)
            if old is None:self.new_file_count+=1
        if set(self.files)!=set(str(path) for path in paths):
            raise ValueError("ORIGINAL_INCREMENTAL_FILE_DELETED")
        return self.events,self.requests

    def assert_full_replay_same(self,full_events,full_requests,*,
                                allow_new_incremental_suffix=False):
        """Independent full rehash must match the cached original chain.

        On an active append-only producer, a new original RESPONSE can arrive
        after the full scan's size-boundary snapshot. A subsequent verified
        incremental update may include these EXTRA records. This is not a
        historical-prefix failure; require every full-replayed record to
        match the independently verified cache, while all existing cached
        file stat/sequence/previous invariants continue to hold. For fixed
        offline tests, default remains strict whole-snapshot equality.
        """
        if (not allow_new_incremental_suffix and
                (len(full_events)!=len(self.events) or
                 len(full_requests)!=len(self.requests))):
            raise ValueError("ORIGINAL_INCREMENTAL_FULL_REPLAY_COUNT_DIFFERENCE")
        a={(e["producer_id"],e["sequence"]):e["sha256"] for e in self.events}
        b={(e["producer_id"],e["sequence"]):e["sha256"] for e in full_events}
        if len(a)!=len(self.events) or len(b)!=len(full_events):
            raise ValueError("ORIGINAL_INCREMENTAL_DUPLICATE_SOURCE_EVENT")
        if any(a.get(key)!=value for key,value in b.items()):
            raise ValueError("ORIGINAL_INCREMENTAL_FULL_REPLAY_CHAIN_DIFFERENCE")
        if any(rid not in self.requests or
               self.requests[rid]["sha256"]!=ev["sha256"]
               for rid,ev in full_requests.items()):
            raise ValueError("ORIGINAL_INCREMENTAL_FULL_REPLAY_REQUEST_DIFFERENCE")
        return {"verified_original_events":len(full_events),
                "verified_original_requests":len(full_requests),
                "verified_extra_new_events":len(self.events)-len(full_events),
                "full_source_consistent":True}
