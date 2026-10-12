//! Test-only REAL original IB PAPER quote / new shadow policy -> original
//! Canonical native LedgerWriter, with actual external Mac/X230 signatures.
//! NEVER original LLM policy, actual orders, dollar returns, production trust.
use codex_hepta_learning_ledger::*;
use codex_hepta_types::{Digest32,StableId,ProbabilityQ32,FixedQ32};
use serde_json::{json,Value};
use ed25519_dalek::{Signature,VerifyingKey,Verifier};
use std::fs::{File,OpenOptions};
use std::io::{self,Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt,PermissionsExt};
use std::path::Path;
use std::time::{SystemTime,UNIX_EPOCH};

const SCHEMA:&str="heptatrader.train-v2-real-market-original-shadow-native-test-only-packet.v1";
const POLICY:&str="SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1";
const SCOPE:&str="REAL_PAPER_SOURCE_TEST_ONLY_NO_BROKER_EXECUTION";
const OBJECTIVE:&str="PAPER_FX_5MIN_MIDPRICE_BPS_NOT_AFTER_COST_PNL_TEST_ONLY";
fn ms()->u64{SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64}
fn id(v:&str)->StableId{StableId::new(v.to_owned()).expect("lab stable ID")}
fn dg(v:&str)->Digest32{Digest32::of_bytes(v.as_bytes())}
fn fromhex(v:&str)->Vec<u8>{
    assert!(v.len()%2==0&&v.len()<=8192);
    (0..v.len()).step_by(2)
        .map(|i|u8::from_str_radix(&v[i..i+2],16).unwrap()).collect()
}
fn fixed<const N:usize>(v:Vec<u8>)->[u8;N]{v.try_into().ok().unwrap()}
fn hx(v:&[u8])->String{v.iter().map(|b|format!("{b:02x}")).collect()}
fn read(path:&str)->(Value,Vec<u8>){
    let raw=std::fs::read(path).unwrap();
    assert!(raw.len()<=128*1024);
    (serde_json::from_slice(&raw).unwrap(),raw)
}
fn sig(role:&str,payload:&[u8])->[u8;64]{
    assert!(["root","generator","observer","evaluator","privacy"].contains(&role));
    println!("SIGN_REQUEST {role} {}",hx(payload));
    io::stdout().flush().unwrap();
    let mut line=String::new();
    io::stdin().read_line(&mut line).unwrap();
    let parts:Vec<_>=line.split_whitespace().collect();
    assert_eq!(parts.len(),2,"invalid test signer reply");
    assert_eq!(parts[0],"SIG");
    fixed(fromhex(parts[1]))
}
fn pubkey(j:&Value,role:&str)->[u8;32]{
    fixed(fromhex(j["roles"][role]["public_key_hex"].as_str().unwrap()))
}
fn signer(j:&Value,role:&str,controller:&str,r:LearningEvidenceRoleV1,
           ref_ms:u64)->TrustedLearningSignerV1 {
    let k=pubkey(j,role);
    TrustedLearningSignerV1{
        principal:AuthenticatedPrincipalV1{
            principal_id:id(&format!("lab.{role}")),
            credential_chain_digest:dg(&format!("REAL_SOURCE_TEST_ONLY_{role}_CREDENTIAL")),
            signing_key_digest:Digest32::of_bytes(&k),
            scope_digest:dg(SCOPE),authority_epoch:19,
            authenticated_at:ref_ms-15000,expires_at:ref_ms+3_600_000,
        },
        controller_id:id(controller),verifying_key:k,
        roles:vec![r],revoked_at:None,
    }
}
fn original_test_keys(j:&Value,ref_ms:u64)->Vec<TrustedLearningSignerV1>{
    vec![
      signer(j,"generator","lab.mac.controller",LearningEvidenceRoleV1::Generator,ref_ms),
      signer(j,"observer","lab.x230.controller",LearningEvidenceRoleV1::Observer,ref_ms),
      signer(j,"evaluator","lab.qian.evaluator",LearningEvidenceRoleV1::Evaluator,ref_ms),
      signer(j,"privacy","lab.qian.privacy",LearningEvidenceRoleV1::UnlearningAuthority,ref_ms),
    ]
}
fn signed_root(j:&Value,ref_ms:u64,signature:[u8;64])->(
    LearningTrustRootV1,SignedLearningTrustDistributionV1){
    let scope=dg(SCOPE);
    let root=LearningTrustRootV1{
        root_id:id("lab.root"),scope_digest:scope,
        verifying_key:pubkey(j,"root"),valid_from:ref_ms-20000,
        expires_at:ref_ms+3_600_000,revoked_at:None,
    };
    let distribution=SignedLearningTrustDistributionV1{
        distribution:LearningTrustDistributionV1{
            distribution_id:id("lab.real-source.distribution"),
            generation:1,effective_at:ref_ms-1000,
            trust:LearningEvidenceTrustV1{
                scope_digest:scope,objective_digest:dg(OBJECTIVE),
                authority_epoch:19,signers:original_test_keys(j,ref_ms),
            },
        },
        root_id:id("lab.root"),issued_at:ref_ms-1000,
        expires_at:ref_ms+2_700_000,signature,
    };
    (root,distribution)
}
fn verify_root(j:&Value,ref_ms:u64,signature:[u8;64],now:u64)->ActivatedLearningTrustV1{
    let (root,signed)=signed_root(j,ref_ms,signature);
    activate_learning_trust(&root,signed,None,now).expect("original trust activated")
}
fn evidence(w:&LedgerWriter,role:&str,r:LearningEvidenceRoleV1,payload:&[u8],
            name:&str,now:u64)->SignedLearningEvidenceV1{
    let mut doc=SignedLearningEvidenceV1{
        evidence_id:id(name),principal_id:id(&format!("lab.{role}")),role:r,
        trust_digest:w.verifier().trust_digest(),
        scope_digest:dg(SCOPE),objective_digest:dg(OBJECTIVE),authority_epoch:19,
        issued_at:now,expires_at:now+15*60*1000,
        payload_digest:Digest32::of_bytes(payload),signature:[0;64],
    };
    doc.signature=sig(role,&doc.signing_bytes());
    doc
}
fn write_one(dir:&Path,name:&str,v:&Value){
    let raw=serde_json::to_vec(v).unwrap();
    let path=dir.join(name);
    let mut f=OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).unwrap();
    assert_eq!(f.metadata().unwrap().permissions().mode()&0o777,0o600);
    f.write_all(&raw).unwrap();f.write_all(b"\n").unwrap();f.sync_all().unwrap();
    File::open(dir).unwrap().sync_all().unwrap();
}
fn require_private_lab(dir:&Path) {
    assert!(dir.is_dir()&&!dir.is_symlink());
    #[cfg(unix)] {
        assert_eq!(dir.metadata().unwrap().permissions().mode()&0o777,0o700);
    }
}
fn file_new(dir:&Path,name:&str)->File{
    let f=OpenOptions::new().write(true).read(true).create_new(true).mode(0o600).open(dir.join(name)).unwrap();
    assert_eq!(f.metadata().unwrap().permissions().mode()&0o777,0o600);
    f
}
fn file_open(dir:&Path,name:&str)->File{
    let f=OpenOptions::new().write(true).read(true).open(dir.join(name)).unwrap();
    assert_eq!(f.metadata().unwrap().permissions().mode()&0o777,0o600);
    f
}
fn writer_recover(j:&Value,ref_ms:u64,rsig:[u8;64],dir:&Path)->LedgerWriter {
    let binding=dg("ONLY_LAB_REAL_SOURCE_NO_BROKER");
    let witness=LedgerWitnessStore::recover(file_open(dir,"witness"),binding).unwrap();
    let ack=witness.frontier().unwrap().anchor;
    let journal=DurableLedger::recover(file_open(dir,"ledger"),binding,64,
            LedgerRecovery::Acknowledged(ack)).unwrap();
    let trust=verify_root(j,ref_ms,rsig,ms());
    let d=File::open(dir).unwrap();
    LedgerWriter::from_durable(journal,witness,trust,&d,&d).unwrap()
}
fn expected_packet(p:&Value){
    assert_eq!(p["schema"],SCHEMA);
    assert_eq!(p["policy_id"],POLICY);
    for k in ["trade_authority","original_llm_policy_probability","broker_order_was_sent",
              "product_root_enrolled","after_cost_trading_pnl"] {assert_eq!(p[k],false);}
    assert_eq!(p["source_kind"],"REAL_ORIGINAL_IB_PAPER_LOCAL_SHA_CHAIN");
    let decision_at=p["decision_at_ms"].as_u64().unwrap();
    let due=p["outcome_due_ms"].as_u64().unwrap();
    assert_eq!(due-decision_at,300000,"exact original future 5minute outcome");
    assert!(decision_at>=p["first_window_ms"].as_u64().unwrap());
    assert!(decision_at<=p["last_window_ms"].as_u64().unwrap());
    assert!(decision_at>=p["plan_sealed_at_ms"].as_u64().unwrap()+30000);
    for k in ["episode_id","plan_id","original_quote_receipt",
              "preoutcome_feature_id","shadow_decision_sha256"]{
        let x=p[k].as_str().unwrap();assert_eq!(x.len(),64,"SHA256 identity");
        assert!(x.bytes().all(|c|c.is_ascii_hexdigit()));
    }
    assert_eq!(p["source_input_receipts"].as_array().unwrap().len(),6);
    assert_eq!(p["original_quote_receipt"],p["source_input_receipts"][5]);
    let sel=p["selected"].as_str().unwrap();
    let c=p["candidate_grammar"].as_array().unwrap();
    let names:Vec<_>=c.iter().map(|x|x.as_str().unwrap()).collect();
    assert!(names==vec!["NO_TRADE","UP_SIGNAL","DOWN_SIGNAL"]||
            names==vec!["NO_TRADE"]);
    assert!(names.contains(&sel));
    let weight=p["selected_propensity_q32_raw"].as_u64().unwrap();
    if names.len()==3{
        let q=if sel=="NO_TRADE"{1_u64<<31}else{1_u64<<30};
        assert_eq!(weight,q);
    }else{
        assert_eq!(sel,"NO_TRADE");
        assert_eq!(weight,1_u64<<32);
    }
}
fn native_decision(p:&Value,packet_raw:&[u8])->ProductionDecisionV2 {
    expected_packet(p);
    let name=|v:&str|match v{
        "NO_TRADE"=>"abstain","UP_SIGNAL"=>"lab.up_signal",
        "DOWN_SIGNAL"=>"lab.down_signal",_=>panic!("forbidden lab outcome")
    };
    let candidates:Vec<StableId>=p["candidate_grammar"].as_array().unwrap()
        .iter().map(|x|id(name(x.as_str().unwrap()))).collect();
    ProductionDecisionV2{
        record_id:id("lab.originalmarket.decision"),
        episode_id:id("lab.originalmarket.episode"),
        run_snapshot_digest:Digest32::of_bytes(packet_raw),
        objective_digest:dg(OBJECTIVE),
        policy_digest:dg(POLICY),candidate_ids:candidates.clone(),
        selected_candidate_id:id(name(p["selected"].as_str().unwrap())),
        selected_propensity:ProbabilityQ32::from_raw(
             p["selected_propensity_q32_raw"].as_u64().unwrap()).unwrap(),
        completeness:CandidateSetCompletenessReceiptV1{
            set_id:id("lab.originalmarket.candidate.set"),
            state_digest:dg(p["original_quote_receipt"].as_str().unwrap()),
            generator_id:id("lab.generator"),
            generator_code_digest:dg(p["generator_code_sha256"].as_str().unwrap()),
            grammar_digest:dg("test-only original shadow-grammar BID-ASK no order"),
            hard_filter_digest:dg("test-only spread ceiling 3 bps hardfilter"),
            truncation_digest:dg("test-only full candidate set no truncation"),
            candidates_digest:candidate_ids_digest_v2(&candidates),
            candidate_count:candidates.len() as u32,omitted_count_bound:0,
            canonical_order_digest:candidate_order_digest_v2(&candidates),
            complete_for_generator:true,
        },
        support_digest:dg(p["preoutcome_feature_id"].as_str().unwrap()),
    }
}

