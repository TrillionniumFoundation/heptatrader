//! Product-source native CROSS-HOST SIGNATURE test, NEVER production authority.
//!
//! A separate parent orchestrator on qian-qi asks Mac for the Generator
//! signature and X230 for the Observer signature. qian-qi retains root,
//! Evaluator and Unlearning test keys. This binary on Pocket4 imports ONLY
//! public keys and signed bytes, then executes the ORIGINAL native LedgerWriter.
//! All actions/outcomes are SYNTHETIC. No brokerage, financial or real strategy.

use codex_hepta_learning_ledger::*;
use codex_hepta_types::{Digest32, FixedQ32, ProbabilityQ32, StableId};
use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::path::Path;

fn id(raw: &str) -> StableId { StableId::new(raw.to_owned()).expect("lab stable identity") }
fn dg(raw: &str) -> Digest32 { Digest32::of_bytes(raw.as_bytes()) }

fn from_hex(raw: &str) -> Vec<u8> {
    assert!(raw.len() % 2 == 0 && raw.len() <= 8192, "hex length");
    let mut out = Vec::new();
    for i in (0..raw.len()).step_by(2) {
        out.push(u8::from_str_radix(&raw[i..i + 2], 16).expect("hex char"));
    }
    out
}
fn hex(raw: &[u8]) -> String {
    raw.iter().map(|x| format!("{x:02x}")).collect()
}
fn fixed<const N: usize>(vec: Vec<u8>) -> [u8; N] {
    vec.try_into().ok().expect("exact signature or key size")
}

fn request_signature(role: &str, bytes: &[u8]) -> [u8; 64] {
    // Only explicitly allowed test roles can request signatures.
    assert!(["root", "generator", "observer", "evaluator", "privacy"].contains(&role));
    println!("SIGN_REQUEST {role} {}", hex(bytes));
    io::stdout().flush().expect("flush signature request");
    let mut line = String::new();
    io::stdin().read_line(&mut line).expect("signed response from orchestrator");
    let line = line.trim();
    let parts: Vec<&str> = line.split(' ').collect();
    assert!(parts.len() == 2 && parts[0] == "SIG", "invalid signer response");
    fixed(from_hex(parts[1]))
}
fn pubkey(manifest: &Value, role: &str) -> [u8; 32] {
    let field = manifest["roles"][role]["public_key_hex"].as_str().expect("pinned public key");
    fixed(from_hex(field))
}
fn trusted(
    role: &str, controller: &str, key: [u8; 32], native_role: LearningEvidenceRoleV1,
) -> TrustedLearningSignerV1 {
    let name = format!("lab.{role}");
    TrustedLearningSignerV1 {
        principal: AuthenticatedPrincipalV1 {
            principal_id: id(&name),
            credential_chain_digest: dg(&format!("crosshost-test-only-credential-{role}")),
            signing_key_digest: Digest32::of_bytes(&key),
            scope_digest: dg("crosshost.test-only.scope"),
            authority_epoch: 7,
            authenticated_at: 10,
            expires_at: 100,
        },
        controller_id: id(controller),
        verifying_key: key,
        roles: vec![native_role],
        revoked_at: None,
    }
}
fn evidence(
    writer: &LedgerWriter, role: &str, native_role: LearningEvidenceRoleV1, payload: &[u8],
    nonce: &str,
) -> SignedLearningEvidenceV1 {
    let mut value = SignedLearningEvidenceV1 {
        evidence_id: id(&format!("lab.{role}.{nonce}")),
        principal_id: id(&format!("lab.{role}")),
        role: native_role,
        trust_digest: writer.verifier().trust_digest(),
        scope_digest: dg("crosshost.test-only.scope"),
        objective_digest: dg("crosshost.test-only.objective"),
        authority_epoch: 7,
        issued_at: 20,
        expires_at: 90,
        payload_digest: Digest32::of_bytes(payload),
        signature: [0; 64],
    };
    value.signature = request_signature(role, &value.signing_bytes());
    value
}

fn lab_decision() -> ProductionDecisionV2 {
    // This is NOT derived from live quotes or the original LLM. Its
    // probability is defined by a separate synthetic shadow grammar.
    let candidates = vec![id("abstain"), id("lab.up_signal"), id("lab.down_signal")];
    ProductionDecisionV2 {
        record_id: id("lab.decision.record"),
        episode_id: id("lab.episode"),
        run_snapshot_digest: dg("SYNTHETIC-TEST-NOT-A-REAL-REQUEST"),
        objective_digest: dg("crosshost.test-only.objective"),
        policy_digest: dg("SHADOW_TEST_TWO_BIT_Q32_ONLY"),
        candidate_ids: candidates.clone(),
        selected_candidate_id: id("abstain"),
        selected_propensity: ProbabilityQ32::from_raw(1_u64 << 31).expect("Q32 valid 1/2"),
        completeness: CandidateSetCompletenessReceiptV1 {
            set_id: id("lab.candidate.set"),
            state_digest: dg("SYNTHETIC-QUOTES-NOT-AN-IB-SOURCE"),
            generator_id: id("lab.generator"),
            generator_code_digest: dg("SYNTHETIC-CONTROLLER-NOT-DEPLOYED"),
            grammar_digest: dg("test.closed.no_trade.up_signal.down_signal"),
            hard_filter_digest: dg("synthetic-filter-no-orders"),
            truncation_digest: dg("no-candidate-omission"),
            candidates_digest: candidate_ids_digest_v2(&candidates),
            candidate_count: 3,
            omitted_count_bound: 0,
            canonical_order_digest: candidate_order_digest_v2(&candidates),
            complete_for_generator: true,
        },
        support_digest: dg("SYNTHETIC-EPISODE-WITH-NO-BROKER-REQUEST"),
    }
}

