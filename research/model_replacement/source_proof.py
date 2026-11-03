"""One source read per path, checking every historical prefix hash boundary."""
from collections import defaultdict
from pathlib import Path
import hashlib
import os
from research.neuron_learning.laya_cell.integrity import ContractError, digest
from research.neuron_learning.laya_cell import source_archive as archive

def need(condition, reason):
    if not condition: raise ContractError(reason)

def checked_pages(pins):
    """Use the resident registry parser, then defer original reads for batching."""
    first=pins[0]; path=archive._page_pin_path(first)
    need(first['index']==0 and first['previous_sha256']==archive.GENESIS,'ARCHIVE_GENESIS')
    versions={}
    pages,_,recorded=archive._load_pages({'feed_directory':str(path.parent.parent),
        'period_sha256':first['period_sha256']},versions=versions)
    need(len(recorded)>=len(pins),'ARCHIVE_NOT_CHECKPOINTED')
    for i,pin in enumerate(pins):
        need(pin==recorded[i],'ARCHIVE_PIN_SEQUENCE')
        p=archive._page_pin_path(pin); archive._private(p.parent,want_dir=True)
        need(archive._page_version(p.lstat())==versions[str(p)],'ARCHIVE_PAGE_CHANGED')
    return pages[:len(pins)]

def verify_prefixes(pins, sealed_keys):
    groups=defaultdict(dict)
    sealed_by_path=defaultdict(list)
    for key in sealed_keys: sealed_by_path[key[0]].append(key)
    for pin in pins:
        path,size,sha=pin['path'],pin['bytes'],pin['sha256']
        need(type(size) is int and size>0 and isinstance(sha,str) and len(sha)==64
             and all(c in '0123456789abcdef' for c in sha),'PREFIX_FORMAT')
        old=groups[path].get(size)
        need(old is None or old['sha256']==sha,'PREFIX_HASH_CONFLICT')
        groups[path][size]=pin
    total=0
    for path,boundaries in groups.items():
        sizes=sorted(boundaries); longest=boundaries[sizes[-1]]
        sealed=sealed_by_path[path]
        need(not sealed or all(k[1]==sizes[-1] for k in sealed),'SEALED_SOURCE_GREW')
        if sealed: longest={**longest,'ended':True}
        fd,_=archive._open_prefix(longest,sealed=bool(sealed))
        sha=hashlib.sha256(); consumed=0; tail=b''
        try:
            for size in sizes:
                while consumed<size:
                    chunk=os.read(fd,min(1024**2,size-consumed))
                    need(chunk,'SOURCE_SHORT_READ'); sha.update(chunk); consumed+=len(chunk); tail=chunk[-1:]
                need(tail==b'\n' and sha.hexdigest()==boundaries[size]['sha256'],'SOURCE_PREFIX_HASH_CHANGED')
            total+=consumed
        finally: os.close(fd)
    return {'original_files_read':len(groups),'original_bytes_read':total,
            'prefix_boundaries_checked':sum(len(v) for v in groups.values())}