// Strictly test-only independent physical escrow BEFORE any native append.
// The original Canonical library is unmodified. A separate X230/Mac process
// must O_EXCL+fsync the exact detached-signer event intent, then return its
// public Ed25519 receipt. The transport independently reads back the
// receipt hash on its originating physical host before ACKing.
const ESCROW_DOMAIN:&[u8]=b"heptatrader.test-only.independently-held-recovery-intent.v1\x00";
pub(crate) fn prove_independent_escrow(
    pubmanifest:&Value,stage:&str,typed_payload:&[u8],
    signed:&SignedLearningEvidenceV1, packet_raw:&[u8],source:&Value,
    predecessor:Digest32,existing_witness_sequence:u64,
    folder:&Path,
) {
    assert!(stage=="decision"||stage=="outcome");
    let role=if stage=="decision"{"generator"}else{"observer"};
    let escrow_role=if stage=="decision"{"escrow_decision"}else{"escrow_outcome"};
    let escrow_host=if stage=="decision"{"qian-ThinkPad-X230"}else{"MacBook-Pro.local"};
    let next_seq=if stage=="decision"{1}else{2};
    assert_eq!(existing_witness_sequence,next_seq-1);
    let intent=json!({
        "schema":"heptatrader.train-v2-original-native-before-append-escrow-intent.v1",
        "stage":stage,"native_signer":role,
        "source_policy":POLICY,
        "source_quote_receipt":source["original_quote_receipt"],
        "test_source_packet_sha256":Digest32::of_bytes(packet_raw).to_string(),
        "original_native_payload_sha256":Digest32::of_bytes(typed_payload).to_string(),
        "original_native_evidence_message_sha256":
            Digest32::of_bytes(&signed.signing_bytes()).to_string(),
        "original_detached_signature_sha256":Digest32::of_bytes(&signed.signature).to_string(),
        "expected_predecessor_chain_digest":predecessor.to_string(),
        "expected_previous_witness_sequence":existing_witness_sequence,
        "next_ledger_sequence":next_seq,
        "source_time_ms":if stage=="decision"{
            source["decision_at_ms"].as_u64().unwrap()
        }else{
            ms()
        },
        "outcome_due_ms":source["outcome_due_ms"],
        "test_only":true,"product_trading_authority":false,
    });
    let body=serde_json::to_vec(&intent).unwrap();
    assert!(body.len()<=4000);
    println!("ESCROW_REQUEST {}",hx(&body));
    io::stdout().flush().unwrap();
    let mut line=String::new();
    io::stdin().read_line(&mut line).expect("independent test escrow reply");
    let tokens:Vec<&str>=line.split_whitespace().collect();
    assert_eq!(tokens.len(),2,"exact durable escrow receipt response");
    assert_eq!(tokens[0],"ESCROW_ACK");
    let original=fromhex(tokens[1]);
    assert!(original.len()<=8192);
    let receipt:Value=serde_json::from_slice(&original).expect("typed independent escrow");
    assert_eq!(receipt["schema"],"heptatrader.train-v2-escrow-signed-original-native-intent.v1");
    assert_eq!(receipt["body"],intent,"cannot escrow substituted signed action");
    assert_eq!(receipt["body_sha256"],Digest32::of_bytes(&body).to_string());
    assert_eq!(receipt["escrow_test_role"],escrow_role);
    assert_eq!(receipt["signing_host"],escrow_host);
    assert_eq!(receipt["message_domain_hex"],hx(ESCROW_DOMAIN));
    assert_eq!(receipt["test_only"],true);
    assert_eq!(receipt["production_root_authority"],false);
    assert_eq!(receipt["durably_persisted_before_reply"],true);
    let when=receipt["intent_held_at_ms"].as_u64().unwrap();
    assert!(when>=signed.issued_at && when<=ms()+2000);
    assert!(when<signed.expires_at);
    if stage=="decision"{
        assert!(when<source["outcome_due_ms"].as_u64().unwrap());
    }
    let pinned=pubkey(pubmanifest,escrow_role);
    assert_eq!(receipt["public_key_hex"],hx(&pinned));
    let vk=VerifyingKey::from_bytes(&pinned).expect("escrow pinned public");
    assert!(!vk.is_weak());
    let signature: [u8;64]=fixed(fromhex(receipt["signature_hex"].as_str().unwrap()));
    let mut signed_message=ESCROW_DOMAIN.to_vec();
    signed_message.extend_from_slice(&body);
    signed_message.extend_from_slice(&when.to_be_bytes());
    vk.verify(&signed_message,&Signature::from_bytes(&signature))
        .expect("independently held intent signature verified");
    write_one(folder,&format!("original-native-{stage}-independent-escrow.json"),&receipt);
    // The escrow never counts as a production authority. The original native
    // product Writer still separately authenticates Mac Generator and X230
    // Observer evidence before an event is allowed to advance.
}

