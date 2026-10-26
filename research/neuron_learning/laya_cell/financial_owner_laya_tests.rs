//! Synthetic, isolated native contracts. Keys below are public test fixtures,
//! never production enrollment or evidence of a qualified trading model.
use super::*;
use ed25519_dalek::{Signer,SigningKey};
use tempfile::TempDir;

fn sid(s:&str)->StableId{StableId::new(s.to_owned()).unwrap()}
fn d(s:&str)->Digest32{Digest32::of_bytes(s.as_bytes())}
fn signer_json(key:&SigningKey)->Value{json!({"signer_id":"fixture-owner","verifying_key":hex(&key.verifying_key().to_bytes()),
 "minimum_authority_epoch":1,"maximum_authority_epoch":9,"valid_from":1,"expires_at":1000000,"revoked_at":null})}
fn lease_json(key:&SigningKey,scope:Digest32)->Value{
 let mut l=a::SignedArtifactWriterLeaseV1{lease_id:sid("fixture-lease"),producer_id:sid("fixture-producer"),
  registry_id:sid("fixture-artifacts"),withdrawal_scope_digest:scope,signer_id:sid("fixture-owner"),
  signing_key_digest:Digest32::of_bytes(&key.verifying_key().to_bytes()),authority_epoch:1,
  lease_generation:1,issued_at:10,expires_at:1000000,signature:[0;64]};
 l.signature=key.sign(&l.signing_bytes()).to_bytes();
 json!({"lease_id":l.lease_id.as_str(),"producer_id":l.producer_id.as_str(),"registry_id":l.registry_id.as_str(),
  "withdrawal_scope_digest":scope.to_string(),"signer_id":l.signer_id.as_str(),"signing_key_digest":l.signing_key_digest.to_string(),
  "authority_epoch":1,"lease_generation":1,"issued_at":10,"expires_at":1000000,"signature":hex(&l.signature)})
}
fn head_json(h:&a::SignedCurrentArtifactHeadV1)->Value{json!({"withdrawal_scope_digest":h.withdrawal_scope_digest.to_string(),
 "binding":h.binding.to_string(),"signature":hex(&h.signature),"witness":{"registry_id":h.witness.registry_id.as_str(),
 "generation":h.witness.generation.get(),"head_digest":h.witness.head_digest.to_string(),
 "predecessor_head_digest":h.witness.predecessor_head_digest.to_string(),"authority_epoch":h.witness.authority_epoch,
 "signer_id":h.witness.signer_id.as_str(),"signing_key_digest":h.witness.signing_key_digest.to_string(),
 "issued_at":h.witness.issued_at,"expires_at":h.witness.expires_at}})}
fn snapshot_json(r:a::RegistrySnapshotReceipt)->Value{json!({"binding":r.binding.to_string(),"head_digest":r.head_digest.to_string(),
 "file_digest":r.file_digest.to_string(),"records":r.records,"encoded_bytes":r.encoded_bytes})}
fn manifest_json(name:&str,raw:&[u8])->Value{json!({"artifact_id":name,"generation":1,
 "source_dataset_digests":[d("fixture-dataset").to_string()],"lineage_digests":[d("fixture-lineage").to_string()],
 "bytes_digest":Digest32::of_bytes(raw).to_string(),"encoded_size_bytes":raw.len(),
 "training_code_digest":d("code").to_string(),"runtime_tuple_digest":d("runtime").to_string(),
 "device_profile_digest":d("device").to_string(),"objective_class_digest":d("objective").to_string(),
 "compatibility_digest":d("compatibility").to_string(),"schema_profile_digest":d("schema").to_string(),
 "normalization_digest":d("normalization").to_string(),"producer_id":"fixture-producer","created_at":10,"expires_at":1000000})}

