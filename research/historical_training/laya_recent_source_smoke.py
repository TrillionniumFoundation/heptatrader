"""Read-only source-parity proof for future Laya shadow worker candidates.

Runs original cold path once for independent equality; never launches a model.
"""
import argparse
import hashlib
import json
import os
import statistics
import time
from pathlib import Path

from laya_cell.integrity import ContractError,read_json,sha_file,digest
from laya_cell.resident_data import read_quotes,forecast_input
from laya_cell.resident_recent_source import (
    read_recent_original_quotes,MIN_CAUSAL_SPAN_MS,prewarm_recent_decoding)
from laya_cell.shadow_source import SourceDecodeCache
from laya_cell.source_read_budget import source_read_budget


def audit(config):
    at=int(time.time()*1000)
    t=time.monotonic()
    quick,q_pins=read_recent_original_quotes(
        config,at,min_span_ms=MIN_CAUSAL_SPAN_MS)
    quick_sec=time.monotonic()-t
    t=time.monotonic()
    full,f_pins=read_quotes(config,at)
    full_sec=time.monotonic()-t
    cutoff=at-MIN_CAUSAL_SPAN_MS
    q_last=[row for row in quick if row["t"]>=cutoff]
    f_last=[row for row in full if row["t"]>=cutoff]
    if q_last!=f_last:
        raise ContractError("RECENT_SOURCE_RECENT_PRICE_PARITY_FAILED")
    sample_quick,row_quick=forecast_input(config,quick,at)
    sample_full,row_full=forecast_input(config,full,at)
    if sample_quick!=sample_full or row_quick!=row_full:
        raise ContractError("RECENT_SOURCE_MODEL_FEATURE_PARITY_FAILED")
    # File identity must NOT import the live predictor/Trainer: those modules
    # import GPU model stacks even when the evidence check is read-only.
    source_root=Path(__file__).resolve().parents[1]/'neuron_learning'/'laya_cell'
    names=('resident_recent_source.py','resident_source_policy.py',
           'resident_data.py','resident_predict.py','resident.py')
    source_files={name:sha_file(source_root/name) for name in names}
    return {
       "schema":"heptatrader.laya-future-recent-source-parity-smoke.v1",
       "asof_ms":at,
       "original_quote_count":len(full),
       "recent_reader_quote_count":len(quick),
       "exact_recent_comparison_rows":len(q_last),
       "latest_quote_age_at_original_asof_ms":at-q_last[-1]["t"],
       "original_full_reader_seconds":round(full_sec,4),
       "opt_in_reader_seconds":round(quick_sec,4),
       "original_full_source_prefix_count":len(f_pins),
       "recent_reader_source_prefix_count":len(q_pins),
       "same_recent_quote_values":True,
       "same_native_numeric_sample_and_state":True,
       "sample_id":sample_quick["sample_id"],
       "state_sha256":digest(row_quick["state"]),
       "identical_source_prefixes_at_different_observation_instants":q_pins==f_pins,
       "archived_sealed_original_bytes_hashed_on_each_reader_use":True,
       "past_frozen_trial_modified":False,
       "future_source_profile_installed":False,
       "native_model_loaded":False,
       "canonical_owner_activated":False,
       "economic_effectiveness_claimed":False,
       "trade_authority":False,
       "broker_orders_sent":0,
       "source_code_sha256":source_files,
    }


def audit_decoding(config):
    """Alternate existing/cached readers on the live source at the same as-of.

    Fresh original byte verification is retained in both paths. Concurrent
    appends can change pins; exact as-of quotes and model input must agree.
    This CPU measurement cannot register a trial or claim forward efficacy.
    """
    cache=SourceDecodeCache();prepared=time.monotonic()
    prewarm_recent_decoding(config,cache,deadline_ms=int(time.time()*1000)+30000)
    prewarm_ms=round((time.monotonic()-prepared)*1000,3)
    pairs=[]
    for index in range(3):
        at=int(time.time()*1000);results={};elapsed={}
        order=('ordinary','cached') if index%2==0 else ('cached','ordinary')
        for name in order:
            started=time.monotonic()
            with source_read_budget(int(time.time()*1000)+30000):
                results[name]=read_recent_original_quotes(
                    config,at,**({'decode_cache':cache} if name=='cached' else {}))
            elapsed[name]=round((time.monotonic()-started)*1000,3)
        if results['ordinary'][0]!=results['cached'][0]:
            raise ContractError('DECODE_CACHE_ASOF_QUOTE_PARITY_FAILED')
        plain=forecast_input(config,results['ordinary'][0],at)
        reused=forecast_input(config,results['cached'][0],at)
        if plain!=reused:raise ContractError('DECODE_CACHE_MODEL_INPUT_PARITY_FAILED')
        pairs.append({'asof_ms':at,'order':list(order),'elapsed_ms':elapsed,
                      'quote_count':len(results['ordinary'][0]),'input_sha256':digest(plain[1]),
                      'same_asof_quotes':True,'same_model_input':True,
                      'same_live_prefix_snapshot':results['ordinary'][1]==results['cached'][1]})
    return {'schema':'heptatrader.source-decode-cache-parity.v1','pairs':pairs,
            'prewarm_ms':prewarm_ms,'median_ordinary_ms':statistics.median(p['elapsed_ms']['ordinary'] for p in pairs),
            'median_cached_ms':statistics.median(p['elapsed_ms']['cached'] for p in pairs),
            'cache':cache.counters(),'all_archived_originals_fully_rehashed_each_use':True,
            'live_source_concurrent_append_possible':True,'native_model_loaded':False,
            'new_forward_observations':0,'broker_orders_sent':0,'economic_effectiveness_claimed':False}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument('--decode-cache',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    record=(audit_decoding if args.decode_cache else audit)(read_json(args.config))
    path=args.output
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ContractError("IMMUTABLE_NEW_RECENT_AUDIT_PATH_REQUIRED")
    raw=json.dumps(record,sort_keys=True,indent=2,allow_nan=False).encode()+b"\n"
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as output:
        output.write(raw);output.flush();os.fsync(output.fileno())
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)
    if args.decode_cache:
        print(json.dumps({'status':'DECODE_CACHE_ORIGINAL_INPUT_PARITY_READONLY',
                         'output':str(path),'sha256':hashlib.sha256(raw).hexdigest(),**record},sort_keys=True))
        return
    print(json.dumps({
       "status":"ORIGINAL_RECENT_MODEL_FEATURE_PARITY_READONLY",
       "full_seconds":record["original_full_reader_seconds"],
       "recent_seconds":record["opt_in_reader_seconds"],
       "recent_rows_identical":record["exact_recent_comparison_rows"],
       "sample_id":record["sample_id"],
       "broker_orders_sent":0,
       "sha256":hashlib.sha256(raw).hexdigest(),
    },sort_keys=True))


if __name__=="__main__":
    main()
