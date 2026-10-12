"""X230 independent readback adversarial faults; copies only, never edits originals."""
from __future__ import annotations
from pathlib import Path
import hashlib,importlib.util,json,os,shutil,stat,sys,tempfile
from datetime import datetime,timezone

def run(root:Path):
    spec=importlib.util.spec_from_file_location("pocket4_original_readback",root/"independent-readback.py")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline=module.run(root)
    if baseline["state"]!="READONLY_CROSSHOST_ORIGINAL_SOURCE_COST_AND_MATURE_PRICE_MATCH":
        raise ValueError("ORIGINAL_BUNDLE_NOT_QUALIFIED")
    def json_mutation(filename,field,value):
        def action(temp):
            p=temp/filename
            obj=json.loads(p.read_bytes())
            obj[field]=value(obj[field]) if callable(value) else value
            p.write_bytes((json.dumps(obj,sort_keys=True)+"\n").encode())
            p.chmod(0o600)
        return action
    def edit_cost(temp):
        p=temp/"historical-cost.json"
        obj=json.loads(p.read_bytes())
        obj["fx_commission_one_way_bps"]["p90"]=1.2345
        p.write_bytes((json.dumps(obj,sort_keys=True)+"\n").encode())
        p.chmod(0o600)
    def mutate_source(temp):
        p=temp/"original-source-through-outcome.jsonl"
        raw=p.read_bytes()
        old=b'heptatrader.laya-shadow-quotes.v1'
        new=b'heptatrader.laya-shadow-quotes.v2'
        if old not in raw:raise ValueError("EXPECTED_SOURCE_MARK_NOT_FOUND")
        p.write_bytes(raw.replace(old,new,1))
        p.chmod(0o600)
    def truncate_source(temp):
        p=temp/"original-source-through-outcome.jsonl"
        raw=p.read_bytes();before,separator,_=raw.rstrip(b"\n").rpartition(b"\n")
        if not separator:raise ValueError("MISSING_PREFIX")
        p.write_bytes(before+b"\n")
        p.chmod(0o600)
    def tamper_code(temp):
        p=temp/"fx_cost_shadow.py"
        p.write_bytes(p.read_bytes()+b"\n# invalid extra\n")
        p.chmod(0o600)
    challenges=[
        ("decision_timestamp_mutation",json_mutation(
           "frozen-decision.json","decision_at_ms",lambda v:v+1),"DECISION_SHA256_CHANGED"),
        ("revised_cost_receipt_mutation",edit_cost,"HISTORICAL_COST_CHANGED"),
        ("source_chain_content_mutation",mutate_source,"SOURCE_CHAIN_SEQUENCE"),
        ("source_outcome_missing",truncate_source,"NO_MATURE_ORIGINAL_QUOTE"),
        ("source_code_drift",tamper_code,"SOURCE_CODE_DRIFT"),
        ("mature_event_clock_mutation",json_mutation(
            "mature-price-only-outcome-v1.json","mature_quote_event_at_ms",lambda v:v+1),
            "MATURE_QUOTE_IDENTITY_DRIFT"),
    ]
    results=[]
    for name,fn,reason in challenges:
        with tempfile.TemporaryDirectory(prefix="p4-x230-negative-") as folder:
            temp=Path(folder)
            for p in root.iterdir():
                if p.is_file() and p.name!="x230-readback.json":
                    shutil.copy2(p,temp/p.name)
                    (temp/p.name).chmod(0o600)
            fn(temp)
            caught=None
            try:module.run(temp)
            except ValueError as exc:caught=str(exc)
            ok=caught is not None and reason in caught
            results.append({"challenge":name,"refused":ok,
                            "expected_refusal_reason":reason,
                            "observed_reason":caught})
            if not ok:raise ValueError("NEGATIVE_CHALLENGE_FAILED:"+name+":"+str(caught))
    proof={
        "schema":"heptatrader.fx-cost-shadow-x230-negative-challenges.v1",
        "state":"ORIGINAL_BUNDLE_ACCEPTED_ALL_ADVERSARIAL_MUTATIONS_REJECTED",
        "at_utc":datetime.now(timezone.utc).isoformat(),
        "original_bundle_path_sha256":hashlib.sha256(str(root).encode()).hexdigest(),
        "original_bundle_unchanged":True,
        "completed":len(results),"passed":sum(x["refused"] for x in results),
        "cases":results,
        "only_local_copied_inputs_mutated":True,
        "trade_authority":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
    }
    output=root/"x230-negative-challenges.json"
    os.umask(0o077)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as fh:
        fh.write((json.dumps(proof,sort_keys=True,indent=2)+"\n").encode())
        fh.flush();os.fsync(fh.fileno())
    print(json.dumps({"state":proof["state"],"passed":proof["passed"],
         "total":proof["completed"],"receipt_sha256":hashlib.sha256(output.read_bytes()).hexdigest(),
         "broker_orders_sent":0},sort_keys=True))

if __name__=="__main__":run(Path(sys.argv[1]))
