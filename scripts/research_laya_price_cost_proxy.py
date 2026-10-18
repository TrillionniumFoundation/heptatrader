#!/usr/bin/env python3
"""Pocket4 frozen price probabilities versus a fixed historical cost proxy."""
from __future__ import annotations
import argparse,json,os,resource,socket,sys,time
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"research/neuron_learning"))
from laya_cell.integrity import ContractError,sha_file,digest
from laya_cell.resident_store import write_once
from laya_cell.frozen_price_numpy import FrozenPriceModel
from laya_cell.price_year_gpu import fold_indices,_ms
from laya_cell.price_original_live_features import features_at_closed_bar
from laya_cell.market_session import session_clock,PROFILE
from laya_cell import price_cost_proxy as cost

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ("bars","dataset","training","output"):ap.add_argument("--"+name,type=Path,required=True)
    args=ap.parse_args()
    if socket.gethostname()!="alex-G1628-04":raise ContractError("POCKET4_ONLY")
    os.umask(0o077);args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
    selection=json.loads((args.training/"selection.json").read_text())
    train_protocol=json.loads((args.training/"protocol.json").read_text())
    contract=json.loads((args.dataset/"dataset-contract.json").read_text())
    checks=[
        (args.bars,contract["bars_sha256"]),
        (args.dataset/"features-targets.npz",contract["array_sha256"]),
        (args.dataset/"dataset-contract.json",selection["dataset_contract_sha256"]),
        (args.training/"protocol.json",selection["protocol_sha256"]),
        (ROOT/"research/neuron_learning/laya_cell/price_year_gpu.py",contract["feature_source_sha256"]),
        (args.training/"tiny_mlp.safetensors",selection["training"]["tiny_mlp"]["weights_sha256"])]
    for name in ("price_original_live_features.py","frozen_price_numpy.py","market_session.py"):
        path=ROOT/"research/neuron_learning/laya_cell"/name
        checks.append((path,sha_file(path)))
    weights_sha=selection["training"]["tiny_mlp"]["weights_sha256"]
    if any(sha_file(p)!=h for p,h in checks):raise ContractError("COST_FROZEN_SOURCE_CHANGED")
    if selection["selected_before_test"]!="tiny_mlp" or selection["test_used_in_selection"] is not False:
        raise ContractError("COST_MODEL_SELECTION_CHANGED")
    protocol={"schema":"heptatrader.frozen-price-cost-proxy-protocol.v1","created_at_ms":time.time_ns()//1000000,
        "model_sha256":weights_sha,"selection_sha256":sha_file(args.training/"selection.json"),
        "pins":[{"path":str(p),"sha256":h} for p,h in checks],
        "source_sha256":sha_file(ROOT/"research/neuron_learning/laya_cell/price_cost_proxy.py"),
        "runner_sha256":sha_file(Path(__file__)),
        "cohorts":["validation_already_used","partial_test_already_seen"],
        "quantity_eur":100000,"action_reference_roundtrip_fee_usd":4,"scoring_fees_usd":[0,4,8],
        "costs_are_hypotheses_not_ib_tariff":True,
        "action":"EXPECTED_TRAIN_BIN_MEAN_EXCEEDS_CURRENT_SPREAD_PLUS_4USD",
        "train_mapping":"UNCLIPPED_TRAIN_ONLY_CLASS_CONDITIONAL_MEAN_RETURN_BPS",
        "population":"KNOWN_13_BAR_INPUTS_ON_CALENDAR_GRID_NO_FUTURE_LABEL_ADMISSION",
        "known_input_unavailable_separately_counted":True,
        "future_missing_or_invalid_active_trades_unscorable_not_dropped":True,
        "calendar_profile":PROFILE,"future_bar_marks_zero_latency_not_execution":True,
        "no_trading_no_promotion":True,"horizon_ms":300000,"parameters_searched":0}
    write_once(args.output/"protocol.json",protocol)
    with np.load(args.dataset/"features-targets.npz",allow_pickle=False) as stored:
        arrays={name:stored[name] for name in ("features","targets","anchor_ms")}
    for array in arrays.values():array.setflags(write=False)
    dataset={"anchor_ms":arrays["anchor_ms"].tolist()}
    indices=fold_indices(dataset,train_protocol["fold"])
    if {k:len(v) for k,v in indices.items()}!=selection["counts"]:
        raise ContractError("COST_TRAIN_COUNTS_CHANGED")
    source=json.loads(args.bars.read_text());bars=source["bars"]
    bytime={b["time"]:b for b in bars}
    if len(bytime)!=len(bars):raise ContractError("COST_DUPLICATE_BAR_TIME")
    returns=[];labels=[]
    for i in indices["train"]:
        anchor=int(arrays["anchor_ms"][i]);base=bytime[anchor//1000-300];target=bytime[anchor//1000]
        returns.append((float(target["midpoint"]["close"])/float(base["midpoint"]["close"])-1)*10000)
        labels.append(int(arrays["targets"][i]))
    mapping=cost.train_mapping(returns,labels)
    write_once(args.output/"train-return-mapping.json",
        {"train_rows":len(returns),"mapping":mapping,"protocol_sha256":sha_file(args.output/"protocol.json")})
    model=FrozenPriceModel(args.training/"tiny_mlp.safetensors",weights_sha,selection["normalizer"])
    index_by_anchor={int(v):i for i,v in enumerate(arrays["anchor_ms"])}
    cutoff=(bars[-1]["time"]+300)*1000
    fold=train_protocol["fold"];population={};books={};model_outputs={}
    for part,start_key,end_key in (("validation","val_start_utc","val_end_utc"),("test","test_start_utc","test_end_utc")):
        start=_ms(fold[start_key]);end=min(_ms(fold[end_key]),cutoff)
        first=((start+cost.BAR_MS-1)//cost.BAR_MS)*cost.BAR_MS
        eligible=[];unavailable=[];calendar_count=0;npz_matches=0;additional=0
        for anchor in range(first,end-cost.BAR_MS+1,cost.BAR_MS):
            def opened(t):
                return session_clock(datetime.fromtimestamp(t/1000,timezone.utc))["calendar_regular_session_open"]
            if not opened(anchor) or not opened(anchor+cost.BAR_MS-1):continue
            calendar_count+=1
            try:feature,known_bar=cost.known_input_features(bytime,anchor)
            except ContractError as exc:
                unavailable.append({"anchor_ms":anchor,"reason":str(exc)});continue
            x=np.asarray(feature["features"],dtype=np.float32)
            old=index_by_anchor.get(anchor)
            if old is not None:
                if not np.array_equal(x,arrays["features"][old]):
                    raise ContractError("COST_INPUT_FEATURE_CONTRACT_MISMATCH")
                npz_matches+=1
            else:additional+=1
            eligible.append({"anchor_ms":anchor,"features":feature["features"],"known_bar":known_bar})
        x=np.asarray([e["features"] for e in eligible],dtype=np.float32)
        probs,clipped=model.predict(x)
        population[part]={"start_ms":start,"matured_analysis_end_ms":end,
            "planned_end_ms":_ms(fold[end_key]),"calendar_opportunities":calendar_count,
            "known_input_unavailable":len(unavailable),"known_input_failure_records":unavailable,
            "input_eligible":len(eligible),"exact_existing_npz_feature_matches":npz_matches,
            "extra_inputs_not_admitted_by_future_labels":additional}
        model_outputs[part]={"anchors":[e["anchor_ms"] for e in eligible],
            "probabilities":probs.tolist(),"clip_counts":clipped.tolist()}
        tiny=[];prior=[];flat=[]
        for i,e in enumerate(eligible):
            a=cost.decide(e["anchor_ms"],probs[i].tolist(),mapping,e["known_bar"])
            b=cost.decide(e["anchor_ms"],selection["frozen_prior"],mapping,e["known_bar"])
            tiny.append(a);prior.append(b);flat.append({**a,"side":"FLAT","predicted_return_bps":0.0})
        books[part]={"tiny_mlp":tiny,"train_prior":prior,"flat":flat}
    # The full fixed action book is durable BEFORE target marks are consulted.
    write_once(args.output/"input-populations.json",population)
    write_once(args.output/"frozen-probabilities.json",model_outputs)
    write_once(args.output/"sealed-actions.json",books)
    action_sha=sha_file(args.output/"sealed-actions.json")
    sealed_books=json.loads((args.output/"sealed-actions.json").read_text())
    results={};scored_books={}
    for part,strategies in sealed_books.items():
        results[part]={};scored_books[part]={}
        for name,actions in strategies.items():
            marked=[cost.mark(a,bytime.get(a["anchor_ms"]//1000)) for a in actions]
            if any(any(row.get(k)!=v for k,v in action.items()) for row,action in zip(marked,actions)):
                raise ContractError("COST_SCORED_ACTION_FIELDS_CHANGED")
            scored_books[part][name]=marked
            results[part][name]=cost.summarize(marked)
    if sha_file(args.output/"sealed-actions.json")!=action_sha:
        raise ContractError("COST_ACTION_BOOK_CHANGED")
    if any(sha_file(p)!=h for p,h in checks):raise ContractError("COST_SOURCE_CHANGED_DURING_RUN")
    write_once(args.output/"scored-mark-book.json",scored_books)
    result={"schema":"heptatrader.frozen-price-cost-proxy-result.v1",
        "at_ms":time.time_ns()//1000000,"protocol_sha256":sha_file(args.output/"protocol.json"),
        "mapping_sha256":sha_file(args.output/"train-return-mapping.json"),
        "action_book_sha256":action_sha,"mark_book_sha256":sha_file(args.output/"scored-mark-book.json"),
        "mapping":mapping,"population":{k:{n:v for n,v in p.items() if n!="known_input_failure_records"} for k,p in population.items()},
        "results":results,"hypothetical_costs_not_actual_commissions":True,
        "seen_historical_diagnostic_not_forward_efficacy":True,"gpu_initialized":False,
        "torch_imported":"torch" in sys.modules,"max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "new_training_steps":0,"orders_sent":0,"model_promoted":False}
    write_once(args.output/"result.json",result)
    brief={"output":str(args.output),"result_sha256":sha_file(args.output/"result.json"),
        "population":result["population"],"mapping":mapping,
        "results":{part:{name:{"counts":r["counts"],"scenarios":{fee:s["overall"] for fee,s in r["scenarios"].items()},
            "known_zero_spread_opportunities":r["known_zero_spread_opportunities"]}
            for name,r in strategies.items()} for part,strategies in results.items()},
        "max_rss_kib":result["max_rss_kib"],"torch_imported":result["torch_imported"]}
    print(json.dumps(brief),flush=True)

if __name__=="__main__":main()
