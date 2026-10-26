//! Adapter into the existing canonical learning.artifacts owner.
//! It mints no trust, CURRENT signature, operator acceptance or selection.
#![forbid(unsafe_code)]
use codex_hepta_learning_artifacts as a;
use codex_hepta_types::{Digest32,Generation,StableId};
use serde_json::{Value,json};
use std::{fs::File,io::{self,Read},path::{Path,PathBuf},time::{SystemTime,UNIX_EPOCH}};
type R<T> = Result<T,String>;
const PIN:&str="78fdb0cf8537e3a84fc6e0a849707559c80881e8";
fn text<'a>(v:&'a Value,k:&str)->R<&'a str>{v.get(k).and_then(Value::as_str).filter(|x|!x.is_empty()&&x.len()<=8192).ok_or_else(||format!("TEXT:{k}"))}
fn number(v:&Value,k:&str)->R<u64>{v.get(k).and_then(Value::as_u64).ok_or_else(||format!("U64:{k}"))}
fn dig(v:&Value,k:&str)->R<Digest32>{text(v,k)?.parse().map_err(|_|format!("DIGEST:{k}"))}
fn id(v:&Value,k:&str)->R<StableId>{StableId::new(text(v,k)?.to_owned()).map_err(|_|format!("ID:{k}"))}
fn generation(v:&Value,k:&str)->R<Generation>{Generation::new(number(v,k)?).map_err(|_|format!("GENERATION:{k}"))}
fn list<'a>(v:&'a Value,k:&str)->R<&'a Vec<Value>>{v.get(k).and_then(Value::as_array).filter(|x|x.len()<=1024).ok_or_else(||format!("LIST:{k}"))}
fn bytes<const N:usize>(s:&str)->R<[u8;N]>{
 if !s.is_ascii()||s.len()!=N*2{return Err("HEX_LENGTH".into())};let mut out=[0;N];
 for (i,x) in out.iter_mut().enumerate(){*x=u8::from_str_radix(&s[i*2..i*2+2],16).map_err(|_|"HEX".to_string())?};Ok(out)
}
fn optional_id(v:&Value,k:&str)->R<Option<StableId>>{if v.get(k).is_none_or(Value::is_null){Ok(None)}else{Ok(Some(id(v,k)?))}}
fn revoked(v:&Value)->R<Option<u64>>{if v.get("revoked_at").is_none_or(Value::is_null){Ok(None)}else{Ok(Some(number(v,"revoked_at")?))}}
fn kind(v:&Value)->R<a::ArtifactKind>{match text(v,"artifact_kind")?{"Model"=>Ok(a::ArtifactKind::Model),_=>Err("ONLY_FINANCIAL_MODEL_ARTIFACT".into())}}
fn signer(v:&Value)->R<a::TrustedArtifactSignerV1>{Ok(a::TrustedArtifactSignerV1{signer_id:id(v,"signer_id")?,verifying_key:bytes(text(v,"verifying_key")?)?,minimum_authority_epoch:number(v,"minimum_authority_epoch")?,maximum_authority_epoch:number(v,"maximum_authority_epoch")?,valid_from:number(v,"valid_from")?,expires_at:number(v,"expires_at")?,revoked_at:revoked(v)?})}
fn owner(v:&Value)->R<a::ArtifactOwnerTrustV1>{Ok(a::ArtifactOwnerTrustV1{registry_id:id(v,"registry_id")?,withdrawal_scope_digest:dig(v,"withdrawal_scope_digest")?,minimum_registry_generation:generation(v,"minimum_registry_generation")?,genesis_predecessor_head_digest:dig(v,"genesis_predecessor_head_digest")?,minimum_authority_epoch:number(v,"minimum_authority_epoch")?,writer_signers:list(v,"writer_signers")?.iter().map(signer).collect::<R<_>>()?,head_signers:list(v,"head_signers")?.iter().map(signer).collect::<R<_>>()?})}
fn selection_trust(v:&Value)->R<a::ArtifactSelectionTrustV1>{
 let mut selectors=Vec::new();for x in list(v,"selectors")?{selectors.push(a::TrustedArtifactSelectorV1{selector_id:id(x,"selector_id")?,verifying_key:bytes(text(x,"verifying_key")?)?,minimum_authority_epoch:number(x,"minimum_authority_epoch")?,maximum_authority_epoch:number(x,"maximum_authority_epoch")?,valid_from:number(x,"valid_from")?,expires_at:number(x,"expires_at")?,revoked_at:revoked(x)?});}
 Ok(a::ArtifactSelectionTrustV1{registry_id:id(v,"registry_id")?,withdrawal_scope_digest:dig(v,"withdrawal_scope_digest")?,minimum_authority_epoch:number(v,"minimum_authority_epoch")?,selectors})
}
fn signed_head(v:&Value)->R<a::SignedCurrentArtifactHeadV1>{let w=&v["witness"];Ok(a::SignedCurrentArtifactHeadV1{withdrawal_scope_digest:dig(v,"withdrawal_scope_digest")?,binding:dig(v,"binding")?,signature:bytes(text(v,"signature")?)?,witness:a::RegistryHeadWitnessV1{registry_id:id(w,"registry_id")?,generation:generation(w,"generation")?,head_digest:dig(w,"head_digest")?,predecessor_head_digest:dig(w,"predecessor_head_digest")?,authority_epoch:number(w,"authority_epoch")?,signer_id:id(w,"signer_id")?,signing_key_digest:dig(w,"signing_key_digest")?,issued_at:number(w,"issued_at")?,expires_at:number(w,"expires_at")?}})}
fn selected(v:&Value)->R<a::SignedArtifactSelectionV1>{Ok(a::SignedArtifactSelectionV1{
 selection_id:id(v,"selection_id")?,artifact_id:id(v,"artifact_id")?,registry_id:id(v,"registry_id")?,withdrawal_scope_digest:dig(v,"withdrawal_scope_digest")?,registry_head_digest:dig(v,"registry_head_digest")?,current_witness_digest:dig(v,"current_witness_digest")?,current_trust_digest:dig(v,"current_trust_digest")?,artifact_kind:kind(v)?,artifact_generation:generation(v,"artifact_generation")?,predecessor_id:optional_id(v,"predecessor_id")?,content_digest:dig(v,"content_digest")?,objective_digest:dig(v,"objective_digest")?,support_digest:dig(v,"support_digest")?,compatibility_digest:dig(v,"compatibility_digest")?,encoded_size_bytes:number(v,"encoded_size_bytes")?,selector_id:id(v,"selector_id")?,selector_credential_digest:dig(v,"selector_credential_digest")?,signing_key_digest:dig(v,"signing_key_digest")?,authority_epoch:number(v,"authority_epoch")?,issued_at:number(v,"issued_at")?,expires_at:number(v,"expires_at")?,signature:bytes(text(v,"signature")?)?})}
