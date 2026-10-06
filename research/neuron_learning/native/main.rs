//! Non-authorizing adapter over existing Hepta native mechanisms, not a replacement.
//! No broker/client/credential I/O. JSON stdin is bounded; all outputs deny effects.
use std::io::{self, Read};
use codex_hepta_types::{Digest32, FixedQ32, Generation, StableId};
use codex_hepta_neuron::{SparseConfig, SparseTick, sparse_tick};
use codex_hepta_ndu::{RecursiveUtilityPath, UtilityEvent, evaluate_recursive_utility};
use codex_hepta_plasticity::*;
use serde_json::{Value, json};
type E = Box<dyn std::error::Error>;
const Q24: f64 = 16777216.0;
const Q32: f64 = 4294967296.0;
fn fail<T>(msg: &str) -> Result<T,E> { Err(msg.to_string().into()) }
fn hash(v:&Value)->Digest32 {Digest32::of_bytes(&serde_json::to_vec(v).expect("bounded JSON"))}
fn hex(v:&Value)->Result<Digest32,E> {let d:Digest32=v.as_str().ok_or("DIGEST_REQUIRED")?.parse()?;if d.is_zero(){return fail("ZERO_DIGEST");}Ok(d)}
fn id(s:&str)->Result<StableId,E>{Ok(StableId::new(s)?)}
fn num(v:&Value)->Result<f64,E>{let n=v.as_f64().ok_or("NUMBER_REQUIRED")?;if !n.is_finite()||n.abs()>8.0{return fail("NUMBER_BOUND");}Ok(n)}
fn q(v:f64)->FixedQ32 {FixedQ32::from_raw((v*Q32).round_ties_even() as i64)}
fn check_fields(v:&Value,fields:&[&str])->Result<(),E>{let o=v.as_object().ok_or("OBJECT_REQUIRED")?;if o.keys().any(|k|!fields.contains(&k.as_str())){return fail("UNKNOWN_CRITICAL_FIELD");}Ok(())}
fn neuron(v:&Value)->Result<Value,E>{
 check_fields(v,&["op","episodes"])?;
 let episodes=v["episodes"].as_array().ok_or("EPISODES_REQUIRED")?;
 if episodes.is_empty()||episodes.len()>512{return fail("EPISODE_BOUND");}
 let cfg=SparseConfig{model_digest:Digest32::of_bytes(b"heptatrader-native-neuron16-v1"),
 normalization_digest:Digest32::of_bytes(b"causal-fixed-scale-features16-v1"),generation:Generation::new(1)?,
 width:16,top_k:3,temporal_decay_q24:(Q24*0.75) as i64,inhibition_gain_q24:0,inhibition:vec![],
 activity_decay_q24:(Q24*0.9) as i64,target_activity_q24:(Q24*0.15) as i64,
 threshold_rate_q24:(Q24*0.01) as i64,threshold_min_q24:0,threshold_max_q24:Q24 as i64,
 eligibility_decay_q24:(Q24*0.9) as i64};
 let mut outputs=Vec::new();
 for ep in episodes{
  check_fields(ep,&["scope","rows","times"])?;
  let scope=ep["scope"].as_str().ok_or("SCOPE_REQUIRED")?;
  if scope.len()>256||scope.is_empty(){return fail("SCOPE_BOUND");}
  let rows=ep["rows"].as_array().ok_or("ROWS_REQUIRED")?;
  let times=ep["times"].as_array().ok_or("TIMES_REQUIRED")?;
  if rows.is_empty()||rows.len()>24||rows.len()!=times.len(){return fail("HISTORY_BOUND");}
  let mut previous=None;let mut last=0;let mut final_signal=None;
  for (i,row) in rows.iter().enumerate(){
   let vals=row.as_array().ok_or("ROW_REQUIRED")?;if vals.len()!=16{return fail("WIDTH");}
   let at=times[i].as_u64().ok_or("TIME_REQUIRED")?;
   if at<=last||at>20_000_000_000_000{return fail("TIME_ORDER");}last=at;
   let drive=vals.iter().map(|x|num(x).map(|f|(f*Q24).round_ties_even() as i64)).collect::<Result<Vec<_>,_>>()?;
   let tick=SparseTick{scope_digest:Digest32::of_bytes(scope.as_bytes()),
    objective_digest:Digest32::of_bytes(b"predict-future-mid-return"),
    ndu_digest:Digest32::of_bytes(b"fixed-negative-huber-utility-v1"),
    body_digest:cfg.model_digest,input_digest:hash(&json!({"row":row,"at":at})),
    sequence:i as u64+1,monotonic_micros:at*1000,drive_q24:drive,prediction_q24:vec![0;16]};
   let (state,receipt)=sparse_tick(&cfg,&tick,previous.as_ref())?;previous=Some(state);final_signal=Some(receipt);
  }
  let state=previous.ok_or("NO_STATE")?;let signal=final_signal.ok_or("NO_SIGNAL")?;
  if signal.authority.grants_any(){return fail("AUTHORITY_UNEXPECTED");}
  outputs.push(json!({"activation":signal.activation_q24.iter().map(|x|*x as f64/Q24).collect::<Vec<_>>(),
   "checkpoint_digest":state.digest().to_string(),"eligibility_digest":state.eligibility_digest().to_string(),
   "eligibility_q24":state.eligibility_q24(),"requires_calibration":signal.requires_calibration,
   "config_digest":signal.config_digest.to_string(),"effect_authority":false}));
 }
 Ok(json!({"episodes":outputs,"native_function":"codex_hepta_neuron::sparse_tick"}))
}
fn utility(v:&Value)->Result<Value,E>{
 check_fields(v,&["op","events","dataset_digest","objective_digest","outcome_digest"])?;
 let input=v["events"].as_array().ok_or("EVENTS_REQUIRED")?;
 if input.is_empty()||input.len()>512{return fail("UTILITY_HORIZON");}
 let objective=hex(&v["objective_digest"])?;let dataset=hex(&v["dataset_digest"])?;
 let outcome=hex(&v["outcome_digest"])?;
 let mut events=Vec::new();
 for (i,x) in input.iter().enumerate(){
  check_fields(x,&["instant","event_digest","discount"])?;
  let d=num(&x["discount"])?;if !(0.0..=1.0).contains(&d){return fail("DISCOUNT");}
  events.push(UtilityEvent{sequence:i as u64+1,event_digest:hex(&x["event_digest"])?,preference_digest:objective,
   instant:q(num(&x["instant"])?),discount:q(d)});
 }
 let path=RecursiveUtilityPath{objective_digest:objective,episode_digest:dataset,
 coefficient_digest:Digest32::of_bytes(b"fixed-discount-huber-units-v1"),units_digest:Digest32::of_bytes(b"return-divided-by-100bps"),
 terminal_outcome_digest:outcome,terminal:FixedQ32::ZERO,lower:q(-8.0),upper:q(8.0),events};
 let result=evaluate_recursive_utility(&path)?;
 if result.authority.grants_any(){return fail("AUTHORITY_UNEXPECTED");}
 Ok(json!({"value":result.values[0].raw() as f64/Q32,"values_q32":result.values.iter().map(|x|x.raw()).collect::<Vec<_>>(),
   "receipt_digest":result.evidence_digest.to_string(),"projection_count":result.projection_count,
   "effect_authority":false,"native_function":"codex_hepta_ndu::evaluate_recursive_utility"}))
}
fn proposal(v:&Value)->Result<Value,E>{
 check_fields(v,&["op","baseline_q32","candidate_q32","generation","dataset_digest","update_rule_digest",
 "modulator_digest","eligibility_digest","evaluation_digest"])?;
 let base=v["baseline_q32"].as_array().ok_or("BASE_REQUIRED")?;
 let cand=v["candidate_q32"].as_array().ok_or("CANDIDATE_REQUIRED")?;
 if base.len()!=562||cand.len()!=562{return fail("HEAD_PARAMETER_SHAPE");}
 let baseline=base.iter().map(|x|x.as_i64().ok_or("Q32_REQUIRED")).collect::<Result<Vec<_>,_>>()?;
 let candidate=cand.iter().map(|x|x.as_i64().ok_or("Q32_REQUIRED")).collect::<Result<Vec<_>,_>>()?;
 if baseline.iter().chain(candidate.iter()).any(|x|x.unsigned_abs()>8*(1_u64<<32)){return fail("PARAMETER_BOUND");}
 let selected=hash(&json!(baseline));let dataset=hex(&v["dataset_digest"])?;
 let generation=Generation::new(v["generation"].as_u64().ok_or("GENERATION_REQUIRED")?)?;
 let mut layers=Vec::new();let mut deltas=Vec::new();
 for (name,start,end) in [("hidden",0,528),("output",528,562)]{
  let den=baseline[start..end].iter().try_fold(0_u128,|sum,x|sum.checked_add((*x as i128).unsigned_abs().pow(2)).ok_or("NORM_OVERFLOW"))?;
  layers.push(LayerNormDenominatorV2{layer_id:id(name)?,baseline_squared_l2_raw_q64:den});
  for i in start..end{let delta=candidate[i]-baseline[i];if delta!=0{
   deltas.push(ParameterDeltaV2{layer_id:id(name)?,parameter_id:id(&format!("p{i}"))?,delta:FixedQ32::from_raw(delta),
    lower_bound:q(-0.0625),upper_bound:q(0.0625),evidence_digest:dataset});
  }}
 }
 let mut candidates=vec![ParameterCandidateRequestV2{candidate_id:id("no-change")?,kind:ParameterCandidateKindV2::NoChange,parameter_deltas:vec![]}];
 if !deltas.is_empty(){candidates.push(ParameterCandidateRequestV2{candidate_id:id("gpu-head-candidate")?,kind:ParameterCandidateKindV2::Update,parameter_deltas:deltas});}
 let result=propose_v2(ParameterProposalRequestV2{proposal_id:id(&format!("trader-{}-{}",generation.get(),&dataset.to_string()[..16]))?,
 proposer_id:id("pocket4-gpu-supervised-trainer")?,evaluator_id:id("native-structural-norm-check-not-efficacy")?,
 selected_artifact_digest:selected,window:ProposalWindowV2{window_id:id(&format!("dataset-{}",&dataset.to_string()[..16]))?,window_digest:dataset},
 baseline_generation:generation,candidate_generation:generation.next()?,dataset_digest:dataset,
 update_rule_digest:hex(&v["update_rule_digest"])?,modulator_digest:hex(&v["modulator_digest"])?,
 modulator_broadcast_digest:Digest32::of_bytes(b"supervised-head-gradient-no-arbitrary-neuron-broadcast-v1"),
 eligibility_digest:hex(&v["eligibility_digest"])?,evaluation_digest:hex(&v["evaluation_digest"])?,
 rollback_predecessor_digest:selected,norm_layers:layers,candidates})?;
 verify_parameter_proposal_v2(&result)?;
 if result.authority.grants_any(){return fail("AUTHORITY_UNEXPECTED");}
 Ok(json!({"proposal_digest":result.proposal_digest.to_string(),"candidate_count":result.candidates.len(),
 "status":"RequiresIndependentAcceptance","effect_authority":false,"selection_performed":false,
 "per_layer_max_relative_ppm":result.norm_profile.per_layer_max_relative_ppm,
 "global_max_relative_ppm":result.norm_profile.global_max_relative_ppm,
 "native_function":"codex_hepta_plasticity::propose_v2"}))
}
fn run(v:&Value)->Result<Value,E>{match v["op"].as_str(){Some("neuron")=>neuron(v),Some("utility")=>utility(v),Some("proposal")=>proposal(v),
 Some("identity")=>Ok(json!({"modules":["hepta-neuron","hepta-ndu","hepta-plasticity"],"source_sha":env!("HEPTA_NATIVE_SOURCE_SHA"),"broker_orders_sent":0})),_=>fail("UNKNOWN_OPERATION")}}
fn main(){let mut raw=Vec::new();let result=(||->Result<Value,E>{io::stdin().take(8*1024*1024+1).read_to_end(&mut raw)?;
 if raw.len()>8*1024*1024{return fail("INPUT_BOUND");}let v:Value=serde_json::from_slice(&raw)?;let mut out=run(&v)?;
 out["native_source_sha"]=json!(env!("HEPTA_NATIVE_SOURCE_SHA"));out["authorization_effect"]=json!("NONE");Ok(out)})();
 match result{Ok(v)=>println!("{}",v),Err(e)=>{println!("{}",json!({"error":e.to_string(),"authorization_effect":"NONE"}));std::process::exit(2)}}}