struct Fixture{
 _temp:TempDir, root:PathBuf, key:SigningKey, withdrawals:a::DatasetWithdrawalRegistry,
 owner:Value, head:a::SignedCurrentArtifactHeadV1,
}
impl Fixture{
 fn new()->Self{
  let temp=TempDir::new().unwrap();let root=temp.path().join("existing-owner");
  let key=SigningKey::from_bytes(&[81;32]);
  let withdrawals=a::DatasetWithdrawalRegistry::new_scoped(a::DatasetWithdrawalScopeV1{
   authority_domain_id:sid("fixture-dataset-authority"),registry_id:sid("fixture-withdrawals"),scope_id:sid("fixture-scope")});
  let scope=withdrawals.scope_digest().unwrap();let signer=signer_json(&key);
  let trust=json!({"registry_id":"fixture-artifacts","withdrawal_scope_digest":scope.to_string(),
   "minimum_registry_generation":1,"genesis_predecessor_head_digest":Digest32::ZERO.to_string(),
   "minimum_authority_epoch":1,"writer_signers":[signer.clone()],"head_signers":[signer]});
  let lease=lease_json(&key,scope);
  let withdrawal_path=temp.path().join("withdrawals.snapshot");
  let wr=a::write_dataset_withdrawal_snapshot(a::CreateOnlyArtifactFile::create(&withdrawal_path).unwrap(),&withdrawals,d("withdrawal-binding")).unwrap();
  let enrollment=json!({"root":root,"owner_trust":trust,"writer_lease":lease,"storage_binding":d("binding").to_string(),
   "withdrawal_snapshot_path":withdrawal_path,"withdrawal_receipt":{"binding":wr.binding.to_string(),"scope_digest":wr.scope_digest.to_string(),
   "head_digest":wr.head_digest.to_string(),"file_digest":wr.file_digest.to_string(),"records":wr.records,"encoded_bytes":wr.encoded_bytes}});
  // Only fixture setup may create the first owner; production dispatch refuses it.
  let host=a::LearningArtifactOwnerHost::open(&root,owner(&enrollment["owner_trust"]).unwrap(),super::lease(&enrollment["writer_lease"]).unwrap(),30).unwrap();
  let raw=b"synthetic seed model";let m=manifest(&manifest_json("fixture-seed",raw)).unwrap();
  let admission=a::admit_manifest_at_withdrawal_head_v3(&withdrawals,withdrawals.head_digest(),m,30).unwrap();
  let mut registry=a::ArtifactRegistry::new();let predecessor=registry.snapshot().head_digest;
  let transaction=a::ArtifactPublicationTransactionV1::begin(sid("fixture-seed-operation"),admission.clone(),&withdrawals,&registry,predecessor,30).unwrap();
  host.stage_compatibility_registration(&transaction,&mut registry,30).unwrap();
  let head=Self::sign_head(&key,scope,1,registry.snapshot().head_digest,predecessor);drop(host);
  let mut service=a::LearningArtifactOwnerService::open(a::LearningArtifactOwnerServiceConfigV1{root:root.clone(),
   trust:owner(&enrollment["owner_trust"]).unwrap(),writer_lease:super::lease(&enrollment["writer_lease"]).unwrap(),
   required_current_head:None,withdrawal_registry:withdrawals.clone(),storage_binding:d("binding"),now:30}).unwrap();
  service.publish(a::LearningArtifactPublishRequestV1{operation_id:sid("fixture-seed-operation"),admission,payload:raw.to_vec(),
   signed_current_head:head.clone(),expected_registry_predecessor_head:predecessor,now:30}).unwrap();drop(service);
  let mut enrollment=enrollment;enrollment["required_current_head"]=head_json(&head);
  Self{_temp:temp,root,key,withdrawals,owner:enrollment,head}
 }
 fn sign_head(key:&SigningKey,scope:Digest32,generation:u64,head:Digest32,previous:Digest32)->a::SignedCurrentArtifactHeadV1{
  let mut signed=a::SignedCurrentArtifactHeadV1{withdrawal_scope_digest:scope,binding:d("binding"),
   witness:a::RegistryHeadWitnessV1{registry_id:sid("fixture-artifacts"),generation:Generation::new(generation).unwrap(),
    head_digest:head,predecessor_head_digest:previous,authority_epoch:1,signer_id:sid("fixture-owner"),
    signing_key_digest:Digest32::of_bytes(&key.verifying_key().to_bytes()),issued_at:30,expires_at:1000000},signature:[0;64]};
  signed.signature=key.sign(&signed.signing_bytes()).to_bytes();signed
 }
 fn publication(&self,name:&str,raw:&[u8])->(Value,a::SignedCurrentArtifactHeadV1){
  let host=a::LearningArtifactOwnerHost::open_with_required_current_head(&self.root,owner(&self.owner["owner_trust"]).unwrap(),
   super::lease(&self.owner["writer_lease"]).unwrap(),self.head.clone(),30).unwrap();
  let mut registry=host.recover_current_registry(30).unwrap();let predecessor=registry.snapshot().head_digest;
  let m=manifest_json(name,raw);let admission=a::admit_manifest_at_withdrawal_head_v3(&self.withdrawals,self.withdrawals.head_digest(),manifest(&m).unwrap(),30).unwrap();
  let operation=format!("fixture-operation-{name}");
  let transaction=a::ArtifactPublicationTransactionV1::begin(sid(&operation),admission,&self.withdrawals,&registry,predecessor,30).unwrap();
  host.stage_compatibility_registration(&transaction,&mut registry,30).unwrap();
  let head=Self::sign_head(&self.key,self.withdrawals.scope_digest().unwrap(),self.head.witness.generation.get()+1,registry.snapshot().head_digest,predecessor);
  let payload=self._temp.path().join(format!("{name}.json"));std::fs::write(&payload,raw).unwrap();
  (json!({"operation":"publish_laya_model","owner":self.owner,"manifest":m,"payload_path":payload,
   "operation_id":operation,"publication_head":head_json(&head),"expected_registry_predecessor_head":predecessor.to_string(),
   "expected_withdrawal_head":self.withdrawals.head_digest().to_string()}),head)
 }
 fn selected_request(&self,published:&Value)->Value{
  let service=existing_owner(&self.owner,30).unwrap();let current=service.current_registry_view(30).unwrap();
  let m=manifest(&published["manifest"]).unwrap();let pin=service.registry().manifest(&m.artifact_id).unwrap();
  let selector_key=SigningKey::from_bytes(&[92;32]);
  let selection_trust_value=json!({"registry_id":"fixture-artifacts","withdrawal_scope_digest":self.withdrawals.scope_digest().unwrap().to_string(),
   "minimum_authority_epoch":1,"selectors":[{"selector_id":"fixture-independent-selector","verifying_key":hex(&selector_key.verifying_key().to_bytes()),
    "minimum_authority_epoch":1,"maximum_authority_epoch":9,"valid_from":1,"expires_at":1000000,"revoked_at":null}]});
  let mut signed=a::SignedArtifactSelectionV1{selection_id:sid("fixture-selection"),artifact_id:m.artifact_id.clone(),
   registry_id:sid("fixture-artifacts"),withdrawal_scope_digest:self.withdrawals.scope_digest().unwrap(),
   registry_head_digest:current.receipt().head_digest,current_witness_digest:current.witness_digest(),current_trust_digest:current.trust_digest(),
   artifact_kind:a::ArtifactKind::Model,artifact_generation:m.generation,predecessor_id:None,content_digest:m.bytes_digest,
   objective_digest:m.objective_class_digest,support_digest:pin.support_digest,compatibility_digest:m.compatibility_digest,
   encoded_size_bytes:m.encoded_size_bytes,selector_id:sid("fixture-independent-selector"),selector_credential_digest:d("fixture-selector-credential"),
   signing_key_digest:Digest32::of_bytes(&selector_key.verifying_key().to_bytes()),authority_epoch:1,issued_at:30,expires_at:1000000,signature:[0;64]};
  signed.signature=selector_key.sign(&signed.signing_bytes()).to_bytes();
  let verifier=a::ArtifactSelectionVerifierV1::new(selection_trust(&selection_trust_value).unwrap(),&owner(&self.owner["owner_trust"]).unwrap()).unwrap();
  let verified=verifier.verify(&signed,&current,30).unwrap();
  let mut journal=a::ArtifactLifecycleJournalV2::new();
  let transitions=[(a::LifecycleActorRoleV2::Producer,"fixture-producer",a::ArtifactLifecycleStateV1::Proposed,a::ArtifactLifecycleStateV1::Trained),
   (a::LifecycleActorRoleV2::Evaluator,"fixture-evaluator",a::ArtifactLifecycleStateV1::Trained,a::ArtifactLifecycleStateV1::Evaluated),
   (a::LifecycleActorRoleV2::ShadowOperator,"fixture-shadow",a::ArtifactLifecycleStateV1::Evaluated,a::ArtifactLifecycleStateV1::Shadow),
   (a::LifecycleActorRoleV2::CanaryOperator,"fixture-canary",a::ArtifactLifecycleStateV1::Shadow,a::ArtifactLifecycleStateV1::Canary),
   (a::LifecycleActorRoleV2::HumanOperator,"fixture-human",a::ArtifactLifecycleStateV1::Canary,a::ArtifactLifecycleStateV1::OperatorAccepted)];
  for (i,(role,actor,prior,next)) in transitions.into_iter().enumerate(){
   journal.append(journal.head_digest(),&m.producer_id,a::LifecycleActorEvidenceV2{actor_id:sid(actor),credential_digest:d(actor),role,
    authority_epoch:1,verified_at:20,expires_at:1000000},a::ArtifactLifecycleEventV1{event_id:sid(&format!("fixture-lifecycle-{i}")),
    artifact_id:m.artifact_id.clone(),prior_state:prior,next_state:next,actor_id:sid(actor),actor_credential_digest:d(actor),
    evidence_digest:d(&format!("fixture-evidence-{i}")),authority_epoch:1,occurred_at:30},30).unwrap();
  }
  let prior=journal.head_digest();
  a::record_verified_selection(&mut journal,prior,&m.producer_id,&verified,sid("fixture-selected"),30).unwrap();
  let lifecycle_path=self._temp.path().join("lifecycle.snapshot");
  let lr=a::write_artifact_lifecycle_snapshot(a::CreateOnlyArtifactFile::create(&lifecycle_path).unwrap(),&journal,d("fixture-lifecycle-binding")).unwrap();
  let receipt=current.receipt();let selected=json!({"selection_id":signed.selection_id.as_str(),"artifact_id":signed.artifact_id.as_str(),
   "registry_id":signed.registry_id.as_str(),"withdrawal_scope_digest":signed.withdrawal_scope_digest.to_string(),
   "registry_head_digest":signed.registry_head_digest.to_string(),"current_witness_digest":signed.current_witness_digest.to_string(),
   "current_trust_digest":signed.current_trust_digest.to_string(),"artifact_kind":"Model","artifact_generation":signed.artifact_generation.get(),
   "predecessor_id":null,"content_digest":signed.content_digest.to_string(),"objective_digest":signed.objective_digest.to_string(),
   "support_digest":signed.support_digest.to_string(),"compatibility_digest":signed.compatibility_digest.to_string(),
   "encoded_size_bytes":signed.encoded_size_bytes,"selector_id":signed.selector_id.as_str(),"selector_credential_digest":signed.selector_credential_digest.to_string(),
   "signing_key_digest":signed.signing_key_digest.to_string(),"authority_epoch":1,"issued_at":30,"expires_at":1000000,"signature":hex(&signed.signature)});
  json!({"operation":"load_laya_selected","owner":self.owner,"manifest":published["manifest"],"selection_trust":selection_trust_value,
   "selection_head_requirement":{"registry_id":"fixture-artifacts","minimum_generation":self.head.witness.generation.get(),
    "expected_predecessor_head_digest":self.head.witness.predecessor_head_digest.to_string(),"minimum_authority_epoch":1},
   "selection_bundle":{"signed_selection":selected,"signed_current_head":head_json(&self.head),"registry_receipt":snapshot_json(receipt),
    "registry_snapshot_path":self.root.join("registries").join(format!("{}-{}.snapshot",receipt.head_digest,receipt.file_digest)),
    "payload_path":self.root.join("payloads").join(format!("{}-{}.bin",m.artifact_id,m.bytes_digest))},
   "lifecycle":{"path":lifecycle_path,"receipt":{"binding":lr.binding.to_string(),"head_digest":lr.head_digest.to_string(),
    "file_digest":lr.file_digest.to_string(),"records":lr.records,"encoded_bytes":lr.encoded_bytes}}})
 }
}
fn payload()->Vec<u8>{serde_json::to_vec(&json!({"schema":LAYA_PAYLOAD,"contract":{"output":"CALIBRATED_PRICE7_AND_ACT_ESCALATE_ADVISORY_ONLY","trade_permission":false},
 "candidate":{"candidate_id":d("candidate").to_string(),"base_id":d("base").to_string(),"adapter_sha256":d("adapter").to_string(),
 "objective":{"schema":"heptatrader.laya-resident-objective.v8"}},"adapter_hex":"0000","synthetic_native_contract_fixture":true})).unwrap()}