fn receipt(v:&Value)->R<a::RegistrySnapshotReceipt>{Ok(a::RegistrySnapshotReceipt{binding:dig(v,"binding")?,head_digest:dig(v,"head_digest")?,file_digest:dig(v,"file_digest")?,records:number(v,"records")?.try_into().map_err(|_|"RECORDS")?,encoded_bytes:number(v,"encoded_bytes")?.try_into().map_err(|_|"SIZE")?})}
fn manifest(v:&Value)->R<a::LearningArtifactManifestV2>{
 let ds=list(v,"source_dataset_digests")?.iter().map(|x|x.as_str().ok_or("DATASET").and_then(|s|s.parse().map_err(|_|"DATASET"))).collect::<Result<Vec<Digest32>,_>>().map_err(str::to_string)?;
 let lineage=list(v,"lineage_digests")?.iter().map(|x|x.as_str().ok_or("LINEAGE").and_then(|s|s.parse().map_err(|_|"LINEAGE"))).collect::<Result<Vec<Digest32>,_>>().map_err(str::to_string)?;
 Ok(a::LearningArtifactManifestV2{artifact_id:id(v,"artifact_id")?,kind:a::ArtifactKind::Model,generation:generation(v,"generation")?,provenance_mode:a::ProvenanceModeV1::DatasetDerived,source_dataset_digests:ds,lineage_digests:lineage,predecessor_ids:vec![],rollback_predecessor:None,bytes_digest:dig(v,"bytes_digest")?,encoded_size_bytes:number(v,"encoded_size_bytes")?,training_code_digest:dig(v,"training_code_digest")?,runtime_tuple_digest:dig(v,"runtime_tuple_digest")?,device_profile_digest:dig(v,"device_profile_digest")?,objective_class_digest:dig(v,"objective_class_digest")?,compatibility_digest:dig(v,"compatibility_digest")?,schema_profile_digest:dig(v,"schema_profile_digest")?,normalization_digest:dig(v,"normalization_digest")?,producer_id:id(v,"producer_id")?,created_at:number(v,"created_at")?,expires_at:number(v,"expires_at")?})
}
fn open(path:&str,max:u64)->R<File>{let f=File::open(path).map_err(|e|e.to_string())?;if !f.metadata().map_err(|e|e.to_string())?.is_file()||f.metadata().map_err(|e|e.to_string())?.len()>max{return Err("FILE_BOUND".into())}Ok(f)}

