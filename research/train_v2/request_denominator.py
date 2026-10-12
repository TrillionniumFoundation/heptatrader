"""Captures original MODEL REQUEST attempts, not just successful returns.

The shadow measurement is research-only. An observed LLM REQUEST is NOT every
strategy opportunity, and an original returned BUY/SELL is NOT an executable
risk/OMS policy decision or a logged stochastic propensity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from hepta_decision_attribution import read_private, strict
from .contracts import ContractError, digest, is_digest
from .original_decision_population import _scan_original, _request_id, _graph_decision, MAX_MANIFESTS
from .original_policy_audit import verify_release
from .postmodel_requote import load_original_quote_archive, source_model_returns, analyze_join

SCHEMA="heptatrader.train-v2-original-request-denominator.v1"
KINDS=("REQUEST","RESPONSE","DECISION")
REASONS=("NO_RESPONSE_NO_DECISION","NO_RESPONSE","NO_DECISION",
         "FULL_GRAPH_UNQUALIFIED","VALID_GRAPH")

def classify_producer(rows,instrument,period):
    """One verified original journal prefix. No missing row becomes NO_TRADE."""
    groups=defaultdict(dict)
    for row in rows:
        kind=row["kind"]
        if kind not in KINDS:
            continue
        rid=_request_id(row["data"])
        if kind in groups[rid]:
            raise ContractError("REQUEST_DENOMINATOR_DUPLICATE_KIND")
        groups[rid][kind]=(row["data"],row["sequence"])
    request_count=0
    valid=0
    unresolved=Counter()
    orphan=Counter()
    for group in groups.values():
        if "REQUEST" not in group:
            orphan["ORPHAN_NO_REQUEST"]+=1
            continue
        request_count+=1
        missing=set(KINDS)-set(group)
        if missing:
            if missing=={"RESPONSE","DECISION"}:
                unresolved["NO_RESPONSE_NO_DECISION"]+=1
            elif "RESPONSE" in missing:
                unresolved["NO_RESPONSE"]+=1
            else:
                unresolved["NO_DECISION"]+=1
            continue
        try:
            action,_=_graph_decision(
                *(group[name][0] for name in KINDS),
                *(group[name][1] for name in KINDS),
                instrument,period)
            if action not in ("BUY","SELL","HOLD"):
                raise ValueError("UNREGISTERED_ORIGINAL_MODEL_ACTION")
        except (ValueError,KeyError,IndexError,TypeError,AttributeError):
            unresolved["FULL_GRAPH_UNQUALIFIED"]+=1
            continue
        valid+=1
    if request_count != valid+sum(unresolved.values()):
        raise ContractError("REQUEST_DENOMINATOR_NOT_CONSERVED")
    return {
        "captured_request_events":request_count,
        "verified_original_model_returns":valid,
        "captured_unresolved_requests":dict(sorted(unresolved.items())),
        "orphan_event_groups_without_request":dict(sorted(orphan.items())),
        "candidate_set_complete":False,
        "model_behavior_propensities_known":False,
        "whole_policy_opportunity_population_complete":False,
    }

def source_model_attempts(releases_root,period_sha,original_cache):
    """Exact scoped manifests; share SAME journal-prefix cache with returns."""
    root=Path(releases_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ContractError("REQUEST_RELEASE_ROOT_UNSAFE")
    manifests=sorted(root.glob("*/*/manifest.json"))
    if not 1<=len(manifests)<=MAX_MANIFESTS:
        raise ContractError("REQUEST_RELEASE_MANIFEST_BOUND")
    per=[];seen=set()
    for path in manifests:
        m=strict(read_private(path,65536))
        if m.get("instrument")!="EUR.USD":
            continue
        if m.get("period_sha256")!=period_sha:
            raise ContractError("REQUEST_RELEASE_PERIOD_MISMATCH")
        verified=verify_release(path)
        if hashlib.sha256(read_private(path,65536)).hexdigest()!=verified["manifest_sha256"]:
            raise ContractError("REQUEST_RELEASE_MANIFEST_CHANGED")
        directory=Path(m["journal_directory"])
        if directory not in original_cache:
            original_cache[directory]=_scan_original(directory)
        reader=original_cache[directory]
        selected=[p for p in reader["producers"]
                  if p["manifest_sha256"]==verified["manifest_sha256"]]
        if not selected:
            raise ContractError("REQUEST_RELEASE_MISSING_PRODUCER")
        totals=Counter();missing=Counter();orphan=Counter()
        health=Counter()
        for producer in selected:
            # A request identity is unique in this ORIGINAL period even when
            # a different release or producer records it.
            for row in producer["rows"]:
                if row["kind"]=="REQUEST":
                    rid=_request_id(row["data"])
                    identity=(period_sha,rid)
                    if identity in seen:
                        raise ContractError("REQUEST_CROSS_RELEASE_OR_PRODUCER_REUSE")
                    seen.add(identity)
            result=classify_producer(producer["rows"],"EUR.USD",period_sha)
            totals["captured_request_events"]+=result["captured_request_events"]
            totals["verified_original_model_returns"]+=result["verified_original_model_returns"]
            missing.update(result["captured_unresolved_requests"])
            orphan.update(result["orphan_event_groups_without_request"])
            if producer["local_drops"]:
                health["CAPTURE_LOCAL_DROPPED"]+=1
            if producer["health_dropped_or_failed"] is True:
                health["CAPTURE_PRODUCER_DROPPED_OR_FAILED"]+=1
            if producer["health_dropped_or_failed"] is None:
                health["CAPTURE_HEALTH_UNATTESTED"]+=1
        per.append({
            "original_manifest_sha256":verified["manifest_sha256"],
            "captured_request_events":totals["captured_request_events"],
            "verified_original_model_returns":totals["verified_original_model_returns"],
            "unresolved_original_request_reasons":dict(sorted(missing.items())),
            "orphan_without_request_not_in_denominator":dict(sorted(orphan.items())),
            "source_observer_health_issues":dict(sorted(health.items())),
            "journal_prefix_inventory_sha256":digest(reader["files"]),
            "reported_capture_bytes":reader["verified_total_prefix_bytes"],
            "true_policy_opportunity_population_attested":False,
        })
    if not per:
        raise ContractError("REQUEST_NO_ORIGINAL_FX_RELEASES")
    return per

def reconcile_denominators(attempts,quote_result):
    counts=quote_result.get("observations")
    if not isinstance(counts,dict):
        raise ContractError("REQUEST_MODEL_QUOTE_RESULT_INVALID")
    model_count=counts.get("verified_original_model_returns")
    by_manifest=quote_result.get("per_release")
    if type(model_count)is not int or model_count<0 or not isinstance(by_manifest,dict):
        raise ContractError("REQUEST_QUOTE_SCOPE_MISSING")
    if (quote_result.get("scope")!="RETROSPECTIVE_READONLY_UPPER_BOUND_NOT_ORIGINAL_LLM_POLICY"
        or quote_result.get("promotion_allowed") is not False
        or quote_result.get("original_full_action_propensities_known") is not False
        or quote_result.get("source_attested_as_broker_signed") is not False
        or quote_result.get("broker_orders_sent")!=0):
        raise ContractError("REQUEST_REQUOTE_SOURCE_POLICY_NOT_RESEARCH_ONLY")
    fields=("fresh_local_quote_asof_model_return_upper_bound",
            "no_original_local_fresh_quote_known_at_model_return",
            "outside_attested_sealed_source_coverage_unknown",
            "in_attested_sealed_source_time_bounds",
            "verified_original_model_returns")
    if any(type(counts.get(field)) is not int or counts[field]<0 for field in fields):
        raise ContractError("REQUEST_REQUOTE_COUNTERS_INVALID")
    fresh=counts["fresh_local_quote_asof_model_return_upper_bound"]
    nofresh=counts["no_original_local_fresh_quote_known_at_model_return"]
    out=counts["outside_attested_sealed_source_coverage_unknown"]
    covered=counts["in_attested_sealed_source_time_bounds"]
    if covered!=fresh+nofresh or covered+out!=model_count:
        raise ContractError("REQUEST_REQUOTE_POPULATION_NOT_CONSERVED")
    action_counts=[counts.get("fresh_returned_"+action,0)
                   for action in ("BUY","SELL","HOLD")]
    if (any(type(value) is not int or value<0 for value in action_counts)
        or sum(action_counts)!=fresh):
        raise ContractError("REQUEST_REQUOTE_ACTION_SUBSET_MISMATCH")
    manifest_ids=[item.get("original_manifest_sha256") for item in attempts]
    if not attempts or any(not is_digest(value) for value in manifest_ids):
        raise ContractError("REQUEST_MANIFEST_ID_MISSING")
    if len(set(manifest_ids))!=len(manifest_ids):
        raise ContractError("REQUEST_MANIFEST_REPEATED")
    if set(manifest_ids)!=set(by_manifest):
        raise ContractError("REQUEST_RELEASE_SET_OR_MODEL_COUNT_DRIFT")
    total_attempts=0
    completed=0
    unresolved=Counter()
    orphan=Counter()
    ids=set()
    for release in attempts:
        identity=release["original_manifest_sha256"]
        if identity in ids:
            raise ContractError("REQUEST_MANIFEST_REPEATED")
        ids.add(identity)
        request_total=release["captured_request_events"]
        done=release["verified_original_model_returns"]
        if (type(request_total)is not int or type(done)is not int
            or done<0 or request_total<done):
            raise ContractError("REQUEST_POPULATION_NEGATIVE_OR_UNBOUNDED")
        other=release["unresolved_original_request_reasons"]
        if (any(k not in REASONS[:-1] or type(v)is not int or v<0
                for k,v in other.items())
            or done+sum(other.values())!=request_total):
            raise ContractError("REQUEST_UNRESOLVED_NOT_CONSERVED")
        observed_manifest=by_manifest.get(identity,{})
        if type(observed_manifest.get("verified_model_returns")) is not int:
            raise ContractError("REQUEST_MANIFEST_COUNT_NOT_INTEGER")
        if observed_manifest["verified_model_returns"]!=done:
            raise ContractError("REQUEST_MODEL_RETURNS_SNAPSHOT_DRIFT")
        total_attempts+=request_total
        completed+=done
        unresolved.update(other)
        orphan.update(release["orphan_without_request_not_in_denominator"])
    if len(by_manifest)!=len(ids) or completed!=model_count:
        raise ContractError("REQUEST_RELEASE_SET_OR_MODEL_COUNT_DRIFT")
    classified=sum(counts.get(k,0) for k in (
        "fresh_local_quote_asof_model_return_upper_bound",
        "no_original_local_fresh_quote_known_at_model_return",
        "outside_attested_sealed_source_coverage_unknown"))
    if classified!=completed:
        raise ContractError("REQUEST_REQUOTE_POPULATION_NOT_CONSERVED")
    if total_attempts<completed:
        raise ContractError("REQUEST_DENOMINATOR_INVALID")
    return {
        "captured_original_model_requests_lower_bound":total_attempts,
        "fully_verified_original_model_returns":completed,
        "original_requests_without_qualified_model_return":total_attempts-completed,
        "incomplete_or_invalid_request_reason_counts":dict(sorted(unresolved.items())),
        "orphan_fragment_groups_not_requests":dict(sorted(orphan.items())),
        "fresh_postmodel_quote_upper_bound":counts.get(
            "fresh_local_quote_asof_model_return_upper_bound",0),
        "verified_model_return_without_fresh_quote":counts.get(
            "no_original_local_fresh_quote_known_at_model_return",0),
        "verified_model_return_outside_sealed_quote_coverage_unknown":counts.get(
            "outside_attested_sealed_source_coverage_unknown",0),
        "request_population_is_all_trade_opportunities":False,
        "missing_or_failed_returns_are_not_zero_profit":True,
        "return_success_only_selection_bias_excluded":True,
        "original_model_actions_are_new_policy_candidates":False,
        "selection_propensity_attested":False,
        "economic_alpha_qualified":False,
        "broker_orders_sent":0,
    }

def test_source_sha256():
    test_path=Path(__file__).resolve().parents[2]/"tests/python/test_train_v2_request_denominator.py"
    if test_path.is_symlink() or not test_path.is_file() or test_path.stat().st_size>128000:
        raise ContractError("REQUEST_ADVERSARIAL_TEST_SOURCE_MISSING")
    return hashlib.sha256(test_path.read_bytes()).hexdigest()

def run(period_file,quote_root,releases_root):
    from historical_training.common import sha
    period=sha(period_file)
    archive=load_original_quote_archive(quote_root,period)
    original_cache={}
    captured=source_model_attempts(releases_root,period,original_cache)
    returns,release_summary,missing=source_model_returns(
        releases_root,period,original_cache=original_cache)
    quote_result=analyze_join(archive,returns,release_summary,missing)
    reconciliation=reconcile_denominators(captured,quote_result)
    report={
        "schema":SCHEMA,
        "observed_at_utc":datetime.now(timezone.utc).isoformat(),
        "original_period_sha256":period,
        "original_release_captured_attempts":captured,
        "reconciliation":reconciliation,
        "quote_archive":{key:value for key,value in archive.items()
                         if key not in ("rows","known_times")},
        "original_model_requote_research_scope":quote_result["scope"],
        "quote_audit_code_sha256":hashlib.sha256(
            Path(__file__).with_name("postmodel_requote.py").read_bytes()).hexdigest(),
        "auditor_code_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "adversarial_test_sha256":test_source_sha256(),
        "new_forward_trial_created":False,
        "model_inference_performed":False,
        "root_signed_product_generator_enrolled":False,
        "complete_risk_policy_opportunity_set_attested":False,
        "true_logged_behavior_propensity_attested":False,
        "original_request_prepared_trade_binding_attested":False,
        "current_artifact_owner_selection_admitted":False,
        "after_cost_pnl_efficacy_attested":False,
        "broker_queries_sent":0,"broker_orders_sent":0,
        "promotion_allowed":False,
        "trade_authority":False,
    }
    return report

def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("period","quote-source-root","original-release-root","output"):
        p.add_argument("--"+name,type=Path,required=True)
    args=p.parse_args();os.umask(0o077)
    target=args.output
    # An already frozen receipt must never cause a second expensive scan
    # or a partially updated retrospective population on accidental retry.
    if target.is_symlink() or target.exists():
        raise ContractError("REQUEST_IMMUTABLE_NEW_RECEIPT_REQUIRED")
    result=run(args.period,args.quote_source_root,args.original_release_root)
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    import stat
    st=target.parent.stat()
    if (target.parent.is_symlink() or not stat.S_ISDIR(st.st_mode)
        or st.st_uid!=os.geteuid() or st.st_mode & 0o077):
        raise ContractError("REQUEST_OUTPUT_DIRECTORY_NOT_PRIVATE")
    raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    dirfd=os.open(target.parent,os.O_DIRECTORY|os.O_RDONLY|os.O_NOFOLLOW)
    try:os.fsync(dirfd)
    finally:os.close(dirfd)
    print(json.dumps({
        "scope":"CAPTURED_ORIGINAL_MODEL_REQUESTS_ONLY_NOT_ALL_POLICY_OPPORTUNITIES",
        "captured_requests":result["reconciliation"]["captured_original_model_requests_lower_bound"],
        "verified_model_returns":result["reconciliation"]["fully_verified_original_model_returns"],
        "nonreturned_or_invalid":result["reconciliation"]["original_requests_without_qualified_model_return"],
        "new_quote_upper_bound":result["reconciliation"]["fresh_postmodel_quote_upper_bound"],
        "true_original_policy_propensity_attested":False,
        "broker_orders_sent":0,
        "receipt_sha256":hashlib.sha256(raw).hexdigest()
    },sort_keys=True))

if __name__=="__main__":
    main()
