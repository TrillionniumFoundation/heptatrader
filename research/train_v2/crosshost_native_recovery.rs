//! Native cold process recovery with NO signer private key and NO signer RPC.
//! Test-only stored root signature + public key distribution, original ledger,
//! and independently retained witness. No product/runtime activation.
use codex_hepta_learning_ledger::*;
use codex_hepta_types::{Digest32, StableId};
use serde_json::{json,Value};
use std::fs::{File,OpenOptions};
use std::path::Path;

fn id(raw:&str)->StableId { StableId::new(raw.to_owned()).unwrap() }
fn dg(raw:&str)->Digest32 { Digest32::of_bytes(raw.as_bytes()) }
fn decode(raw:&str)->Vec<u8> {
    assert_eq!(raw.len()%2,0);
    (0..raw.len()).step_by(2)
        .map(|i| u8::from_str_radix(&raw[i..i+2],16).unwrap())
        .collect()
}
fn fixed<const N:usize>(value:Vec<u8>)->[u8;N] { value.try_into().ok().unwrap() }
fn pubkey(v:&Value,role:&str)->[u8;32] {
    fixed(decode(v["roles"][role]["public_key_hex"].as_str().unwrap()))
}
fn trusted(role:&str,controller:&str,key:[u8;32],native_role:LearningEvidenceRoleV1)
    ->TrustedLearningSignerV1 {
    let name=format!("lab.{role}");
    TrustedLearningSignerV1 {
        principal: AuthenticatedPrincipalV1 {
            principal_id:id(&name),
            credential_chain_digest:dg(&format!("crosshost-test-only-credential-{role}")),
            signing_key_digest:Digest32::of_bytes(&key),
            scope_digest:dg("crosshost.test-only.scope"),
            authority_epoch:7,authenticated_at:10,expires_at:100,
        },
        controller_id:id(controller),verifying_key:key,
        roles:vec![native_role],revoked_at:None,
    }
}

fn main() {
    let a:Vec<String>=std::env::args().collect();
    assert_eq!(a.len(),4,"pubs, publicly-signed evidence, lab source directory");
    let pub_manifest:Value=serde_json::from_slice(
        &std::fs::read(&a[1]).unwrap()).unwrap();
    assert_eq!(pub_manifest["purpose"],"test-only-no-production");
    let history:Value=serde_json::from_slice(&std::fs::read(&a[2]).unwrap()).unwrap();
    assert_eq!(history["all_test_only"],true);
    assert_eq!(history["native_result"]["native_durable_witness_frontier_records"],3);
    let prior=history["signature_requests"].as_array().unwrap();
    assert_eq!(prior[0]["role"],"root");
    let root_sig:[u8;64]=fixed(decode(prior[0]["signature_hex"].as_str().unwrap()));

    // Reconstitute IDENTICAL original typed trust from public metadata only.
    let scope=dg("crosshost.test-only.scope");
    let root=LearningTrustRootV1 {
        root_id:id("lab.root"),scope_digest:scope,verifying_key:pubkey(&pub_manifest,"root"),
        valid_from:1,expires_at:200,revoked_at:None,
    };
    let signers=vec![
        trusted("generator","physical.mac.controller",pubkey(&pub_manifest,"generator"),
                LearningEvidenceRoleV1::Generator),
        trusted("observer","physical.x230.controller",pubkey(&pub_manifest,"observer"),
                LearningEvidenceRoleV1::Observer),
        trusted("evaluator","physical.qian.eval.controller",pubkey(&pub_manifest,"evaluator"),
                LearningEvidenceRoleV1::Evaluator),
        trusted("privacy","physical.qian.privacy.controller",pubkey(&pub_manifest,"privacy"),
                LearningEvidenceRoleV1::UnlearningAuthority),
    ];
    let signed=SignedLearningTrustDistributionV1 {
        distribution:LearningTrustDistributionV1 {
            distribution_id:id("lab.distribution"),generation:1,effective_at:20,
            trust:LearningEvidenceTrustV1 {
                scope_digest:scope,objective_digest:dg("crosshost.test-only.objective"),
                authority_epoch:7,signers,
            },
        },
        root_id:id("lab.root"),issued_at:15,expires_at:90,signature:root_sig,
    };
    let activated=activate_learning_trust(&root,signed,None,50)
        .expect("previously signed root distribution reactivated WITHOUT private keys");

    let dir=Path::new(&a[3]);
    assert!(dir.is_dir()&&!dir.is_symlink());
    let binding=dg("crosshost-test-only-native-ledger-witness");
    let ledger_file=OpenOptions::new().read(true).write(true)
        .open(dir.join("ledger")).unwrap();
    let witness_file=OpenOptions::new().read(true).write(true)
        .open(dir.join("witness")).unwrap();
    let witness=LedgerWitnessStore::recover(witness_file,binding)
        .expect("actual independent witnessed frontier recovers");
    let anchor=witness.frontier().unwrap().anchor;
    assert_eq!(anchor.sequence,3);
    let ledger=DurableLedger::recover(
        ledger_file,binding,64,LedgerRecovery::Acknowledged(anchor))
        .expect("original canonical durable ledger matches independent ack");
    let d=File::open(dir).unwrap();
    let recovered=LedgerWriter::from_durable(ledger,witness,activated,&d,&d)
        .expect("original product writer recovers authoritative frontier");
    assert_eq!(recovered.witness_frontier().unwrap().anchor.sequence,3);
    assert_eq!(recovered.witness_frontier().unwrap().anchor.chain_digest,anchor.chain_digest);
    assert_eq!(recovered.records().unwrap().len(),3);
    assert_eq!(recovered.snapshot().unwrap().records().len(),3);

    // Copy tamper case into a distinct test-only path. Never corrupt original.
    let corrupt_path=dir.join("tampered-witness-test-only");
    std::fs::copy(dir.join("witness"),&corrupt_path).unwrap();
    let mut bytes=std::fs::read(&corrupt_path).unwrap();
    let last=bytes.len()-1;
    bytes[last]^=0x42;
    std::fs::write(&corrupt_path,bytes).unwrap();
    let corrupt=File::open(&corrupt_path).unwrap();
    assert!(LedgerWitnessStore::recover(corrupt,binding).is_err(),
            "a changed independent witness must not permit recovery");
    std::fs::remove_file(&corrupt_path).unwrap();
    println!("RECOVERY_RESULT_JSON {}",json!({
        "schema":"heptatrader.crosshost-native-cold-recovery.v1",
        "status":"TEST_ONLY_COLD_REOPEN_PASSED",
        "public_signature_material_only":true,
        "private_signer_keys_accessed":false,
        "new_signed_requests_during_recovery":0,
        "native_original_root_distribution_reactivated":true,
        "native_ledger_recovered_with_witness_ack":true,
        "native_record_count":3,
        "witness_ack_sequence":anchor.sequence,
        "witness_anchor_matches_native_ledger":true,
        "tampered_witness_copy_rejected":true,
        "production_trust_distribution_activated":false,
        "real_trader_behavior_propensity_claimed":false,
        "real_broker_outcome_or_after_cost_return_claimed":false
    }));
}