fn do_init(pubfile:&str,packetfile:&str,dirname:&str){
    let (j,_)=read(pubfile);assert_eq!(j["purpose"],"test-only-no-production");
    let (p,raw)=read(packetfile);expected_packet(&p);
    let dir=Path::new(dirname);require_private_lab(dir);
    let now=ms();
    let at=p["decision_at_ms"].as_u64().unwrap();
    assert!(now>=at&&now<at+120000,"native source-time witness cannot be retroactively backdated");
    assert!(now<p["outcome_due_ms"].as_u64().unwrap(),
            "native generator MUST sign before price outcome");
    let ref_ms=now;
    let (root,mut dist)=signed_root(&j,ref_ms,[0;64]);
    dist.signature=sig("root",&dist.signing_bytes().unwrap());
    let mut bad=dist.clone();bad.signature[0]^=0x80;
    assert!(activate_learning_trust(&root,bad,None,ms()).is_err());
    let trust=activate_learning_trust(&root,dist.clone(),None,ms()).unwrap();
    write_one(dir,"original-test-trust.json",&json!({
        "ref_ms":ref_ms,"root_signature_hex":hx(&dist.signature),
        "test_only":true,"real_quote_public_source_not_root_trust":true
    }));
    let binding=dg("ONLY_LAB_REAL_SOURCE_NO_BROKER");
    let journal=DurableLedger::create(file_new(dir,"ledger"),binding,64).unwrap();
    let witness=LedgerWitnessStore::create(file_new(dir,"witness"),binding).unwrap();
    let d=File::open(dir).unwrap();
    let mut writer=LedgerWriter::from_durable(journal,witness,trust,&d,&d).unwrap();
    let request=native_decision(&p,&raw);
    let payload=decision_signing_payload_v2(&request).unwrap();
    let doc=evidence(&writer,"generator",LearningEvidenceRoleV1::Generator,
                     &payload,"lab.originalmarket.generator_evidence.signed",ms());
    let mut flip=doc.clone();flip.signature[0]^=1;
    assert!(writer.append_decision(Digest32::ZERO,request.clone(),&flip,ms()).is_err());
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,0);
    prove_independent_escrow(&j,"decision",&payload,&doc,&raw,&p,
                             Digest32::ZERO,0,dir);
    let ack=writer.append_decision(Digest32::ZERO,request,&doc,ms())
        .expect("Mac signed genuine new source-time shadow-only original native Decision");
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
    write_one(dir,"native-source-decision-ack.json",&json!({
        "schema":"heptatrader.train-v2-true-real-source-native-test-lab-decision.v1",
        "receipt_kind":"PRODUCT_NATIVE_SOURCE_COMPATIBLE_TEST_ONLY",
        "source_packet_sha256":hx(Digest32::of_bytes(&raw).as_array()),
        "shadow_decision_sha256":p["shadow_decision_sha256"],
        "source_receipt":p["original_quote_receipt"],
        "selected_signal":p["selected"],
        "selected_shadow_propensity_q32_raw":p["selected_propensity_q32_raw"],
        "decision_at_ms":p["decision_at_ms"],
        "native_acknowledged_at_ms":ms(),
        "outcome_due_ms":p["outcome_due_ms"],
        "native_witness_ack_sequence":1,
        "native_witness_chain_sha_debug":format!("{:?}",ack.chain_digest),
        "original_opaque_trader_policy":false,
        "test_root_not_product_enrolled":true,
        "after_cost_PnL":false,"broker_orders":0
    }));
    println!("RESULT_JSON {}",json!({
        "status":"NEW_REAL_QUOTE_PREOUTCOME_MAC_SIGNED_NATIVE_DECISION_TEST_ONLY",
        "native_witness_ack_sequence":1,"native_decision_before_horizon":true,
        "producer_actual_host":"MacBook-Pro.local",
        "broker_orders_sent":0,"product_authority":false
    }));
}
fn do_finish(pubfile:&str,packetfile:&str,dirname:&str,outcomefile:&str) {
    let (j,_)=read(pubfile);
    let (p,packet_raw)=read(packetfile);expected_packet(&p);
    let dir=Path::new(dirname);require_private_lab(dir);
    let (rootdoc,_)=read(dir.join("original-test-trust.json").to_str().unwrap());
    assert_eq!(rootdoc["test_only"],true);
    let ref_ms=rootdoc["ref_ms"].as_u64().unwrap();
    let root_sig: [u8;64]=fixed(fromhex(rootdoc["root_signature_hex"].as_str().unwrap()));
    let (out,raw)=read(outcomefile);
    assert_eq!(out["schema"],"heptatrader.train-v2-real-source-crosshost-price-label.v1");
    assert_eq!(out["research_only"],true);
    assert_eq!(out["production_observer_enrolled"],false);
    assert_eq!(out["trade_pnl_reported"],false);
    assert_eq!(out["source_shadow_packet_sha256"],hx(Digest32::of_bytes(&packet_raw).as_array()));
    assert_eq!(out["episode_id"],p["episode_id"]);
    assert_eq!(out["outcome_due_ms"],p["outcome_due_ms"]);
    let observed_ms=out["endpoint_at_ms"].as_u64().unwrap();
    let evaluated_ms=out["verified_at_ms"].as_u64().unwrap();
    let due=p["outcome_due_ms"].as_u64().unwrap();
    assert!(ms()>=evaluated_ms&&evaluated_ms>=observed_ms);
    assert!(observed_ms>=due&&observed_ms<=due+5000);
    let mut writer=writer_recover(&j,ref_ms,root_sig,dir);
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
    let original=writer.records().unwrap();
    assert_eq!(original.len(),1);
    // A REAL quote-midprice delta is NOT a dollar PnL. Unit digest encodes
    // exactly that; native lab is excluded from product outcome admission.
    let origin=original[0].chain_digest;
    let observer=original_test_keys(&j,ref_ms)[1].principal.clone();
    let value=FixedQ32::from_raw(out["midprice_change_basis_points_q32_raw"].as_i64().unwrap());
    let result=AuthenticatedOutcomeV1{
        record_id:id("lab.originalmarket.outcome"),
        outcome_id:id("lab.originalmarket.midprice"),
        episode_id:id("lab.originalmarket.episode"),
        observer, observed_at:Some(observed_ms),
        value:Some(value),
        unit_profile_digest:dg("ORIGINAL_QUOTE_MIDPRICE_BPS_EXPLICITLY_NOT_NET_PNL"),
        support_digest:Digest32::of_bytes(&raw),
        watermark:OutcomeWatermarkV1{
            latest_observable_at:evaluated_ms,
            expected_delay_profile_digest:dg("5MIN_ORIGINAL_BROKER_QUOTE_TOLERANCE_5S_TEST_ONLY"),
            terminality:OutcomeTerminalityV1::Terminal,
            censoring_reason:None,correction_predecessor:None,
            finalized_at:Some(evaluated_ms),
        },
    };
    let signed=evidence(&writer,"observer",LearningEvidenceRoleV1::Observer,
                        &outcome_signing_payload_v2(&result),
                        "lab.originalmarket.obs.signed",ms());
    let mut changed=signed.clone();changed.signature[1]^=0x33;
    assert!(writer.append_outcome(origin,result.clone(),&changed,ms()).is_err());
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
    let outcome_payload=outcome_signing_payload_v2(&result);
    prove_independent_escrow(&j,"outcome",&outcome_payload,&signed,&packet_raw,&p,
                             origin,1,dir);
    let outcome_ack=writer.append_outcome(origin,result,&signed,ms())
        .expect("X230 real price label signed as test-only terminal outcome");
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,2);
    let plan=DatasetFreezePlanV2{
        snapshot_id:id("lab.originalmarket.dataset"),
        objective_digest:dg(OBJECTIVE),
        inclusion_policy_digest:dg("TEST_ONLY_REAL_QUOTE_RESEARCH_NOT_ECONOMIC_TRAINING"),
    };
    let payload=dataset_freeze_signing_payload_v2(&writer.snapshot().unwrap(),&plan).unwrap();
    let proof=evidence(&writer,"evaluator",LearningEvidenceRoleV1::Evaluator,
        &payload,"lab.originalmarket.eval.signed",ms());
    let dataset=writer.freeze_dataset(plan,&proof,ms()).unwrap();
    writer.revalidate_dataset_snapshot(&dataset,ms()).unwrap();
    assert_eq!(dataset.snapshot.source_record_digests.len(),2);
    write_one(dir,"native-price-outcome-and-dataset-test-ack.json",&json!({
        "schema":"heptatrader.train-v2-original-market-native-test-closed.v1",
        "real_price_outcome_bps":out["market_midprice_change_bps"],
        "original_quote_endpoint_hash":out["endpoint_quote_receipt"],
        "native_writer_test_decision_precommitted":true,
        "observer_actual_signing_host":"qian-ThinkPad-X230",
        "actual_source_outcome_signed_after_due":true,
        "native_durable_witness_sequence":2,
        "native_final_chain_debug":format!("{:?}",outcome_ack.chain_digest),
        "native_V3_dataset_frozen_and_currently_revalidated":true,
        "native_V3_dataset_is_product_market_eligible":false,
        "actual_order_execution_cost_and_PnL":false,
        "original_llm_complete_policy_qualified":false,
        "test_root_has_product_authority":false,
        "product_trading_promotion_allowed":false
    }));
    println!("RESULT_JSON {}",json!({
        "status":"NEW_REAL_MARKET_PRICE_LABEL_NATIVE_COLD_REOPEN_TEST_COMPLETED",
        "ledger_witness_records":2,
        "native_v3_test_revalidation_passed":true,
        "actual_independent_test_signature_hosts":2,
        "real_paper_order_or_broker_request":false,
        "product_authority":false
    }));
}
fn do_recover(pubfile:&str,dirname:&str) {
    let (j,_)=read(pubfile);
    let dir=Path::new(dirname);require_private_lab(dir);
    let (r,_)=read(dir.join("original-test-trust.json").to_str().unwrap());
    let signed:[u8;64]=fixed(fromhex(r["root_signature_hex"].as_str().unwrap()));
    let writer=writer_recover(&j,r["ref_ms"].as_u64().unwrap(),signed,dir);
    let num=writer.records().unwrap().len();
    assert_eq!(num,writer.witness_frontier().unwrap().anchor.sequence as usize);
    assert!((1..=2).contains(&num));
    println!("RESULT_JSON {}",json!({
        "status":"REAL_SOURCE_TEST_ONLY_LEDGER_COLD_RECOVERY_WITH_PUBLIC_TRUST",
        "records":num,"exact_ack_witness":true,
        "private_signers_read":false,"new_signatures_sent":0,
        "product_authority":false
    }));
}
fn main(){
    let args:Vec<String>=std::env::args().collect();
    match args.as_slice(){
        [_,mode,pubfile,packet,dir] if mode=="init"=>
            do_init(pubfile,packet,dir),
        [_,mode,pubfile,packet,dir,outcome] if mode=="finish"=>
            do_finish(pubfile,packet,dir,outcome),
        [_,mode,pubfile,dir] if mode=="recover"=>
            do_recover(pubfile,dir),
        _=>panic!("usage init PUB PACKET DIR | finish PUB PACKET DIR OUTCOME | recover PUB DIR")
    }
}