// Laya uses the same Model owner, registry and signed selection as financial
// value. These operations cannot initialize an owner or manufacture credentials.
const LAYA_PAYLOAD:&str="heptatrader.laya-owner-payload.v1";
const LAYA_LIMIT:u64=8*1024*1024;
fn hex(raw:&[u8])->String{raw.iter().map(|b|format!("{b:02x}")).collect()}
fn existing(path:&str)->R<PathBuf>{
 let p=Path::new(path);
 if !p.is_absolute()||p.canonicalize().map_err(|e|e.to_string())?!=p{return Err("EXISTING_CANONICAL_OWNER_PATH".into())}
 Ok(p.to_path_buf())
}
fn lease(v:&Value)->R<a::SignedArtifactWriterLeaseV1>{Ok(a::SignedArtifactWriterLeaseV1{
 lease_id:id(v,"lease_id")?,producer_id:id(v,"producer_id")?,registry_id:id(v,"registry_id")?,
 withdrawal_scope_digest:dig(v,"withdrawal_scope_digest")?,signer_id:id(v,"signer_id")?,
 signing_key_digest:dig(v,"signing_key_digest")?,authority_epoch:number(v,"authority_epoch")?,
 lease_generation:number(v,"lease_generation")?,issued_at:number(v,"issued_at")?,
 expires_at:number(v,"expires_at")?,signature:bytes(text(v,"signature")?)?})}
fn withdrawal_receipt(v:&Value)->R<a::DatasetWithdrawalSnapshotReceiptV1>{Ok(a::DatasetWithdrawalSnapshotReceiptV1{
 binding:dig(v,"binding")?,scope_digest:dig(v,"scope_digest")?,head_digest:dig(v,"head_digest")?,
 file_digest:dig(v,"file_digest")?,records:number(v,"records")?.try_into().map_err(|_|"RECORDS")?,
 encoded_bytes:number(v,"encoded_bytes")?.try_into().map_err(|_|"SIZE")?})}
