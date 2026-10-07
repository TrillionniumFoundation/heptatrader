//! Thin read-only adapter into canonical learning.artifacts verification.
//! It mints no trust, owner acknowledgement, CURRENT head or selection signature.
#![forbid(unsafe_code)]
use codex_hepta_learning_artifacts as a;
use codex_hepta_types::{Digest32,Generation,StableId};
use serde_json::{Value,json};
use std::{fs::File,io::{self,Read},time::{SystemTime,UNIX_EPOCH}};
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
  },_=>Err("UNKNOWN_OPERATION_NO_WRITE_ROUTE".into())
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
