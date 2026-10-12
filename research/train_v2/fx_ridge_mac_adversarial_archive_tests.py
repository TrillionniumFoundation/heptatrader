"""Hostile research ZIP copies. Each must fail the independent Mac verifier.

No network/order path; original bundle remains immutable and untouched.
"""
from __future__ import annotations
from decimal import Decimal
import hashlib,json,os,tempfile,zipfile
from pathlib import Path
from fx_ridge_mac_independent_stdlib_verify import check,canonical,sha,source_prefix

def run(bundle):
    with zipfile.ZipFile(bundle) as z:
        original={name:z.read(name) for name in z.namelist()}
    index=json.loads(original["index.json"])
    tests={}

    def mutated(name,modify):
        files=dict(original)
        header=json.loads(files["index.json"])
        modify(files,header)
        files["index.json"]=(json.dumps(header,sort_keys=True,indent=2)+"\n").encode()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"mutation.zip"
            with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
                for f,d in files.items(): z.writestr(f,d)
            try:
                check(path)
            except Exception as ex:
                tests[name]={"refused":True,"error_code":type(ex).__name__+":"+str(ex)[:100]}
            else:
                tests[name]={"refused":False,"error_code":None}

    def update_json(files,index,name,body):
        raw=(json.dumps(body,sort_keys=True,indent=2)+"\n").encode()
        files[name]=raw
        index["file_sha256"][name]=sha(raw)

    def model_tamper(files,index):
        name="artifacts/model.json";obj=json.loads(files[name])
        obj["weights"][0]+=1.
        update_json(files,index,name,obj)
    mutated("model_weight_changed_index_rehashed",model_tamper)

    def result_tamper(files,index):
        name="artifacts/mature-outcome.json";obj=json.loads(files[name])
        obj["observed_original_midpoint_return_bps"]="999"
        update_json(files,index,name,obj)
    mutated("mature_price_changed_index_rehashed",result_tamper)

    def source_tamper(files,index):
        name=sorted(k for k in files if k.startswith("source/"))[0]
        raw=bytearray(files[name])
        pos=raw.find(b'"bid":')
        if pos<0: raise RuntimeError("NO_TEST_SOURCE_QUOTE")
        pos=raw.find(b'1.',pos)
        if pos<0: raise RuntimeError("NO_TEST_PRICE")
        raw[pos+2]=ord("9") if raw[pos+2]!=ord("9") else ord("7")
        files[name]=bytes(raw)
        index["file_sha256"][name]=sha(files[name])
        for v in index["source_map"].values():
            if v["archive"]==name:v["max_sha256"]=sha(files[name])
    mutated("source_body_changed_file_index_rehashed",source_tamper)

    def decision_tamper(files,index):
        name="artifacts/preoutcome-decision.json"
        obj=json.loads(files[name]);obj["forecast_midpoint_bps"]=100.
        update_json(files,index,name,obj)
        qname="artifacts/qualification.json"
        q=json.loads(files[qname]);q["decision_sha256"]=sha(files[name])
        update_json(files,index,qname,q)
    mutated("forecast_fabricated_and_qual_resigned_locally",decision_tamper)

    def late_registration(files,index):
        pname="artifacts/plan.json";rname="artifacts/local-registration.json"
        plan=json.loads(files[pname]);receipt=json.loads(files[rname])
        plan["registered_at_ms"]=plan["first_decision_ms"]-1000
        receipt["plan"]=plan
        receipt["plan_sha256"]=sha(canonical(plan))
        update_json(files,index,pname,plan)
        update_json(files,index,rname,receipt)
    mutated("retroactive_plan_with_matching_local_index",late_registration)

    def cherry_pick(files,index):
        name="artifacts/mature-outcome.json"
        obj=json.loads(files[name])
        due=obj["outcome_due_ms"]
        samples=[]
        for v in index["source_map"].values():
            for r in v["receipts"]:
                if r["role"]=="outcome":
                    raws=source_prefix(files[v["archive"]][:r["bytes"]],
                                       json.loads(files["artifacts/plan.json"])["period_sha256"])
                    samples.extend([q for q in raws if
                                    due<=q["event_ms"]<=due+5000
                                    and q["available_ms"]>=due])
        uniq={}
        for q in samples:
            uniq.setdefault(q["event_ms"],q)
        seq=sorted(uniq.values(),key=lambda q:(q["event_ms"],q["available_ms"]))
        if len(seq)<2: raise RuntimeError("NO_ADVERSARIAL_SECOND_QUOTE")
        other=seq[1]
        obj["outcome_quote_sha256"]=other["id"]
        obj["price_event_ms"]=other["event_ms"]
        obj["price_available_ms"]=other["available_ms"]
        input_=json.loads(files["artifacts/preoutcome-decision.json"])["history_quote_samples"][-1]
        entry=(Decimal(input_["bid"])+Decimal(input_["ask"]))/2
        current=(other["bid"]+other["ask"])/2
        value=(current/entry-1)*10000
        forecast=Decimal(str(obj["forecast_midpoint_bps"]))
        obj["observed_original_midpoint_return_bps"]=str(value)
        obj["forecast_error_bps"]=str(forecast-value)
        obj["research_direction_correct"]=(forecast>0 and value>0) or (forecast<0 and value<0)
        update_json(files,index,name,obj)
    mutated("later_but_self_consistent_future_quote",cherry_pick)

    passed=sum(v["refused"] for v in tests.values())
    return {"schema":"heptatrader.fx-ridge-mac-adversarial-archive-tests.v1",
            "original_bundle_sha256":sha(Path(bundle).read_bytes()),
            "cases":tests,"refused":passed,"total":len(tests),
            "all_hostile_copies_refused":passed==len(tests),
            "read_only":True,"broker_requests_or_orders":0}

def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--bundle",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    r=run(a.bundle)
    fd=os.open(a.output,os.O_EXCL|os.O_CREAT|os.O_WRONLY,0o600)
    with os.fdopen(fd,"w") as f:
        json.dump(r,f,sort_keys=True,indent=2);f.write("\n")
    print(json.dumps({"cases":r["total"],"refused":r["refused"],
                      "all_refused":r["all_hostile_copies_refused"]}))
    if not r["all_hostile_copies_refused"]:raise SystemExit(2)
if __name__=="__main__":main()
