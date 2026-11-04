#!/usr/bin/env python3
"""CPU-only frozen Current100k readout study; historical labels are already viewed.

Writes private research candidates. No broker, service or production publication.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, resource, sys, time
from pathlib import Path

PROFILE = "MASKED_MEAN_L2_NORMALIZED_FP32_768"
BACKEND = "LAYA_CPU_FP32_SINGLE_ROW_MAX2048_NO_TRUNCATION_V1"
ARMS = ("parent", "bias_shift_mean", "constant_mean", "constant_median", "ridge_normcap_0p1", "sgd16_reference", "sgd_head_budget_2s", "common_direction_half_ablation", "covariance_direction_patch")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8*1024*1024), b""): h.update(block)
    return h.hexdigest()


def digest(b): return hashlib.sha256(b).hexdigest()


def log(event, **kwargs):
    print(json.dumps(dict(event=event, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), **kwargs)), flush=True)


def json_write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()+b"\n"
    if path.exists():
        if path.read_bytes() != blob: raise RuntimeError("IMMUTABLE_CONFLICT:"+str(path))
        return
    temp = path.with_name(path.name+".partial-"+str(os.getpid()))
    with temp.open("xb") as f: f.write(blob); f.flush(); os.fsync(f.fileno())
    temp.chmod(0o400); os.replace(temp, path)


def tensors_write(path, tensors):
    from safetensors.torch import save_file
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists(): raise RuntimeError("TENSOR_EXISTS:"+str(path))
    temp = path.with_name(path.name+".partial-"+str(os.getpid()))
    save_file({k:v.detach().cpu().contiguous() for k,v in tensors.items()},str(temp))
    with temp.open("rb") as f: os.fsync(f.fileno())
    temp.chmod(0o400); os.replace(temp,path)


def memory_guard(min_mib=3072):
    mem = dict(line.split(":",1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(mem["MemAvailable"].split()[0])//1024
    if available < min_mib: raise RuntimeError("MEMORY_PRESSURE_PAUSE:"+str(available))
    return available


_configured = False

def configure_cpu(threads=2):
    global _configured
    os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1",USE_TF="0",TOKENIZERS_PARALLELISM="false",HIP_VISIBLE_DEVICES="",ROCR_VISIBLE_DEVICES="",CUDA_VISIBLE_DEVICES="",OPENBLAS_NUM_THREADS=str(threads),OMP_NUM_THREADS=str(threads),MKL_NUM_THREADS=str(threads),NUMEXPR_NUM_THREADS=str(threads))
    import torch
    if not _configured:
        torch.set_num_threads(threads); torch.set_num_interop_threads(1); _configured=True
    return torch


def snapshot(study):
    root=study/"snapshot"; manifest=json.loads((root/"source-manifest.json").read_text())
    for name, receipt in manifest["files"].items():
        if sha(root/name)!=receipt["sha256"]: raise RuntimeError("SNAPSHOT_HASH:"+name)
    candidate=json.loads((root/"candidate.json").read_text()); data=json.loads((root/"dataset-selected.json").read_text()); rows=data["rows"]
    if candidate["row_ids"]!=[r["row_id"] for r in rows]: raise RuntimeError("ROW_ORDER_IDENTITY")
    if [float(v) for v in candidate["target_net_usd"]]!=[float(r["net_usd"]) for r in rows]: raise RuntimeError("LABEL_IDENTITY")
    for r in rows:
        if digest(r["feature_text"].encode())!=r["feature_text_sha256"]: raise RuntimeError("FEATURE_TEXT_IDENTITY")
    return manifest,candidate,rows


def pinned_module(directory, module):
    import importlib
    import importlib.util
    directory=Path(directory).resolve();name="_hepta_weight_runtime_"+digest(str(directory).encode())[:16]
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,directory/"__init__.py",submodule_search_locations=[str(directory)])
        if spec is None or spec.loader is None:raise RuntimeError("PINNED_PACKAGE_UNAVAILABLE")
        package=importlib.util.module_from_spec(spec);sys.modules[name]=package
        try:spec.loader.exec_module(package)
        except BaseException:
            sys.modules.pop(name,None);raise
    return importlib.import_module(name+"."+module)


class WarmCpuEncoder:
    """Load once, then share one feature vector per action across all readouts.

    Runtime must be the pinned production source snapshot, never a mutable tree.
    ``encode_request`` consumes the exact pre-open REQUEST dictionary.
    """
    def __init__(self,runtime,base,threads=2,batch_size=1):
        if threads not in (1,2,4) or batch_size not in (1,2):raise ValueError("CPU_RESOURCE_PROFILE")
        self.threads=threads;self.batch_size=batch_size
        self.torch=configure_cpu(threads); self.runtime=Path(runtime); self.base=Path(base)
        memory_guard(6144)
        loader_module=pinned_module(self.runtime/"learn/laya_cell","model")
        load_verified_laya=loader_module.load_verified_laya
        self.actual_loader_path=str(Path(loader_module.__file__).resolve());self.actual_loader_sha256=sha(self.actual_loader_path)
        self.feature_text=pinned_module(self.runtime/"research/paired_net_100k_preflight","core").feature_text
        self.manifest=json.loads((self.base/"base-manifest.json").read_text())
        self.raw,self.tokenizer,self.base_id=load_verified_laya(self.base,self.manifest,enforce_gpu=False)
        if next(self.raw.parameters()).device.type!="cpu": raise RuntimeError("CPU_REQUIRED")
        self.raw.requires_grad_(False).eval()
    def encode_text(self,text):
        from torch.nn import functional as F
        memory_guard(); started=time.monotonic(); torch=self.torch
        batch=self.tokenizer([text],padding=True,truncation=False,return_tensors="pt")
        if batch["input_ids"].shape[1]>2048: raise RuntimeError("CONTEXT_EXCEEDS_2048_NO_TRUNCATION")
        batch={k:v.to("cpu") for k,v in batch.items() if k in ("input_ids","attention_mask","token_type_ids")}
        with torch.inference_mode():
            hidden=self.raw.encoder(**batch).last_hidden_state
            mask=batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled=(hidden*mask).sum(1)/mask.sum(1).clamp_min(1)
            x=F.normalize(pooled.float(),p=2,dim=1).detach().cpu()
        if x.shape!=(1,768) or not torch.isfinite(x).all(): raise RuntimeError("FEATURE_SHAPE_OR_NUMBER")
        return x,{"tokens":int(batch["attention_mask"].sum()),"seconds":time.monotonic()-started,"dtype":"float32","device":"cpu","feature_bytes_sha256":digest(x.numpy().tobytes())}
    def encode_request(self,request):
        if self.batch_size==2:
            try:from .batch_feature import encode_pair
            except ImportError:from batch_feature import encode_pair
            return encode_pair(self,request)
        result={}; hashes={}; timings={}
        for side in ("BUY","SELL"):
            text=self.feature_text(request,side); x,receipt=self.encode_text(text)
            result[side]=x[0].tolist(); hashes[side]=digest(text.encode()); timings[side]=receipt
        return {"features":result,"feature_text_sha256":hashes,"feature_profile":PROFILE,"backend":BACKEND,"threads":self.threads,"batch_size":self.batch_size,"actual_loader_sha256":self.actual_loader_sha256,"base_id":self.base_id,"receipts":timings,"computed_at_ms":int(time.time()*1000)}


def perturbations(row):
    text=row["feature_text"]; side=row["action"]; opposite="SELL" if side=="BUY" else "BUY"
    marker="selected action="+side
    if text.count(marker)!=1: raise RuntimeError("ACTION_MARKER")
    yield "action_flip",text.replace(marker,"selected action="+opposite,1)
    prefix="Recent authoritative quotes="; start=text.index(prefix)+len(prefix)
    quotes,length=json.JSONDecoder().raw_decode(text[start:]); anchor=float(quotes[0]["mid"])
    for name in ("quote_reserialized","price_shift_10pips","trend_reversed"):
        output=[]
        for q in quotes:
            v=dict(q)
            if name=="price_shift_10pips":
                for k in ("bid","ask","mid"):v[k]=float(q[k])+.001
            elif name=="trend_reversed":
                v["bid"],v["ask"],v["mid"]=2*anchor-float(q["ask"]),2*anchor-float(q["bid"]),2*anchor-float(q["mid"])
            output.append(v)
        yield name,text[:start]+json.dumps(output,separators=(",",":"))+text[start+length:]


def extract(args):
    torch=configure_cpu(args.threads)
    from safetensors.torch import load_file
    study=Path(args.study); manifest,candidate,rows=snapshot(study); started=time.monotonic()
    base_manifest=json.loads((Path(args.base)/"base-manifest.json").read_text())
    import transformers
    identity={"profile":PROFILE,"backend":BACKEND,"base_id":candidate["base_id"],"base_files":base_manifest["files"],"dataset_sha256":manifest["files"]["dataset-selected.json"]["sha256"],"ordered_feature_text_sha256":[r["feature_text_sha256"] for r in rows],"model_loader_sha256":sha(Path(args.runtime)/"learn/laya_cell/model.py"),"feature_reference_source_sha256":sha(Path(args.runtime)/"learn/laya_cell/financial_value_train.py"),"torch":torch.__version__,"transformers":transformers.__version__,"threads":args.threads,"truncation":False,"max_tokens":2048,"batch_size":1}
    json_write(study/"features/identity.json",identity)
    log("loading_cpu_encoder",memory_available_mib=memory_guard(6144))
    encoder=WarmCpuEncoder(args.runtime,args.base,args.threads)
    if encoder.base_id!=candidate["base_id"]: raise RuntimeError("BASE_ID_MISMATCH")
    log("loaded_cpu_encoder",load_seconds=time.monotonic()-started)
    vectors=[]; receipts=[]
    for i,row in enumerate(rows):
        p=study/"features/rows"/(f"{i:04d}-"+row["feature_text_sha256"][:16]+".safetensors"); rp=p.with_suffix(".json")
        if p.exists() and rp.exists():
            receipt=json.loads(rp.read_text())
            if receipt["row_id"]!=row["row_id"] or receipt["file_sha256"]!=sha(p):raise RuntimeError("CACHE_IDENTITY")
            x=load_file(str(p))["features"]
        else:
            x,receipt=encoder.encode_text(row["feature_text"]);tensors_write(p,{"features":x})
            receipt.update(index=i,row_id=row["row_id"],input_id=row["input_id"],input_started_at_ms=row["input_started_at_ms"],feature_text_sha256=row["feature_text_sha256"],file_sha256=sha(p));json_write(rp,receipt)
        vectors.append(x);receipts.append(receipt)
        if i%6==0 or i==len(rows)-1:log("features",completed=i+1,total=len(rows),seconds=time.monotonic()-started)
    x=torch.cat(vectors);p=study/"features/original-fp32.safetensors"
    if not p.exists():tensors_write(p,{"features":x})
    elif not torch.equal(load_file(str(p))["features"],x):raise RuntimeError("AGGREGATE_CACHE_CONFLICT")
    json_write(study/"features/rows.json",receipts)
    probes=[]
    for i in sorted(set((0,len(rows)//3,2*len(rows)//3,len(rows)-1))):
        for name,text in perturbations(rows[i]):
            p=study/"features/probes"/f"row-{i:04d}-{name}.safetensors";rp=p.with_suffix(".json");text_hash=digest(text.encode())
            if rp.exists():
                receipt=json.loads(rp.read_text())
                if receipt["feature_text_sha256"]!=text_hash:raise RuntimeError("PROBE_CACHE_IDENTITY")
                if receipt.get("supported",True) and receipt["file_sha256"]!=sha(p):raise RuntimeError("PROBE_CACHE_HASH")
            else:
                try:
                    xp,receipt=encoder.encode_text(text);tensors_write(p,{"features":xp})
                    receipt.update(path=str(p),file_sha256=sha(p),supported=True)
                except RuntimeError as exc:
                    if str(exc)!="CONTEXT_EXCEEDS_2048_NO_TRUNCATION":raise
                    receipt={"supported":False,"reason":str(exc),"seconds":0,"truncation_applied":False}
                receipt.update(row_index=i,row_id=rows[i]["row_id"],variant=name,feature_text_sha256=text_hash,counterfactual_not_new_market_label=True);json_write(rp,receipt)
            probes.append(receipt);log("probe",row=i,variant=name,seconds=receipt["seconds"],supported=receipt.get("supported",True))
    json_write(study/"features/probes.json",probes)
    json_write(study/"features/manifest.json",{"schema":"heptatrader.weight-feature-cache.v1","identity":identity,"features_sha256":sha(study/"features/original-fp32.safetensors"),"rows_sha256":sha(study/"features/rows.json"),"rows":len(rows),"probe_count":len(probes),"labels_previously_viewed":True,"gpu_initialized":torch.cuda.is_initialized(),"wall_seconds":time.monotonic()-started,"max_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024})
    log("extraction_complete",seconds=time.monotonic()-started,rows=len(rows),probes=len(probes))


def metric(pred,target):
    import numpy as np
    p,y=np.asarray(pred,dtype=float),np.asarray(target,dtype=float)
    return {"n":len(y),"mae_usd":float(np.mean(np.abs(p-y))),"rmse_usd":float(np.sqrt(np.mean((p-y)**2))),"mean_prediction_usd":float(p.mean()),"prediction_std_usd":float(p.std()),"min_prediction_usd":float(p.min()),"max_prediction_usd":float(p.max())}


def fit_recipes(x,y,parent):
    """Closed-form fitting still uses labels; common-direction ablation does not."""
    import torch
    from torch.nn import functional as F
    x,y=x.double(),y.double();w,b=parent["value.weight"].double(),parent["value.bias"].double()
    result={"parent":{k:v.clone().float() for k,v in parent.items()}}
    before=F.linear(x,w,b).flatten()
    result["bias_shift_mean"]={"value.weight":w.float(),"value.bias":(b+(y-before).mean()).float()}
    for name,center in (("constant_mean",y.mean()),("constant_median",torch.quantile(y,.5))):
        result[name]={"value.weight":torch.zeros_like(w).float(),"value.bias":center.reshape(1).float()}
    z=torch.cat((x,torch.ones(len(x),1,dtype=x.dtype)),dim=1)
    eigenvalues,vectors=torch.linalg.eigh(z@z.T/len(z)); eigenvalues=eigenvalues.clamp_min(0);rhs=vectors.T@y
    def solve(lam):return z.T@(vectors@(rhs/(eigenvalues+lam)))/len(z)
    low,high=1e-10,1.
    while solve(high).norm()>.1:high*=2
    for _ in range(64):
        mid=(low+high)/2
        if solve(mid).norm()>.1:low=mid
        else:high=mid
    theta=solve(high)
    result["ridge_normcap_0p1"]={"value.weight":theta[:768].reshape(1,768).float(),"value.bias":theta[768:].float()}
    head=torch.nn.Linear(768,1,dtype=torch.float32)
    with torch.no_grad():head.weight.zero_();head.bias.zero_()
    optimizer=torch.optim.SGD(head.parameters(),lr=.05);step_losses=[]
    for _ in range(16):
        optimizer.zero_grad(set_to_none=True);loss=F.smooth_l1_loss(head(x.float()).flatten(),y.float());loss.backward()
        torch.nn.utils.clip_grad_norm_(head.parameters(),1.,error_if_nonfinite=True);optimizer.step()
        with torch.no_grad():
            norm=torch.cat((head.weight.flatten(),head.bias)).norm()
            if norm>.1:head.weight.mul_(.1/norm);head.bias.mul_(.1/norm)
        step_losses.append(float(loss.detach()))
    result["sgd16_reference"]={"value.weight":head.weight.detach().clone(),"value.bias":head.bias.detach().clone()}
    mu=x.mean(0);u=F.normalize(mu,dim=0);removed=.5*(w.flatten()@u)*u
    result["common_direction_half_ablation"]={"value.weight":(w-removed).float(),"value.bias":(b+removed@mu).float()}
    return result,{"ridge_lagrange_multiplier":high,"ridge_constraint":"joint_weight_bias_l2<=0.1","ridge_loss":"mean_squared_scaled_loss","sgd_steps":16,"sgd_lr":.05,"sgd_huber_step_losses":step_losses}


def head_forward(tensors,x):
    import torch
    from torch.nn import functional as F
    if set(tensors)!={"value.weight","value.bias"} or tensors["value.weight"].shape!=(1,768) or tensors["value.bias"].shape!=(1,):raise RuntimeError("HEAD_SCHEMA")
    if any(v.dtype!=torch.float32 or not torch.isfinite(v).all() for v in tensors.values()):raise RuntimeError("HEAD_DTYPE_OR_NUMBER")
    head=torch.nn.Linear(768,1,dtype=torch.float32).requires_grad_(False)
    head.load_state_dict({"weight":tensors["value.weight"],"bias":tensors["value.bias"]},strict=True)
    with torch.inference_mode():
        actual=head(x.float()).flatten()*100.;direct=F.linear(x.float(),tensors["value.weight"],tensors["value.bias"]).flatten()*100.
    if not torch.equal(actual,direct):raise RuntimeError("FORWARD_MISMATCH")
    return actual


def analyze(args):
    torch=configure_cpu(args.threads)
    from safetensors.torch import load_file
    study=Path(args.study);manifest,candidate,rows=snapshot(study)
    fm=json.loads((study/"features/manifest.json").read_text());fp=study/"features/original-fp32.safetensors"
    if fm["features_sha256"]!=sha(fp):raise RuntimeError("FEATURE_HASH")
    x=load_file(str(fp))["features"]
    if x.shape!=(len(rows),768) or not torch.isfinite(x).all():raise RuntimeError("FEATURE_SHAPE_OR_NUMBER")
    y=torch.tensor([float(r["net_usd"])/100 for r in rows],dtype=torch.float64)
    parent=load_file(str(study/"snapshot/value-head.safetensors"))
    t=time.monotonic();cpu_started=time.process_time();recipes,info=fit_recipes(x,y,parent);base_seconds=time.monotonic()-t
    if base_seconds>2.05:raise RuntimeError("BASE_CONSTRUCTION_BUDGET_EXCEEDED")
    info["base_group_compute"]={"wall_seconds":base_seconds,"cpu_seconds":time.process_time()-cpu_started,"wall_budget_seconds":2.0,"shared_feature_cost_excluded":True}
    try:from .weight_recipes import augment_recipes
    except ImportError:from weight_recipes import augment_recipes
    extra,extra_info=augment_recipes(x,y,parent);recipes.update(extra);info.update(extra_info);fit_seconds=time.monotonic()-t
    cosine=x.double()@x.double().T;off=cosine[~torch.eye(len(x),dtype=torch.bool)]
    centered=x.double()-x.double().mean(0);s=torch.linalg.svdvals(centered);power=s.square();prob=power/power.sum();nonzero=prob>0
    geometry={"mean_feature_norm":float(x.mean(0).norm()),"centered_rms_l2":float(centered.square().sum(1).mean().sqrt()),"pairwise_cosine_min":float(off.min()),"pairwise_cosine_median":float(off.median()),"pairwise_cosine_max":float(off.max()),"centered_effective_rank":float(torch.exp(-(prob[nonzero]*prob[nonzero].log()).sum())),"centered_numeric_rank_rtol_1e6":int((s>s[0]*1e-6).sum()),"centered_singular_values":s.tolist(),"top1_centered_energy_share":float(power[0]/power.sum()),"top5_centered_energy_share":float(power[:5].sum()/power.sum()),"feature_total_variance":float(power.sum()/len(x)),"any_head_norm_0p1_prediction_std_upper_bound_usd":float(10*s[0]/len(x)**.5),"any_head_norm_0p1_pair_difference_upper_bound_usd":float(10*torch.cdist(x.double(),x.double()).max()),"label_std_usd":float((100*y).std(correction=0))}
    json_write(study/"geometry.json",geometry);comparisons={};candidates=[]
    for name in ARMS:
        tensors=recipes[name];directory=study/"candidates"/name;path=directory/"value-head.safetensors"
        if not path.exists():tensors_write(path,tensors)
        loaded=load_file(str(path))
        if any(not torch.equal(loaded[k],tensors[k]) for k in tensors):raise RuntimeError("CANDIDATE_READBACK:"+name)
        prediction=head_forward(loaded,x);result=metric(prediction.numpy(),(100*y).numpy());comparisons[name]=result
        coords={key:(v.reshape(-1)!=parent[key].reshape(-1)).nonzero().flatten().tolist() for key,v in loaded.items()}
        norm=float(torch.cat([loaded[k].flatten().double() for k in sorted(loaded)]).norm())
        delta=float(torch.cat([(loaded[k]-parent[k]).flatten().double() for k in sorted(loaded)]).norm())
        patch={"schema":"heptatrader.weight-head-patch.v1","name":name,"candidate_path":str(path),"sha256":sha(path),"base_head_sha256":manifest["files"]["value-head.safetensors"]["sha256"],"dataset_sha256":manifest["files"]["dataset-selected.json"]["sha256"],"feature_profile":PROFILE,"backend":BACKEND,"dtype":"float32","changed_coordinates":coords,"changed_parameter_count":sum(map(len,coords.values())),"parameter_l2":norm,"delta_l2":delta,"kind":"analytic_baseline" if name.startswith("constant_") else "research_candidate","joint_parameter_norm_le_0p1":norm<=.100001,"labels_used":name not in ("parent","common_direction_half_ablation"),"formula":"raw_USD=100*Linear768(features)","module_forward_and_formula_identical":True,"research_only":True,"labels_previously_viewed":True,"prospective_efficacy_claim":False,"trade_authority":False}
        if not name.startswith("constant_") and norm>.100001:raise RuntimeError("RESEARCH_HEAD_NORM_CAP:"+name)
        json_write(directory/"patch.json",patch);json_write(directory/"metrics.json",dict(scope="KNOWN_54_DEVELOPMENT_ROWS_NOT_BLIND",**result))
        candidates.append({k:patch[k] for k in ("name","candidate_path","sha256","kind","parameter_l2","changed_parameter_count")})
    probes=[]
    for receipt in json.loads((study/"features/probes.json").read_text()):
        if not receipt.get("supported",True):
            probes.append(receipt);continue
        p=Path(receipt["path"])
        if sha(p)!=receipt["file_sha256"]:raise RuntimeError("PROBE_HASH")
        probe=load_file(str(p))["features"];original=x[receipt["row_index"]:receipt["row_index"]+1]
        probes.append({"row_index":receipt["row_index"],"variant":receipt["variant"],"feature_delta_l2":float((probe-original).norm()),"cosine":float((probe*original).sum()),"delta_prediction_usd":{name:float((head_forward(v,probe)-head_forward(v,original))[0]) for name,v in recipes.items()},"interpretation":"INPUT_SENSITIVITY_ONLY_NO_NEW_OUTCOME_LABEL"})
    json_write(study/"sensitivity.json",probes)
    # All 54 labels were already inspected. No pseudo-blind split or selection.
    before=head_forward(parent,x);recorded=torch.tensor(candidate["diagnostic"]["after_scaled"])*100
    shift=recipes["bias_shift_mean"]["value.bias"]-parent["value.bias"]
    actual_delta=head_forward(recipes["bias_shift_mean"],x)-before
    report={"schema":"heptatrader.current100k-weight-study.v1","rows":len(rows),"feature_profile":PROFILE,"backend":BACKEND,"feature_sha256":sha(fp),"geometry":geometry,"fit_seconds":fit_seconds,"fit_info":info,"comparisons":comparisons,"cpu_vs_recorded_gpu_prediction_max_abs_usd":float((before-recorded).abs().max()),"bias_delta_scaled":float(shift[0]),"bias_actual_forward_delta_max_error_usd":float((actual_delta-100*shift).abs().max()),"candidates":candidates,"research_only":True,"labels_previously_viewed":True,"future_evaluation_required":True,"automatic_selection":False,"broker_orders_sent":0,"output_limit_usd":15.,"output_note":"Development raw scores; prospective collector applies its pinned bound uniformly."}
    json_write(study/"summary.json",report)
    bundle={"schema":"heptatrader.current100k-weight-candidates.v1","feature_profile":PROFILE,"backend":BACKEND,"input_width":768,"output_scale_usd":100.,"base_id":candidate["base_id"],"source_dataset_sha256":manifest["files"]["dataset-selected.json"]["sha256"],"training_input_ids":[r["input_id"] for r in rows],"training_reward_ids":[r["reward_id"] for r in rows],"training_input_cutoff_ms":max(r["input_started_at_ms"] for r in rows),"candidates":candidates,"research_only":True,"trade_authority":False,"labels_previously_viewed":True}
    json_write(study/"candidate-bundle.json",bundle)
    log("analysis_complete",geometry=geometry,comparisons=comparisons,cpu_vs_recorded_gpu_prediction_max_abs_usd=report["cpu_vs_recorded_gpu_prediction_max_abs_usd"])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("mode",choices=("extract","analyze"));p.add_argument("--study",required=True);p.add_argument("--runtime");p.add_argument("--base");p.add_argument("--threads",type=int,choices=(1,2),default=2);args=p.parse_args()
    if args.mode=="extract" and (not args.runtime or not args.base):p.error("extract requires --runtime and --base")
    os.umask(0o077);{"extract":extract,"analyze":analyze}[args.mode](args)


if __name__=="__main__":main()