// New independent test-only recovery commands. No signer request, order,
// new product credential or mutation of the retained original source trial.
// Reconstruct exactly the previously signed Generator/Observer documents
// from public native bytes and archived signatures, not new signing keys.
fn replay_take<'a>(buf:&'a [u8], offset:&mut usize, n:usize)->&'a [u8] {
    assert!(n<=buf.len().saturating_sub(*offset), "REPLAY_NATIVE_MESSAGE_BOUNDS");
    let r=&buf[*offset..*offset+n];
    *offset+=n;
    r
}
fn replay_u64(buf:&[u8], offset:&mut usize)->u64 {
    u64::from_be_bytes(fixed(replay_take(buf,offset,8).to_vec()))
}
fn replay_id(buf:&[u8],offset:&mut usize)->StableId {
    let n=replay_u64(buf,offset) as usize;
    assert!((1..=128).contains(&n));
    let bytes=replay_take(buf,offset,n);
    id(std::str::from_utf8(bytes).expect("native ID ASCII"))
}
fn replay_digest(buf:&[u8],offset:&mut usize)->Digest32 {
    Digest32::from_array(fixed(replay_take(buf,offset,32).to_vec()))
}
fn replay_signed(att:&Value,role:&str,role_tag:u8)->SignedLearningEvidenceV1 {
    let choices=att["signatures"].as_array().expect("archived signed native entries");
    let prior=choices.iter().find(|e|e["role"]==role).expect("role");
    assert_eq!(prior["signing_host"],
        match role{"generator"=>"MacBook-Pro.local","observer"=>"qian-ThinkPad-X230",_=>panic!("not replayed")});
    let raw=fromhex(prior["public_message_hex"].as_str().unwrap());
    let domain=b"hepta.learning-ledger.signed-evidence.v1";
    assert!(raw.starts_with(domain),"only canonical native signed evidence");
    let mut i=domain.len();
    let evidence_id=replay_id(&raw,&mut i);
    let principal_id=replay_id(&raw,&mut i);
    assert_eq!(replay_take(&raw,&mut i,1)[0],role_tag);
    assert_eq!(principal_id,id(&format!("lab.{role}")));
    let trust_digest=replay_digest(&raw,&mut i);
    let scope_digest=replay_digest(&raw,&mut i);
    let objective_digest=replay_digest(&raw,&mut i);
    let authority_epoch=replay_u64(&raw,&mut i);
    let issued_at=replay_u64(&raw,&mut i);
    let expires_at=replay_u64(&raw,&mut i);
    let payload_digest=replay_digest(&raw,&mut i);
    assert_eq!(i,raw.len(),"no appended forgery after native signed bytes");
    assert_eq!(authority_epoch,19);
    assert_eq!(scope_digest,dg(SCOPE));
    assert_eq!(objective_digest,dg(OBJECTIVE));
    let doc=SignedLearningEvidenceV1{
        evidence_id,principal_id,
        role:if role_tag==0{LearningEvidenceRoleV1::Generator}
             else{LearningEvidenceRoleV1::Observer},
        trust_digest,scope_digest,objective_digest,authority_epoch,
        issued_at,expires_at,payload_digest,
        signature:fixed(fromhex(prior["signature_hex"].as_str().unwrap()))
    };
    assert_eq!(raw,doc.signing_bytes(),"byte-exact original detached signed message");
    assert!(doc.issued_at < doc.expires_at);
    doc
}
fn replay_outcome(p:&Value,out:&Value,original_outcome_raw:&[u8],ref_ms:u64,j:&Value)->AuthenticatedOutcomeV1 {
    assert_eq!(out["schema"],"heptatrader.train-v2-real-source-crosshost-price-label.v1");
    assert_eq!(out["research_only"],true);
    assert_eq!(out["production_observer_enrolled"],false);
    assert_eq!(out["episode_id"],p["episode_id"]);
    let event_at=out["endpoint_at_ms"].as_u64().unwrap();
    let verified=out["verified_at_ms"].as_u64().unwrap();
    assert!(event_at>=p["outcome_due_ms"].as_u64().unwrap());
    assert!(event_at<=p["outcome_due_ms"].as_u64().unwrap()+5000);
    assert!(verified>=event_at);
    AuthenticatedOutcomeV1 {
        record_id:id("lab.originalmarket.outcome"),
        outcome_id:id("lab.originalmarket.midprice"),
        episode_id:id("lab.originalmarket.episode"),
        observer:original_test_keys(j,ref_ms)[1].principal.clone(),
        observed_at:Some(event_at),
        value:Some(FixedQ32::from_raw(out["midprice_change_basis_points_q32_raw"].as_i64().unwrap())),
        unit_profile_digest:dg("ORIGINAL_QUOTE_MIDPRICE_BPS_EXPLICITLY_NOT_NET_PNL"),
        support_digest:Digest32::of_bytes(original_outcome_raw),
        watermark:OutcomeWatermarkV1{
            latest_observable_at:verified,
            expected_delay_profile_digest:dg("5MIN_ORIGINAL_BROKER_QUOTE_TOLERANCE_5S_TEST_ONLY"),
            terminality:OutcomeTerminalityV1::Terminal,
            censoring_reason:None,correction_predecessor:None,
            finalized_at:Some(verified),
        },
    }
}
pub(crate) fn replay_original_signed_test_packet(
    pubfile:&str,packetfile:&str,outcomefile:&str,dirfile:&str,
    generator_attest:&str,observer_attest:&str,kind:&str
) {
    assert!(["acknowledged","lag-exact","lag-wrong","expired","lag-torn","lag-expired"].contains(&kind));
    let (j,_)=read(pubfile);
    assert_eq!(j["purpose"],"test-only-no-production");
    let (p,packet_raw)=read(packetfile);
    expected_packet(&p);
    let (out,outcome_raw)=read(outcomefile);
    assert_eq!(out["source_shadow_packet_sha256"],hx(Digest32::of_bytes(&packet_raw).as_array()));
    let (gen_att,_)=read(generator_attest);
    let (obs_att,_)=read(observer_attest);
    assert_eq!(gen_att["stage"],"init");
    assert_eq!(obs_att["stage"],"finish");
    let generator_evidence=replay_signed(&gen_att,"generator",0);
    let obs=replay_signed(&obs_att,"observer",1);
    let dir=Path::new(dirfile);
    require_private_lab(dir);
    let (r,_)=read(dir.join("original-test-trust.json").to_str().unwrap());
    assert_eq!(r["test_only"],true);
    let ref_ms=r["ref_ms"].as_u64().unwrap();
    let root_sig:[u8;64]=fixed(fromhex(r["root_signature_hex"].as_str().unwrap()));
    let mut writer=writer_recover(&j,ref_ms,root_sig,dir);
    let before=writer.witness_frontier().unwrap().anchor.sequence;
    let records=writer.records().unwrap();
    assert_eq!(records.len(),2);
    let request=native_decision(&p,&packet_raw);
    assert_eq!(generator_evidence.payload_digest,Digest32::of_bytes(&decision_signing_payload_v2(&request).unwrap()));
    let outcome=replay_outcome(&p,&out,&outcome_raw,ref_ms,&j);
    assert_eq!(obs.payload_digest,Digest32::of_bytes(&outcome_signing_payload_v2(&outcome)));
    let historical_gen=generator_evidence.issued_at+1;
    let historical_obs=obs.issued_at+1;
    assert!(historical_gen<generator_evidence.expires_at&&historical_obs<obs.expires_at);
    assert!(ms()>generator_evidence.expires_at,"this test deliberately runs after expired generator credential");
    let predecessor=records[0].chain_digest;
    match kind {
        "acknowledged" => {
            assert_eq!(before,2);
            let a=writer.append_decision(Digest32::ZERO,request.clone(),&generator_evidence,historical_gen)
                .expect("historical exact typed Generator signed replay");
            assert_eq!(a.disposition,AppendDisposition::IdempotentReplay);
            assert_eq!(a.sequence.get(),1);
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,2);
            let b=writer.append_outcome(predecessor,outcome.clone(),&obs,historical_obs)
                .expect("historical exact typed Observer signed replay");
            assert_eq!(b.disposition,AppendDisposition::IdempotentReplay);
            assert_eq!(b.sequence.get(),2);
            let mut altered=request;
            altered.selected_candidate_id=id("lab.up_signal");
            assert!(writer.append_decision(Digest32::ZERO,altered,&generator_evidence,historical_gen).is_err(),
                    "cannot reuse signed original evidence for a different selected action");
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,2);
        }
        "expired" => {
            assert_eq!(before,2);
            assert!(writer.append_decision(Digest32::ZERO,request,&generator_evidence,ms()).is_err(),
                    "real current-time expired test signer not permitted to append");
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,2);
        }
        "lag-expired" => {
            assert_eq!(before,1);
            assert!(ms()>obs.expires_at, "current wall time after original test Observer expiry");
            assert!(writer.append_outcome(predecessor,outcome,&obs,ms()).is_err(),
                    "previously signed Observation must not be newly accepted after expiry");
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
        }
        "lag-wrong" => {
            assert_eq!(before,1);
            assert!(writer.append_decision(Digest32::ZERO,request,&generator_evidence,historical_gen).is_err(),
                    "witness one-behind may ONLY retry exact last ledger event");
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
            let mut bad=outcome;
            bad.value=Some(FixedQ32::ZERO);
            assert!(writer.append_outcome(predecessor,bad,&obs,historical_obs).is_err(),
                    "a changed payload cannot repair a missing witness ACK");
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,1);
        }
        "lag-exact" | "lag-torn" => {
            assert_eq!(before,1);
            let b=writer.append_outcome(predecessor,outcome,&obs,historical_obs)
                .expect("native exact last signed Outcome repair one unwitnessed complete event");
            assert_eq!(b.disposition,AppendDisposition::IdempotentReplay);
            assert_eq!(b.sequence.get(),2);
            assert_eq!(writer.witness_frontier().unwrap().anchor.sequence,2);
        }
        _=>unreachable!()
    }
    let after=writer.witness_frontier().unwrap().anchor.sequence;
    assert_eq!(writer.records().unwrap().len(),2);
    println!("RESULT_JSON {}",json!({
        "schema":"heptatrader.train-v2-native-source-signed-historical-replay-lab.v1",
        "replay_mode":kind,"before_witness":before,"after_witness":after,
        "records_still_exactly_two":true,
        "native_generator_public_signer_expired_now":ms()>generator_evidence.expires_at,
        "old_signed_evidence_historical_timestamp_used_for_replay":
            kind!="expired" && kind!="lag-expired",
        "new_signer_requests":0,
        "test_private_signers_present":false,
        "same_real_IB_market_source_testonly_episode":p["episode_id"],
        "real_paper_broker_order_sent":false,
        "production_product_authority":false
    }));
}
