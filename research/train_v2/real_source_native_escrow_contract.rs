//! Nonproduction original Rust Ed25519 escrow admission contract fixture.
//! All signed event bytes are SYNTHETIC; no product signer, real market
//! source, native LedgerWriter append, actual IB requests, or external escrow.
#[path = "real_source_native_lab.rs"]
mod lab;
use codex_hepta_learning_ledger::{LearningEvidenceRoleV1,SignedLearningEvidenceV1};
use codex_hepta_types::{Digest32,StableId};
use serde_json::{Value,json};
use std::fs;
use std::path::Path;
use std::time::{SystemTime,UNIX_EPOCH};

fn id(s:&str)->StableId{StableId::new(s.to_owned()).unwrap()}
fn dg(s:&str)->Digest32{Digest32::of_bytes(s.as_bytes())}
fn current()->u64{SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as u64}
fn main(){
    let a:Vec<String>=std::env::args().collect();
    assert_eq!(a.len(),4,"STAGE PUBLIC_KEY_HEX EMPTY_PRIVATE_TEST_DIRECTORY");
    let stage=&a[1];
    assert!(stage=="decision"||stage=="outcome");
    let role=if stage=="decision"{"generator"}else{"observer"};
    let pubhex=&a[2];
    assert_eq!(pubhex.len(),64);
    let folder=Path::new(&a[3]);
    assert!(folder.is_dir()&&!folder.is_symlink());
    #[cfg(unix)]{
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(folder.metadata().unwrap().permissions().mode()&0o777,0o700);
    }
    let now=current();
    let sample=b"SYNTHETIC-TYPED-EVIDENCE-NO-MARKET-NO-NET-PNL";
    let doc=SignedLearningEvidenceV1{
        evidence_id:id(&format!("lab.{role}.synthetic.escrow")),
        principal_id:id(&format!("lab.{role}")),
        role:if stage=="decision"{LearningEvidenceRoleV1::Generator}
             else{LearningEvidenceRoleV1::Observer},
        trust_digest:dg("SYNTHETIC-TEST-TRUST"),
        scope_digest:dg("SYNTHETIC-TEST-SCOPE"),
        objective_digest:dg("SYNTHETIC-TEST-OBJECTIVE"),
        authority_epoch:19,
        issued_at:now-2000,
        expires_at:now+600000,
        payload_digest:Digest32::of_bytes(sample),
        signature:[77;64],
    };
    let j:Value=json!({"roles":{
        "escrow_decision":{"public_key_hex":pubhex},
        "escrow_outcome":{"public_key_hex":pubhex}
    }});
    let source=json!({
        "original_quote_receipt":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "decision_at_ms":now-1500,
        "outcome_due_ms":now+300000
    });
    let predecessor=if stage=="decision"{Digest32::ZERO}else{dg("KNOWN-ONE-ACK-PREDECESSOR")};
    let prev=if stage=="decision"{0}else{1};
    lab::prove_independent_escrow(&j,stage,sample,&doc,b"SYNTHETIC-PACKET-TEST-ONLY",
                                  &source,predecessor,prev,folder);
    let file=folder.join(format!("original-native-{stage}-independent-escrow.json"));
    let saved:Value=serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
    assert_eq!(saved["test_only"],true);
    println!("RESULT_JSON {}",json!({
        "result":"SYNTHETIC_TEST_ONLY_ESCROW_PUBLIC_SIGNATURE_ACCEPTED",
        "stage":stage,"no_original_market_or_product_event":true,
        "no_ledger_mutation":true,"native_escrow_gate_signature_checked":true
    }));
}
