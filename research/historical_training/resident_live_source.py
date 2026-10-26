"""Bind the fast freshness check to an extension of the ORIGINAL verified feed.
Continuity remains the cached 24-probe decision with its original time and rule.
No new feed, broker request, time substitution, TTL relaxation or model import.
"""
from pathlib import Path
import hashlib,os,stat,sys,time
from .common import EvidenceError,digest

MAX_BYTES=64*1024*1024

def refresh(original, source, now_ms, reader=None):
    if source.get('source_prefix_digest') != digest(source.get('source_prefixes', [])):
        raise EvidenceError('CONTINUITY_PREFIX_DIGEST_CHANGED')
    feed=Path(original['feed_directory'])
    if not feed.is_absolute() or feed.resolve()!=feed:
        raise EvidenceError('ORIGINAL_FEED_PATH_CHANGED')
    candidates=[]
    for pin in source['source_prefixes']:
        path=Path(pin['path'])
        if path.parent.parent==feed and path.name=='quotes.jsonl' and path.parent.name.startswith('segment-'):
            if path.resolve()!=path:raise EvidenceError('SOURCE_SYMLINK')
            if pin.get('ended') is False:candidates.append((str(path),pin))
    if not candidates:raise EvidenceError('NO_ORIGINAL_CONTINUITY_SOURCE')
    # mtime is only a bounded search hint, NEVER quote freshness or authority.
    # Historical segment-recovery-live sorts after numeric live segments.
    _,pin=max(candidates,key=lambda item:(Path(item[0]).stat().st_mtime_ns,item[0]));path=Path(pin['path'])
    if reader is None:
        runtime=Path(original['runtime_directory'])
        sys.path[:0]=[str(runtime/'learn'),str(runtime/'scripts')]
        from laya_cell.shadow_source import read_source
        reader=read_source
    header,quotes,live=reader(path)
    if header.get('period_sha256')!=original['period_sha256']:
        raise EvidenceError('LIVE_SOURCE_PERIOD_CHANGED')
    if (live.get('path')!=str(path) or type(pin.get('bytes')) is not int or type(live.get('bytes')) is not int
        or not 0<pin['bytes']<=live['bytes']<=MAX_BYTES):
        raise EvidenceError('LIVE_SOURCE_PREFIX_TRUNCATED_OR_BOUND')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
            raise EvidenceError('LIVE_SOURCE_FILE_OWNER_OR_MODE')
        remain=live['bytes'];prefix_remaining=pin['bytes']
        live_hash=hashlib.sha256();prefix_hash=hashlib.sha256()
        while remain:
            part=os.read(fd,min(remain,1024*1024))
            if not part:raise EvidenceError('LIVE_SOURCE_SHORT_READ')
            live_hash.update(part)
            if prefix_remaining:
                count=min(prefix_remaining,len(part))
                prefix_hash.update(memoryview(part)[:count]);prefix_remaining-=count
            remain-=len(part)
            # Do not retain the previous chunk while the next read allocates.
            del part
    finally:os.close(fd)
    if live_hash.hexdigest()!=live['sha256']:
        raise EvidenceError('LIVE_SOURCE_CHANGED_DURING_VERIFICATION')
    if prefix_hash.hexdigest()!=pin['sha256']:
        raise EvidenceError('ORIGINAL_CONTINUITY_PREFIX_REWRITTEN')
    eligible=[q for q in quotes if type(q.get('t')) is int and type(q.get('known_at_ms')) is int
              and 0<q['t']<=q['known_at_ms']<=now_ms]
    if not eligible:raise EvidenceError('NO_CAUSAL_ORIGINAL_QUOTE')
    latest=max(eligible,key=lambda q:q['t'])
    updated={**source,'latest_source_quote_ms':latest['t'],'latest_quote_age_ms':now_ms-latest['t']}
    proof={'schema':'heptatrader.original-live-freshness.v1','continuity_at_ms':source['at_ms'],
        'continuity_prefix_digest':source['source_prefix_digest'],'pinned_prefix':pin,'verified_live_prefix':live,
        'quote_observed_at_ms':latest['t'],'quote_known_at_ms':latest['known_at_ms'],
        'quote_receipt':latest['receipt'],'quote_timestamp_rewritten':False,'source_ttl_ms':5000,
        'cached_continuity_not_efficacy':True,'broker_queries_sent':0}
    return updated,proof
