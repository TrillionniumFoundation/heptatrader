"""Read-only preaction FX shadow eligibility; historical backtests confer no authority."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

SCHEMA = "heptatrader.fx-prospective-cost-admission.v1"
class GateError(ValueError): pass

def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), allow_nan=False)
def sha(o):
    return hashlib.sha256(canonical(o).encode()).hexdigest()
def exact(d, fields, name):
    if type(d) is not dict or set(d) != set(fields):
        raise GateError(name)
    return d
def num(x):
    if type(x) not in (float, int) or not __import__("math").isfinite(x):
        raise GateError("NONFINITE_OR_UNTYPED_COST")
    return float(x)
def evaluate(plan, live, candidate, at_ms):
    """Decision is shadow-only, never a native prepared order or PnL label."""
    exact(plan, ("schema","contract","candidate_sha256","history_sha256",
                 "cost_roundtrip_bps","min_net_edge_bps","max_quote_age_ms",
                 "max_forecast_age_ms","registered_before_ms","shadow_only"), "PLAN_FIELDS")
    if plan["schema"] != SCHEMA or plan["contract"] != "EUR.USD" or plan["shadow_only"] is not True:
        raise GateError("PLAN_SCOPE")
    for k in ("candidate_sha256","history_sha256"):
        v=plan[k]
        if type(v) is not str or len(v)!=64 or any(c not in "0123456789abcdef" for c in v):
            raise GateError("PLAN_SHA")
    if type(at_ms) is not int or type(plan["registered_before_ms"]) is not int:
        raise GateError("CLOCK_TYPE")
    if plan["registered_before_ms"] >= at_ms:
        raise GateError("PLAN_NOT_PRECOMMITTED")
    for k in ("cost_roundtrip_bps","min_net_edge_bps"):
        if num(plan[k]) < 0: raise GateError("NEGATIVE_COST")
    for k in ("max_quote_age_ms","max_forecast_age_ms"):
        if type(plan[k]) is not int or not 0 < plan[k] <= 60000:
            raise GateError("INVALID_TTL")
    reasons=[]
    # No historical-only threshold may be silently admitted.
    if live is None or candidate is None:
        reasons.append("NO_NEW_ORIGINAL_PREACTION_QUOTE_OR_FORECAST")
        return {"schema":SCHEMA,"state":"HOLD","reasons":reasons,
                "shadow_action":"NO_TRADE","trade_authority":False,
                "model_promotion_allowed":False,"plan_sha256":sha(plan)}
    exact(live, ("instrument","bid","ask","observed_at_ms","available_at_ms",
                 "expires_at_ms","source","authoritative","source_sha256"), "QUOTE_FIELDS")
    exact(candidate, ("instrument","direction","expected_move_bps","issued_at_ms",
                      "available_at_ms","input_quote_sha256","model_sha256",
                      "policy_logged_propensity"), "FORECAST_FIELDS")
    if live["instrument"]!="EUR.USD" or live["source"]!="IB" or live["authoritative"] is not True:
        reasons.append("NONORIGINAL_QUOTE")
    for obj, fields in ((live,("observed_at_ms","available_at_ms","expires_at_ms")),
                        (candidate,("issued_at_ms","available_at_ms"))):
        if any(type(obj[k]) is not int for k in fields):
            raise GateError("CLOCK_TYPE")
    if (not live["observed_at_ms"] <= live["available_at_ms"] <= at_ms < live["expires_at_ms"]
            or at_ms-live["available_at_ms"] > plan["max_quote_age_ms"]):
        reasons.append("QUOTE_EXPIRED_OR_NOT_AVAILABLE")
    if (not candidate["issued_at_ms"] <= candidate["available_at_ms"] <= at_ms
            or at_ms-candidate["available_at_ms"] > plan["max_forecast_age_ms"]):
        reasons.append("FORECAST_EXPIRED_OR_NOT_AVAILABLE")
    if candidate["instrument"]!="EUR.USD" or candidate["model_sha256"] != plan["candidate_sha256"]:
        reasons.append("CANDIDATE_NOT_PINNED")
    if live["source_sha256"]!=candidate["input_quote_sha256"]:
        reasons.append("QUOTE_NOT_BOUND_TO_FORECAST")
    if candidate["policy_logged_propensity"] is not True:
        reasons.append("BEHAVIOR_PROPENSITY_NOT_ATTESTED")
    if candidate["direction"] not in ("BUY","SELL"):
        reasons.append("NO_DIRECTION")
    bid,ask=num(live["bid"]),num(live["ask"])
    if not (0 < bid <= ask): raise GateError("CROSSED_OR_ZERO_QUOTE")
    expected=num(candidate["expected_move_bps"])
    spread=(ask-bid)/((ask+bid)/2)*10000
    # A single contemporaneous spread is charged once; calibrated remaining
    # costs are frozen in plan, and are NOT an execution slippage attestation.
    edge=abs(expected)-spread-num(plan["cost_roundtrip_bps"])
    if expected == 0 or (expected>0 and candidate["direction"]!="BUY") or (expected<0 and candidate["direction"]!="SELL"):
        reasons.append("DIRECTION_FORECAST_MISMATCH")
    if edge <= num(plan["min_net_edge_bps"]):
        reasons.append("EDGE_DOES_NOT_CLEAR_COST")
    # Untrusted caller-supplied JSON never proves its own original-IB
    # provenance, prospective registration or logged behavior propensity.
    # This CLI is only a bounded arithmetic diagnostic. Independently
    # verified original quote-chain and native signed owner admissions are
    # separate, unavailable contracts; do not manufacture REVIEW_ONLY.
    reasons.append("CALLER_JSON_NOT_ORIGINAL_SIGNED_PROVENANCE")
    return {"schema":SCHEMA,"state":"HOLD",
            "reasons":reasons,"shadow_action":"NO_TRADE",
            "hypothetical_direction_field_only":candidate["direction"],
            "caller_flags_cannot_grant_review":True,
            "expected_edge_after_cost_bps":round(edge,8),
            "spread_bps":round(spread,8),"trade_authority":False,
            "model_promotion_allowed":False,"plan_sha256":sha(plan)}

def main():
    p=argparse.ArgumentParser()
    for n in ("plan","output"): p.add_argument("--"+n,type=Path,required=True)
    p.add_argument("--live",type=Path)
    p.add_argument("--candidate",type=Path)
    a=p.parse_args()
    if bool(a.live) != bool(a.candidate): raise SystemExit("LIVE_AND_CANDIDATE_MUST_BOTH_BE_PRESENT")
    load=lambda path:json.loads(path.read_text())
    result=evaluate(load(a.plan),load(a.live) if a.live else None,
                    load(a.candidate) if a.candidate else None,
                    int(__import__("time").time()*1000))
    a.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(a.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"w") as f:
        f.write(canonical(result)+"\n");f.flush();os.fsync(f.fileno())
    print(canonical(result))
if __name__=="__main__":main()
