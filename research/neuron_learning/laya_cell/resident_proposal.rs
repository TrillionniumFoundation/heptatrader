// Additive Laya operation; native Plasticity retains its existing bounds and ownership.
fn laya_resident_proposal(v:&Value)->Result<Value,E>{
 check_fields(v,&["op","base_id","selected_artifact_digest","generation","dataset_digest","update_rule_digest","modulator_digest","eligibility_digest","evaluation_digest","layers"])?;
 let base=hex(&v["base_id"])?;let selected=hex(&v["selected_artifact_digest"])?;let dataset=hex(&v["dataset_digest"])?;
 let generation=Generation::new(v["generation"].as_u64().ok_or("GENERATION_REQUIRED")?)?;
 let rows=v["layers"].as_array().ok_or("LAYERS_REQUIRED")?;
 if rows.len()!=3{return fail("LAYA_LAYER_COUNT");}
 let mut norm_layers=Vec::new();let mut deltas=Vec::new();let mut seen=std::collections::BTreeSet::new();
 for layer in rows{
  check_fields(layer,&["name","shape","baseline_squared_q64","deltas"])?;
  let name=layer["name"].as_str().ok_or("LAYER_NAME")?;let shape=layer["shape"].as_array().ok_or("LAYER_SHAPE")?;
  let expected_rows=match name{"scorer.1"=>768_u64,"scorer.3"=>1_u64,"act_head.2"=>2_u64,_=>return fail("LAYER_MASK")};
  if !seen.insert(name.to_string())||shape.len()!=2||shape[0].as_u64()!=Some(expected_rows)||shape[1].as_u64()!=Some(if name=="act_head.2"{256}else{768}){return fail("LAYA_PRETRAINED_SHAPE");}
  let denominator:u128=layer["baseline_squared_q64"].as_str().ok_or("NORM_STRING_REQUIRED")?.parse()?;
  if denominator==0||denominator>(1_u128<<120){return fail("NORM_BOUND");}
  norm_layers.push(LayerNormDenominatorV2{layer_id:id(name)?,baseline_squared_l2_raw_q64:denominator});
  let elements=layer["deltas"].as_array().ok_or("DELTAS_REQUIRED")?;
  let mut coordinates=std::collections::BTreeSet::new();
  for item in elements{
   check_fields(item,&["row","col","delta_q32"])?;
   let row=item["row"].as_u64().ok_or("ROW")?;let col=item["col"].as_u64().ok_or("COL")?;
   let row_limit=match name{"scorer.1"=>4,"scorer.3"=>1,_=>2};
   let col_limit=if name=="act_head.2"{64}else{768};
   if row>=row_limit||col>=col_limit||!coordinates.insert((row,col)){return fail("EFFECTIVE_DELTA_MASK");}
   let delta=item["delta_q32"].as_i64().ok_or("DELTA_Q32")?;
   if delta==0{return fail("ZERO_DELTA_MUST_BE_OMITTED");}
   deltas.push(ParameterDeltaV2{layer_id:id(name)?,parameter_id:id(&format!("{name}-r{row}-c{col}"))?,delta:FixedQ32::from_raw(delta),lower_bound:q(-0.0625),upper_bound:q(0.0625),evidence_digest:dataset});
  }
 }
 if deltas.len()>4096{return fail("NATIVE_DELTA_BUDGET");}
 let mut candidates=vec![ParameterCandidateRequestV2{candidate_id:id("no-change")?,kind:ParameterCandidateKindV2::NoChange,parameter_deltas:vec![]}];
 if !deltas.is_empty(){candidates.push(ParameterCandidateRequestV2{candidate_id:id("laya-resident-cell-delta")?,kind:ParameterCandidateKindV2::Update,parameter_deltas:deltas});}
 let request=ParameterProposalRequestV2{proposal_id:id(&format!("laya-resident-{}-{}",generation.get(),&dataset.to_string()[..16]))?,proposer_id:id("pocket4-laya-supervised-trainer")?,evaluator_id:id("native-norm-check-not-efficacy")?,selected_artifact_digest:selected,
 window:ProposalWindowV2{window_id:id(&format!("dataset-{}",&dataset.to_string()[..16]))?,window_digest:dataset},baseline_generation:generation,candidate_generation:generation.next()?,dataset_digest:dataset,
 update_rule_digest:hex(&v["update_rule_digest"])?,modulator_digest:hex(&v["modulator_digest"])?,modulator_broadcast_digest:Digest32::of_bytes(b"supervised-scorer-and-act-escalate-autograd-v1"),eligibility_digest:hex(&v["eligibility_digest"])?,evaluation_digest:hex(&v["evaluation_digest"])?,rollback_predecessor_digest:selected,norm_layers,candidates};
 let proposal=propose_v2(request)?;verify_parameter_proposal_v2(&proposal)?;
 if proposal.authority.grants_any(){return fail("UNEXPECTED_AUTHORITY");}
 Ok(json!({"native_function":"codex_hepta_plasticity::propose_v2","base_id":base.to_string(),"proposal_digest":proposal.proposal_digest.to_string(),"candidate_count":proposal.candidates.len(),"status":"RequiresIndependentAcceptance","selection_performed":false,"effect_authority":false}))
}