fn existing_owner(v:&Value,now:u64)->R<a::LearningArtifactOwnerService>{
 let root=existing(text(v,"root")?)?;
 // open() may create directories; this adapter deliberately requires a fully
 // existing owner store and an independently retained signed CURRENT floor.
 for name in ["writer","transactions","payloads","registries","witnesses","heads"]{
  let p=root.join(name);if existing(p.to_str().ok_or("OWNER_PATH_UTF8")?)?!=p||!p.is_dir(){return Err("OWNER_NOT_ENROLLED".into())}
 }
 if !root.join("writer/owner.lock").is_file(){return Err("OWNER_NOT_ENROLLED".into())}
 let withdrawals=a::read_dataset_withdrawal_snapshot(
  open(text(v,"withdrawal_snapshot_path")?,64*1024*1024)?,withdrawal_receipt(&v["withdrawal_receipt"])?
 ).map_err(|e|format!("{e:?}"))?;
 a::LearningArtifactOwnerService::open(a::LearningArtifactOwnerServiceConfigV1{
  root,trust:owner(&v["owner_trust"])?,writer_lease:lease(&v["writer_lease"])?,
  required_current_head:Some(signed_head(&v["required_current_head"])?),withdrawal_registry:withdrawals,
  storage_binding:dig(v,"storage_binding")?,now}).map_err(|e|format!("{e:?}"))
}
fn laya_payload(raw:&[u8])->R<Value>{
 if raw.len() as u64>LAYA_LIMIT{return Err("LAYA_PAYLOAD_BOUND".into())}
 let p:Value=serde_json::from_slice(raw).map_err(|e|e.to_string())?;
 if text(&p,"schema")?!=LAYA_PAYLOAD||text(&p["contract"],"output")?!="CALIBRATED_PRICE7_AND_ACT_ESCALATE_ADVISORY_ONLY"
  ||p["contract"]["trade_permission"]!=false||text(&p["candidate"]["objective"],"schema")?!="heptatrader.laya-resident-objective.v8"{
  return Err("LAYA_TYPED_PAYLOAD_REQUIRED".into())
 }
 dig(&p["candidate"],"candidate_id")?;dig(&p["candidate"],"base_id")?;dig(&p["candidate"],"adapter_sha256")?;
 if !p["adapter_hex"].is_string()||p["adapter_hex"].as_str().is_some_and(|s|s.len()>LAYA_LIMIT as usize){return Err("LAYA_ADAPTER_ENCODING".into())}
 Ok(p)
}
fn actual_payload(v:&Value)->R<(a::LearningArtifactManifestV2,Vec<u8>)>{
 let m=manifest(&v["manifest"])?;let mut raw=Vec::new();open(text(v,"payload_path")?,LAYA_LIMIT)?.read_to_end(&mut raw).map_err(|e|e.to_string())?;
 if m.bytes_digest!=Digest32::of_bytes(&raw)||m.encoded_size_bytes!=raw.len() as u64{return Err("ACTUAL_PAYLOAD_IDENTITY".into())}
 laya_payload(&raw)?;Ok((m,raw))
}
fn selected_laya(v:&Value,now:u64)->R<(a::RevalidatingCandidate,a::SignedArtifactSelectionV1,Digest32)>{
 let trust=owner(&v["owner"]["owner_trust"])?;
 let verifier=a::ArtifactOwnerVerifierV1::new(trust.clone()).map_err(|e|format!("{e:?}"))?;
 let b=&v["selection_bundle"];let signed=selected(&b["signed_selection"])?;
 let requirement=&v["selection_head_requirement"];
 let req=a::RegistryHeadRequirementV1{registry_id:id(requirement,"registry_id")?,minimum_generation:generation(requirement,"minimum_generation")?,
  expected_predecessor_head_digest:dig(requirement,"expected_predecessor_head_digest")?,minimum_authority_epoch:number(requirement,"minimum_authority_epoch")?,now};
 let snap=receipt(&b["registry_receipt"])?;
 let current=verifier.verify_current_registry_view(open(text(b,"registry_snapshot_path")?,64*1024*1024)?,snap,&signed_head(&b["signed_current_head"])?,&req).map_err(|e|format!("{e:?}"))?;
 let sv=a::ArtifactSelectionVerifierV1::new(selection_trust(&v["selection_trust"])?,&trust).map_err(|e|format!("{e:?}"))?;
 let verified=sv.verify(&signed,&current,now).map_err(|e|format!("{e:?}"))?;
 let m=manifest(&v["manifest"])?;
 let validated=a::validate_artifact_manifest_v2(m.clone(),now).map_err(|e|format!("{e:?}"))?;
 if signed.artifact_id!=m.artifact_id||signed.artifact_generation!=m.generation||signed.content_digest!=m.bytes_digest
  ||signed.objective_digest!=m.objective_class_digest||signed.compatibility_digest!=m.compatibility_digest
  ||signed.support_digest!=validated.manifest_digest||signed.encoded_size_bytes!=m.encoded_size_bytes||verified.authority().grants_any(){
  return Err("LAYA_SELECTED_MANIFEST_SCOPE".into())
 }
 let selection_digest=verified.selection_digest();
 let loaded=a::load_selected_candidate(open(text(b,"registry_snapshot_path")?,64*1024*1024)?,open(text(b,"payload_path")?,LAYA_LIMIT)?,verified).map_err(|e|format!("{e:?}"))?;
 Ok((loaded,signed,selection_digest))
}
fn lifecycle(v:&Value,signed:&a::SignedArtifactSelectionV1,selection_digest:Digest32)->R<(usize,Digest32)>{
 // This snapshot/receipt is from the pinned owner enrollment, never the model
 // payload. Replay checks roles; signatures remain the host's responsibility.
 let x=&v["lifecycle"];let r=&x["receipt"];
 let journal=a::read_artifact_lifecycle_snapshot(open(text(x,"path")?,64*1024*1024)?,a::ArtifactLifecycleSnapshotReceiptV2{
  binding:dig(r,"binding")?,head_digest:dig(r,"head_digest")?,file_digest:dig(r,"file_digest")?,
  records:number(r,"records")?.try_into().map_err(|_|"RECORDS")?,encoded_bytes:number(r,"encoded_bytes")?.try_into().map_err(|_|"SIZE")?
 }).map_err(|e|format!("{e:?}"))?;
 let last=journal.records().iter().rev().find(|r|r.event.artifact_id==signed.artifact_id).ok_or("LAYA_OPERATOR_SELECTION_REQUIRED")?;
 if last.event.next_state!=a::ArtifactLifecycleStateV1::Selected||last.event.evidence_digest!=selection_digest
  ||last.event.actor_id!=signed.selector_id||last.event.actor_credential_digest!=signed.selector_credential_digest
  ||last.actor.role!=a::LifecycleActorRoleV2::Selector{return Err("LAYA_OPERATOR_SELECTION_REQUIRED".into())}
 if let Some(prior)=v.get("loaded_lifecycle"){
  let n=number(prior,"records")? as usize;
  if n==0||journal.records().get(n-1).map(|r|r.chain_digest)!=Some(dig(prior,"head_digest")?){return Err("LAYA_LIFECYCLE_ROLLBACK".into())}
 }
 Ok((journal.records().len(),journal.head_digest()))
}
fn laya_dispatch(v:&Value,now:u64)->R<Value>{
 let op=text(v,"operation")?;
 if op=="publish_laya_model"{
  let (m,raw)=actual_payload(v)?;let mut service=existing_owner(&v["owner"],now)?;
  let admission=a::admit_manifest_at_withdrawal_head_v3(service.withdrawal_registry(),dig(v,"expected_withdrawal_head")?,m,now).map_err(|e|format!("{e:?}"))?;
  let receipt=service.publish(a::LearningArtifactPublishRequestV1{operation_id:id(v,"operation_id")?,admission,payload:raw,
   signed_current_head:signed_head(&v["publication_head"])?,expected_registry_predecessor_head:dig(v,"expected_registry_predecessor_head")?,now}).map_err(|e|format!("{e:?}"))?;
  if receipt.authority.grants_any(){return Err("UNEXPECTED_AUTHORITY".into())}
  return Ok(json!({"status":"CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED","operation_id":receipt.operation_id.as_str(),
   "admission_digest":receipt.admission_digest.to_string(),"registry_head_digest":receipt.registry_head_digest.to_string(),
   "witness_digest":receipt.witness_digest.to_string(),"publication_state_digest":receipt.state_digest.to_string(),"acknowledged_at":receipt.acknowledged_at,
   "native_source_sha":PIN,"selection_verified":false,"canonical_agentd_admitted":false,"product_consumed":false,"effect_authority":false}));
 }
 let (mut loaded,signed,selection_digest)=selected_laya(v,now)?;
 let (lifecycle_records,lifecycle_head)=lifecycle(v,&signed,selection_digest)?;
 let service=existing_owner(&v["owner"],now)?;
 let current=service.current_registry_view(now).map_err(|e|format!("{e:?}"))?;
 let current_receipt=current.receipt();
 if let Some(prior)=v.get("loaded_registry"){
  let n=number(prior,"records")? as usize;
  if n==0||service.registry().records().get(n-1).map(|r|r.chain_digest)!=Some(dig(prior,"head_digest")?){return Err("LAYA_CURRENT_ROLLBACK".into())}
 }
 let raw=loaded.with_current(current,|raw|raw.to_vec()).map_err(|e|format!("{e:?}"))?;
 laya_payload(&raw)?;
 let mut result=json!({"status":if op=="load_laya_selected"{"CANONICAL_LAYA_SELECTED_BYTES_LOADED"}else{"CANONICAL_LAYA_OUTPUT_REVALIDATED"},
  "artifact_id":signed.artifact_id.as_str(),"artifact_generation":signed.artifact_generation.get(),"content_digest":signed.content_digest.to_string(),
  "selection_digest":selection_digest.to_string(),"support_digest":signed.support_digest.to_string(),"compatibility_digest":signed.compatibility_digest.to_string(),
  "registry_head_digest":current_receipt.head_digest.to_string(),"loaded_registry":{"records":current_receipt.records,"head_digest":current_receipt.head_digest.to_string()},
  "loaded_lifecycle":{"records":lifecycle_records,"head_digest":lifecycle_head.to_string()},
  "native_source_sha":PIN,"selection_verified":true,"canonical_agentd_admitted":false,"product_consumed":false,"effect_authority":false});
 if op=="load_laya_selected"{result["payload_hex"]=Value::String(hex(&raw));}
 else{
  // An actual product callback supplies these after inference. This native
  // receipt attests final currentness/binding, never pretends to run the model.
  result["request_digest"]=Value::String(dig(v,"request_digest")?.to_string());
  result["output_digest"]=Value::String(dig(v,"output_digest")?.to_string());
 }
 Ok(result)
}
fn dispatch(v:&Value)->R<Value>{
 let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e|e.to_string())?.as_millis() as u64;
 match text(v,"operation")?{
  "validate_candidate_manifest"=>{
   let m=manifest(&v["manifest"])?;let mut payload=Vec::new();open(text(v,"payload_path")?,64*1024*1024)?.read_to_end(&mut payload).map_err(|e|e.to_string())?;
   if m.bytes_digest!=Digest32::of_bytes(&payload)||m.encoded_size_bytes!=payload.len() as u64{return Err("ACTUAL_PAYLOAD_IDENTITY".into())}
   let validated=a::validate_artifact_manifest_v2(m,now).map_err(|e|format!("{e:?}"))?;
   if validated.authority.grants_any(){return Err("UNEXPECTED_AUTHORITY".into())}
   Ok(json!({"status":"CANONICAL_MANIFEST_VALID_NOT_OWNER_ADMITTED","manifest_digest":validated.manifest_digest.to_string(),"native_source_sha":PIN,"canonical_agentd_admitted":false,"selection_verified":false,"effect_authority":false}))
  },
  "verify_current_selection"=>{
   let trust=owner(&v["owner_trust"])?;let st=selection_trust(&v["selection_trust"])?;
   let verifier=a::ArtifactOwnerVerifierV1::new(trust.clone()).map_err(|e|format!("{e:?}"))?;
   let signed=selected(&v["signed_selection"])?;let head=signed_head(&v["signed_current_head"])?;let req=&v["head_requirement"];
   // Anti-rollback requirements originate in independently retained host state,
   // never copied from the submitted signed head by this adapter.
   let requirement=a::RegistryHeadRequirementV1{registry_id:id(req,"registry_id")?,minimum_generation:generation(req,"minimum_generation")?,expected_predecessor_head_digest:dig(req,"expected_predecessor_head_digest")?,minimum_authority_epoch:number(req,"minimum_authority_epoch")?,now};
   let snap=receipt(&v["registry_receipt"])?;let snapshot=text(v,"registry_snapshot_path")?;
   let current=verifier.verify_current_registry_view(open(snapshot,64*1024*1024)?,snap,&head,&requirement).map_err(|e|format!("{e:?}"))?;
   let sv=a::ArtifactSelectionVerifierV1::new(st,&trust).map_err(|e|format!("{e:?}"))?;
   let verified=sv.verify(&signed,&current,now).map_err(|e|format!("{e:?}"))?;
   if signed.content_digest!=dig(v,"expected_content_digest")?||signed.support_digest!=dig(v,"expected_support_digest")?||signed.compatibility_digest!=dig(v,"expected_compatibility_digest")?||verified.authority().grants_any(){return Err("FINANCIAL_SELECTION_SCOPE".into())}
   let result=verified.selection_digest();let mut loaded=a::load_selected_candidate(open(snapshot,64*1024*1024)?,open(text(v,"payload_path")?,64*1024*1024)?,verified).map_err(|e|format!("{e:?}"))?;
   let actual=loaded.with_current(current,Digest32::of_bytes).map_err(|e|format!("{e:?}"))?;
   if actual!=signed.content_digest{return Err("LOADED_BYTES_CHANGED".into())}
   Ok(json!({"status":"CANONICAL_SIGNED_SELECTION_VERIFIED_READ_ONLY","selection_digest":result.to_string(),"content_digest":actual.to_string(),"registry_head_digest":snap.head_digest.to_string(),"native_source_sha":PIN,"selection_verified":true,"canonical_agentd_admitted":false,"effect_authority":false}))
   },
   "publish_laya_model"|"load_laya_selected"|"revalidate_laya_output"=>laya_dispatch(v,now),
   _=>Err("UNKNOWN_OPERATION_NO_WRITE_ROUTE".into())
 }
}
fn main(){let mut b=Vec::new();if io::stdin().take(2*1024*1024+1).read_to_end(&mut b).is_err()||b.len()>2*1024*1024{println!("{{\"error\":\"INPUT_BOUND\"}}");std::process::exit(2)}
 let result=serde_json::from_slice::<Value>(&b).map_err(|e|e.to_string()).and_then(|v|dispatch(&v));
 match result{Ok(v)=>println!("{v}"),Err(e)=>{println!("{}",json!({"status":"REJECTED","error":e,"native_source_sha":PIN,"effect_authority":false}));std::process::exit(2)}}}
#[cfg(test)]mod tests{use super::*;
 #[test]fn rejects_unknown_route(){assert!(dispatch(&json!({"operation":"mint_owner_ack"})).is_err());}
 #[test]fn requires_real_signed_head(){assert!(dispatch(&json!({"operation":"verify_current_selection","owner_accepted":true})).is_err());}
 #[test]fn strict_numbers(){assert!(number(&json!({"n":true}),"n").is_err());assert!(generation(&json!({"n":0}),"n").is_err());}
 #[test]fn invalid_keys(){assert!(bytes::<32>("abcd").is_err());assert!(bytes::<32>(&"zz".repeat(32)).is_err());}
 #[test]fn only_model_kind(){assert!(kind(&json!({"artifact_kind":"Policy"})).is_err());}
}

#[cfg(test)]
#[path = "financial_owner_laya_tests.rs"]
mod laya_owner_tests;