#[test]
fn laya_adapter_never_initializes_an_owner(){
 let temp=TempDir::new().unwrap();let missing=temp.path().join("must-not-create");
 assert!(existing_owner(&json!({"root":missing}),30).is_err());assert!(!missing.exists());
}

#[test]
fn real_native_publication_selection_and_final_use_stay_deny_all(){
 let mut f=Fixture::new();let (publish,head)=f.publication("fixture-laya",&payload());
 let receipt=laya_dispatch(&publish,30).unwrap();assert_eq!(receipt["status"],"CANONICAL_LAYA_MODEL_PUBLISHED_NOT_SELECTED");
 assert_eq!(receipt["product_consumed"],false);assert_eq!(laya_dispatch(&publish,30).unwrap(),receipt);
 f.head=head;
 let request=f.selected_request(&publish);let loaded=laya_dispatch(&request,30).unwrap();
 assert_eq!(loaded["status"],"CANONICAL_LAYA_SELECTED_BYTES_LOADED");assert_eq!(loaded["payload_hex"],hex(&payload()));
 assert_eq!(loaded["canonical_agentd_admitted"],false);assert_eq!(loaded["product_consumed"],false);
 let mut final_request=request.clone();final_request["operation"]=json!("revalidate_laya_output");
 final_request["loaded_registry"]=loaded["loaded_registry"].clone();final_request["loaded_lifecycle"]=loaded["loaded_lifecycle"].clone();
 final_request["request_digest"]=json!(d("actual-product-request").to_string());final_request["output_digest"]=json!(d("actual-product-output").to_string());
 let final_receipt=laya_dispatch(&final_request,30).unwrap();
 assert_eq!(final_receipt["status"],"CANONICAL_LAYA_OUTPUT_REVALIDATED");assert_eq!(final_receipt["product_consumed"],false);
 assert_eq!(final_receipt["output_digest"],final_request["output_digest"]);
 // An unrelated real owner publication may extend CURRENT. The selected
 // version remains fixed and the old load frontier must still be its prefix.
 let (other,next)=f.publication("fixture-other",&payload());laya_dispatch(&other,30).unwrap();f.head=next;
 let advanced=laya_dispatch(&final_request,30).unwrap();assert_ne!(advanced["registry_head_digest"],loaded["registry_head_digest"]);
 assert_eq!(advanced["content_digest"],loaded["content_digest"]);
 final_request["loaded_registry"]["head_digest"]=json!(d("fork").to_string());
 assert!(laya_dispatch(&final_request,30).unwrap_err().contains("ROLLBACK"));
}