fn lab_outcome(observer: AuthenticatedPrincipalV1) -> AuthenticatedOutcomeV1 {
    AuthenticatedOutcomeV1 {
        record_id: id("lab.outcome.record"),
        outcome_id: id("lab.outcome.id"),
        episode_id: id("lab.episode"),
        observer,
        observed_at: Some(40),
        value: Some(FixedQ32::ZERO),
        unit_profile_digest: dg("SYNTHETIC-UNIT-NOT-NET-PNL"),
        support_digest: dg("SYNTHETIC-OUTCOME-NOT-A-BROKER-FILL"),
        watermark: OutcomeWatermarkV1 {
            latest_observable_at: 45,
            expected_delay_profile_digest: dg("synthetic-delay-not-real"),
            terminality: OutcomeTerminalityV1::Terminal,
            censoring_reason: None,
            correction_predecessor: None,
            finalized_at: Some(46),
        },
    }
}

fn only_file(dir: &Path, name: &str) -> File {
    OpenOptions::new().read(true).write(true).create_new(true)
        .open(dir.join(name)).expect("create test-only private ledger")
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert!(args.len() == 3, "must supply pinned pub manifest, isolated ledger directory");
    let public: Value = serde_json::from_slice(&std::fs::read(&args[1]).expect("pinned pub manifest"))
        .expect("parse native host pinned test-only manifest");
    assert!(public["schema"] == "hepta-train-v2-crosshost-native-signing-pubs.v1");
    assert!(public["purpose"] == "test-only-no-production");
    let directory = Path::new(&args[2]);
    assert!(directory.is_dir() && !directory.is_symlink(), "exclusive isolated witness root");

    let pub_root = pubkey(&public, "root");
    let keys = [
        trusted("generator", "physical.mac.controller", pubkey(&public, "generator"),
                LearningEvidenceRoleV1::Generator),
        trusted("observer", "physical.x230.controller", pubkey(&public, "observer"),
                LearningEvidenceRoleV1::Observer),
        trusted("evaluator", "physical.qian.eval.controller", pubkey(&public, "evaluator"),
                LearningEvidenceRoleV1::Evaluator),
        trusted("privacy", "physical.qian.privacy.controller", pubkey(&public, "privacy"),
                LearningEvidenceRoleV1::UnlearningAuthority),
    ];
    let observer = keys[1].principal.clone();
    let scope = dg("crosshost.test-only.scope");
    let root = LearningTrustRootV1 {
        root_id: id("lab.root"),
        scope_digest: scope,
        verifying_key: pub_root,
        valid_from: 1,
        expires_at: 200,
        revoked_at: None,
    };
    let mut signed = SignedLearningTrustDistributionV1 {
        distribution: LearningTrustDistributionV1 {
            distribution_id: id("lab.distribution"),
            generation: 1,
            effective_at: 20,
            trust: LearningEvidenceTrustV1 {
                scope_digest: scope,
                objective_digest: dg("crosshost.test-only.objective"),
                authority_epoch: 7,
                signers: keys.to_vec(),
            },
        },
        root_id: id("lab.root"),
        issued_at: 15,
        expires_at: 90,
        signature: [0; 64],
    };
    signed.signature = request_signature("root", &signed.signing_bytes().unwrap());
    let mut invalid_root_signature = signed.clone();
    invalid_root_signature.signature[0] ^= 0x80;
    assert!(
        activate_learning_trust(&root, invalid_root_signature, None, 50).is_err(),
        "tampered remote root must not activate"
    );
    let activated = activate_learning_trust(&root, signed, None, 50)
        .expect("real qian-qi root signed distribution activates native verifier");

    let binding = dg("crosshost-test-only-native-ledger-witness");
    let ledger = DurableLedger::create(only_file(directory, "ledger"), binding, 64)
        .expect("create actual original durable ledger");
    let witness = LedgerWitnessStore::create(only_file(directory, "witness"), binding)
        .expect("create physically distinct actual test witness file");
    let directory_handle = File::open(directory).expect("isolated directory handle");
    let mut writer = LedgerWriter::from_durable(
        ledger, witness, activated, &directory_handle, &directory_handle,
    ).expect("original native product writer uses real directory fsync");

    let request = lab_decision();
    let decision_payload = decision_signing_payload_v2(&request).expect("original payload");
    let decision_sig = evidence(&writer, "generator",
        LearningEvidenceRoleV1::Generator, &decision_payload, "decision");
    let mut bad = decision_sig.clone();
    bad.signature[0] ^= 0x11;
    assert!(writer.append_decision(Digest32::ZERO, request.clone(), &bad, 50).is_err());
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence, 0);
    let decision_receipt = writer.append_decision(
        Digest32::ZERO, request, &decision_sig, 50)
        .expect("Mac signed actual original native Decision");
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence, 1);

    let result = lab_outcome(observer);
    let outcome_payload = outcome_signing_payload_v2(&result);
    let wrong_role = evidence(&writer, "generator",
        LearningEvidenceRoleV1::Generator, &outcome_payload, "wrong-outcome");
    assert!(writer.append_outcome(
        decision_receipt.chain_digest, result.clone(), &wrong_role, 50).is_err());
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence, 1);

    let outcome_sig = evidence(&writer, "observer",
        LearningEvidenceRoleV1::Observer, &outcome_payload, "terminal");
    let outcome_receipt = writer.append_outcome(
        decision_receipt.chain_digest, result, &outcome_sig, 50,
    ).expect("X230 signed the native Outcome independently of Mac Generator");
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence, 2);

    let plan = DatasetFreezePlanV2 {
        snapshot_id: id("lab.dataset.snapshot"),
        objective_digest: dg("crosshost.test-only.objective"),
        inclusion_policy_digest: dg("synthetic-all-active-no-performance-claims"),
    };
    let freeze_payload = dataset_freeze_signing_payload_v2(
        &writer.snapshot().unwrap(), &plan).expect("native V3 freeze signing");
    let freeze_sig = evidence(&writer, "evaluator",
        LearningEvidenceRoleV1::Evaluator, &freeze_payload, "dataset.freeze");
    let dataset = writer.freeze_dataset(plan, &freeze_sig, 50)
        .expect("qian-qi test-only Evaluator signed native DatasetSnapshotV3");
    writer.revalidate_dataset_snapshot(&dataset, 50)
        .expect("native dataset sources retained before withdrawal");
    assert_eq!(dataset.snapshot.source_record_digests.len(), 2);

    let unlearning = UnlearningLineageRequestV1 {
        record_id: id("lab.withdrawal.record"),
        lineage_id: id("lab.withdrawal.lineage"),
        source_record_id: id("lab.outcome.record"),
        dataset_snapshot_id: dataset.snapshot.snapshot_id.clone(),
        dataset_digest: dataset.snapshot.dataset_digest,
        artifact_id: id("lab.artifact.fixture"),
        reason_digest: dg("SYNTHETIC-REVOKE-NATIVE-DATASET"),
    };
    let undo_sig = evidence(&writer, "privacy",
        LearningEvidenceRoleV1::UnlearningAuthority,
        &unlearning_signing_payload_v1(&unlearning), "withdrawal");
    let revoked = writer.append_unlearning(
        outcome_receipt.chain_digest, unlearning, &dataset, &undo_sig, 50,
    ).expect("native source withdrawal persists");
    assert_eq!(writer.witness_frontier().unwrap().anchor.sequence, 3);
    assert!(
        writer.revalidate_dataset_snapshot(&dataset, 50).is_err(),
        "native final-use dataset must reject revoked source"
    );
    assert_eq!(writer.records().unwrap().len(), 3);
    println!("RESULT_JSON {}", json!({
        "schema":"heptatrader.crosshost-native-ledger-test.v1",
        "result":"TEST_ONLY_NATIVE_PASSED",
        "real_broker_evidence":false,
        "private_keys_imported_on_pocket4":false,
        "product_root_authority_enrolled":false,
        "root_host":"qian-qi",
        "generator_signing_host":"MacBook-Pro.local",
        "observer_signing_host":"qian-ThinkPad-X230",
        "distinct_generator_observer_controller_ids":true,
        "native_root_signature_corruption_rejected":true,
        "native_generator_evidence_corruption_rejected":true,
        "native_wrong_role_outcome_rejected":true,
        "native_signed_decisions":1,
        "native_signed_terminal_outcomes":1,
        "native_v3_dataset_frozen":true,
        "native_v3_revalidated_before_revocation":true,
        "native_signed_source_withdrawal":true,
        "native_v3_revalidation_rejected_after_withdrawal":true,
        "native_durable_witness_frontier_records":
            writer.witness_frontier().unwrap().anchor.sequence,
        "native_final_chain_digest":format!("{:?}",revoked.append.chain_digest),
        "no_trading_or_model_promotion":true
    }));
    io::stdout().flush().unwrap();
}