#[test]
fn selected_bundle_cannot_replace_real_current_or_lifecycle(){
 let mut f=Fixture::new();let (publish,head)=f.publication("fixture-laya",&payload());laya_dispatch(&publish,30).unwrap();f.head=head;
 let request=f.selected_request(&publish);
 let service=existing_owner(&f.owner,30).unwrap();
 // The live owner keeps publication fenced while readonly load/final-use work.
 let loaded=laya_dispatch(&request,30).unwrap();
 assert_eq!(loaded["status"],"CANONICAL_LAYA_SELECTED_BYTES_LOADED");
 assert!(existing_owner(&f.owner,30).unwrap_err().contains("WriterFenceBusy"));
 let mut final_request=request.clone();final_request["operation"]=json!("revalidate_laya_output");
 final_request["loaded_registry"]=loaded["loaded_registry"].clone();
 final_request["loaded_lifecycle"]=loaded["loaded_lifecycle"].clone();
 final_request["request_digest"]=json!(d("actual-request").to_string());
 final_request["output_digest"]=json!(d("actual-output").to_string());
 // Reader credentials do not include a writer lease or withdrawal publication state.
 final_request["owner"].as_object_mut().unwrap().remove("writer_lease");
 final_request["owner"].as_object_mut().unwrap().remove("withdrawal_snapshot_path");
 let finalized=laya_dispatch(&final_request,30).unwrap();
 assert_eq!(finalized["status"],"CANONICAL_LAYA_OUTPUT_REVALIDATED");
 assert_eq!(finalized["product_consumed"],false);
 assert_eq!(finalized["canonical_agentd_admitted"],false);
 drop(service);
 let mut bad=request.clone();bad["selection_bundle"]["signed_selection"]["signature"]=json!("00".repeat(64));
 assert!(laya_dispatch(&bad,30).is_err());
 let mut bad=request.clone();bad["lifecycle"]["receipt"]["head_digest"]=json!(d("not-accepted").to_string());
 assert!(laya_dispatch(&bad,30).is_err());
 let mut bad=request;bad["manifest"]["generation"]=json!(2);
 assert!(laya_dispatch(&bad,30).is_err());
}
