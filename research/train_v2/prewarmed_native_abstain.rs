//! Prewarmed ORIGINAL Canonical LedgerWriter with a real fresh PAPER source.
//! Test-only deterministic NO_TRADE. Product Root/Owner is NEVER enrolled.
//! Pinned canonical native crates are linked UNMODIFIED through isolated Cargo.
#![forbid(unsafe_code)]
use codex_hepta_learning_ledger::*;
use codex_hepta_types::{Digest32, ProbabilityQ32, StableId};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::{self, BufRead, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

const SCOPE: &str = "LAB_TRAIN_V2_PREWARM_NO_MODEL_NO_ORDER";
const OBJECTIVE: &str = "LAB_ORIGINAL_FX_SOURCE_ONLY_NO_TRADE_NO_ECONOMIC_REWARD";
const POLICY: &str = "LAB_FIXED_ABSTAIN_ONLY_Q32_ONE_V1";
const DELAY_MS: u64 = 3_500;
fn ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64
}
fn id(value: &str) -> StableId {
    StableId::new(value.to_owned()).unwrap()
}
fn dg(value: &str) -> Digest32 {
    Digest32::of_bytes(value.as_bytes())
}
fn hexd(raw: &[u8]) -> String {
    raw.iter().map(|b| format!("{b:02x}")).collect()
}
fn unhex<const N: usize>(s: &str) -> [u8; N] {
    assert_eq!(s.len(), N * 2, "exact signed length");
    let bytes: Vec<u8> = (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect();
    bytes.try_into().ok().unwrap()
}
fn private_file(dir: &Path, name: &str) -> File {
    let f = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(dir.join(name))
        .unwrap();
    assert_eq!(f.metadata().unwrap().permissions().mode() & 0o777, 0o600);
    f
}
fn write_once(dir: &Path, name: &str, v: &Value) {
    let mut f = private_file(dir, name);
    let raw = serde_json::to_vec(v).unwrap();
    f.write_all(&raw).unwrap();
    f.write_all(b"\n").unwrap();
    f.sync_all().unwrap();
    File::open(dir).unwrap().sync_all().unwrap();
}
fn verify_source(s: &Value, now: u64) -> (u64, u64, String, String) {
    assert_eq!(
        s["schema"],
        "heptatrader.train-v2-prewarmed-native-abstain-input.v1"
    );
    let allowed = [
        "schema",
        "test_only",
        "no_model",
        "no_orders",
        "original_source_independently_verified",
        "original_signed_source_fsynced",
        "production_root_enrolled",
        "model_prediction_emitted",
        "actual_broker_prepared",
        "economic_return_attested",
        "source_event_id",
        "original_quote_receipt",
        "first_source_cohort_id",
        "independent_verifier_report_id",
        "original_signed_source_proof_sha256",
        "original_source_prefix_sha256",
        "registered_at_ms",
        "original_quote_event_ms",
        "original_quote_available_ms",
        "original_quote_expiry_ms",
        "original_signed_source_durable_at_ms",
        "original_source_decision_cutoff_ms",
        "fixed_native_delay_ms",
        "candidate_universe",
        "actual_selected_action",
        "selected_logged_behavior_q32",
        "trading_role",
    ];
    let fields = s.as_object().expect("native input must be object");
    assert_eq!(
        fields.len(),
        allowed.len(),
        "no undeclared native authority fields"
    );
    assert!(
        allowed.iter().all(|name| fields.contains_key(*name)),
        "exact schema keys"
    );
    assert_eq!(s["fixed_native_delay_ms"], DELAY_MS);
    assert_eq!(s["actual_selected_action"], "NO_TRADE");
    assert_eq!(s["trading_role"], "NONE");
    assert_eq!(s["candidate_universe"], json!(["NO_TRADE"]));
    assert_eq!(s["selected_logged_behavior_q32"], 4_294_967_296_u64);
    for k in [
        "test_only",
        "no_model",
        "no_orders",
        "original_source_independently_verified",
        "original_signed_source_fsynced",
    ] {
        assert_eq!(s[k], true, "{k}");
    }
    for k in [
        "production_root_enrolled",
        "model_prediction_emitted",
        "actual_broker_prepared",
        "economic_return_attested",
    ] {
        assert_eq!(s[k], false, "{k}");
    }
    let available = s["original_quote_available_ms"].as_u64().unwrap();
    let occurred = s["original_quote_event_ms"].as_u64().unwrap();
    let expire = s["original_quote_expiry_ms"].as_u64().unwrap();
    let source_seal = s["original_signed_source_durable_at_ms"].as_u64().unwrap();
    let registration = s["registered_at_ms"].as_u64().unwrap();
    let source_cutoff = s["original_source_decision_cutoff_ms"].as_u64().unwrap();
    let decision = available.checked_add(DELAY_MS).unwrap();
    assert!(registration + 30_000 < available, "pre-registered source");
    assert!(occurred <= available && available <= source_seal);
    assert!(source_seal <= source_cutoff && source_cutoff <= expire);
    assert!(
        source_cutoff < decision && decision <= expire,
        "new native decision cutoff valid"
    );
    assert!(
        available <= now && now < decision && now < expire,
        "native input still prospective"
    );
    let event = s["source_event_id"].as_str().unwrap();
    let receipt = s["original_quote_receipt"].as_str().unwrap();
    let cohort = s["first_source_cohort_id"].as_str().unwrap();
    let digest = s["independent_verifier_report_id"].as_str().unwrap();
    let prefix = s["original_source_prefix_sha256"].as_str().unwrap();
    let signed_receipt = s["original_signed_source_proof_sha256"].as_str().unwrap();
    for val in [event, receipt, cohort, digest, prefix, signed_receipt] {
        assert_eq!(val.len(), 64);
        assert!(val.bytes().all(|c| c.is_ascii_hexdigit()));
    }
    (decision, expire, event.to_string(), receipt.to_string())
}
fn activate(mac: [u8; 32], started: u64) -> ActivatedLearningTrustV1 {
    // Per-process TEST-only private root key exists only in this process memory.
    let mut seed = [0u8; 32];
    File::open("/dev/urandom")
        .unwrap()
        .read_exact(&mut seed)
        .unwrap();
    let local_test_root = SigningKey::from_bytes(&seed);
    seed.fill(0);
    let root = LearningTrustRootV1 {
        root_id: id("lab.prewarm.only.root"),
        scope_digest: dg(SCOPE),
        verifying_key: local_test_root.verifying_key().to_bytes(),
        valid_from: started - 10_000,
        expires_at: started + 600_000,
        revoked_at: None,
    };
    let signer = TrustedLearningSignerV1 {
        principal: AuthenticatedPrincipalV1 {
            principal_id: id("lab.mac.only.generator"),
            credential_chain_digest: dg("LAB_MAC_IN_MEMORY_TEST_ONLY"),
            signing_key_digest: Digest32::of_bytes(&mac),
            scope_digest: dg(SCOPE),
            authority_epoch: 1,
            authenticated_at: started - 10_000,
            expires_at: started + 600_000,
        },
        controller_id: id("lab.mac.test.controller.not-product"),
        verifying_key: mac,
        roles: vec![LearningEvidenceRoleV1::Generator],
        revoked_at: None,
    };
    let mut distribution = SignedLearningTrustDistributionV1 {
        distribution: LearningTrustDistributionV1 {
            distribution_id: id("lab.prewarm.test.distribution"),
            generation: 1,
            effective_at: started - 1_000,
            trust: LearningEvidenceTrustV1 {
                scope_digest: dg(SCOPE),
                objective_digest: dg(OBJECTIVE),
                authority_epoch: 1,
                signers: vec![signer],
            },
        },
        root_id: root.root_id.clone(),
        issued_at: started - 1_000,
        expires_at: started + 580_000,
        signature: [0; 64],
    };
    distribution.signature = local_test_root
        .sign(&distribution.signing_bytes().unwrap())
        .to_bytes();
    activate_learning_trust(&root, distribution, None, ms()).unwrap()
}
fn signature_return_within_pocket4_cutoff(
    local_request_ms: u64,
    local_response_ms: u64,
    decision_deadline_ms: u64,
    original_quote_expiry_ms: u64,
) -> bool {
    local_request_ms <= local_response_ms
        && local_response_ms < decision_deadline_ms
        && local_response_ms < original_quote_expiry_ms
}

fn prewarmed_writer(dir: &Path, pubkey: [u8; 32]) -> LedgerWriter {
    let st = dir.metadata().unwrap();
    assert!(st.is_dir() && !dir.is_symlink() && st.permissions().mode() & 0o777 == 0o700);
    let started = ms();
    let trust = activate(pubkey, started);
    let binding = dg("LAB_PREWARM_TEST_ONLY_LEDGER");
    let ledger = DurableLedger::create(private_file(dir, "ledger"), binding, 64).unwrap();
    let witness = LedgerWitnessStore::create(private_file(dir, "witness"), binding).unwrap();
    let d = File::open(dir).unwrap();
    LedgerWriter::from_durable(ledger, witness, trust, &d, &d).unwrap()
}
fn run(dir: &Path, mac_pub_hex: &str) {
    let pubkey = unhex::<32>(mac_pub_hex);
    let mut writer = prewarmed_writer(dir, pubkey);
    let original_precommit_at = ms();
    write_once(
        dir,
        "native-prewarm-intent.json",
        &json!({
            "schema":"heptatrader.train-v2-native-precommitted-abstain-only.v1",
            "precommitted_at_ms":original_precommit_at,
            "fixed_decision_delay_ms":DELAY_MS,
            "fixed_candidate_set":["NO_TRADE"],
            "selected_q32_propensity":4_294_967_296_u64,
            "generator_mac_public_key":mac_pub_hex,
            "test_only_root":"PROCESS_MEMORY_NOT_CANONICAL",
            "model_inference":false,
            "broker_order_authority":false,
            "native_witness_frontier":0
        }),
    );
    println!(
        "NATIVE_READY {}",
        json!({
            "schema": "heptatrader.train-v2-native-abstain-prewarm-ready.v1",
            "prewarmed_at_ms": ms(),
            "native_writer_initial_frontier": writer.witness_frontier().unwrap().anchor.sequence,
            "native_root": "IN_MEMORY_TEST_ONLY_NOT_PRODUCT",
            "remote_generator": "MAC_RAM_KEY_TEST_ONLY",
            "fixed_policy": POLICY,
            "fixed_decision_delay_ms": DELAY_MS,
            "broker_orders_sent": 0
        })
    );
    io::stdout().flush().unwrap();
    let mut line = String::new();
    assert!(io::stdin().lock().read_line(&mut line).unwrap() <= 16_384);
    let source: Value = serde_json::from_str(&line).unwrap();
    let input_at = ms();
    let (deadline, expire, event_id, receipt) = verify_source(&source, input_at);
    let c = vec![id("abstain")];
    let request = ProductionDecisionV2 {
        record_id: id(&format!("lab.prewarm.decision.{}", &event_id[..24])),
        episode_id: id(&format!("lab.prewarm.episode.{}", &event_id[..24])),
        run_snapshot_digest: Digest32::of_bytes(line.as_bytes()),
        objective_digest: dg(OBJECTIVE),
        policy_digest: dg(POLICY),
        candidate_ids: c.clone(),
        selected_candidate_id: id("abstain"),
        selected_propensity: ProbabilityQ32::ONE,
        completeness: CandidateSetCompletenessReceiptV1 {
            set_id: id("lab.prewarm.complete.candidates"),
            state_digest: dg(&receipt),
            generator_id: id("lab.mac.only.generator"),
            generator_code_digest: dg("LAB_PREDECLARED_FIXED_ABSTAIN_ONLY_CODE_V1"),
            grammar_digest: dg("ONE_CANDIDATE_NO_TRADE_ONLY"),
            hard_filter_digest: dg("NO_TRADING_PERMISSIONS"),
            truncation_digest: dg("COMPLETE_ONE_OF_ONE"),
            candidates_digest: candidate_ids_digest_v2(&c),
            candidate_count: 1,
            omitted_count_bound: 0,
            canonical_order_digest: candidate_order_digest_v2(&c),
            complete_for_generator: true,
        },
        support_digest: dg(&event_id),
    };
    let payload = decision_signing_payload_v2(&request).unwrap();
    let issued_at = ms();
    assert!(
        issued_at < deadline && issued_at < expire,
        "native proof not late"
    );
    let mut witness = SignedLearningEvidenceV1 {
        evidence_id: id(&format!("lab.prewarm.signature.{}", &event_id[..24])),
        principal_id: id("lab.mac.only.generator"),
        role: LearningEvidenceRoleV1::Generator,
        trust_digest: writer.verifier().trust_digest(),
        scope_digest: dg(SCOPE),
        objective_digest: dg(OBJECTIVE),
        authority_epoch: 1,
        issued_at,
        expires_at: issued_at + 60_000,
        payload_digest: Digest32::of_bytes(&payload),
        signature: [0; 64],
    };
    let signing = witness.signing_bytes();
    println!(
        "NATIVE_SIGN_REQUEST {}",
        json!({
            "message_hex": hexd(&signing),
            "issued_at_ms": issued_at,
            "quote_expiry_ms": expire,
            "decision_deadline_ms": deadline,
            "test_only": true
        })
    );
    io::stdout().flush().unwrap();
    line.clear();
    assert!(io::stdin().lock().read_line(&mut line).unwrap() <= 512);
    let signed: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(signed["kind"], "SIGNED");
    let actual_mac_signed = signed["signed_at_ms"].as_u64().unwrap();
    // The MAC and Pocket4 are distinct physical clocks, not synchronized
    // independent monotonic authorities. The returned Mac time is diagnostic.
    // The locally timed request->Ed25519 reply->Ledger/Witness ACK bounds
    // are the authoritative cutoff for this TEST-ONLY native source.
    let pocket4_received_signature_at_ms = ms();
    assert!(
        signature_return_within_pocket4_cutoff(
            issued_at,
            pocket4_received_signature_at_ms,
            deadline,
            expire
        ),
        "native signature response after Pocket4 original cutoff"
    );
    witness.signature = unhex::<64>(signed["signature_hex"].as_str().unwrap());
    let ack = writer
        .append_decision(Digest32::ZERO, request, &witness, ms())
        .unwrap();
    let native_ack_ms = ms(); // Ledger AND independent witness have fsynced.
    let frontier = writer.witness_frontier().unwrap();
    assert_eq!(frontier.anchor.sequence, 1);
    assert!(
        native_ack_ms <= deadline && native_ack_ms <= expire,
        "native witness ack after original cutoff"
    );
    write_once(
        dir,
        "native-prewarm-public-ack.json",
        &json!({
            "schema": "heptatrader.train-v2-original-fx-native-witness-before-quote-expiry.test.v1",
            "source_event_id": event_id,
            "original_quote_receipt": receipt,
            "first_source_cohort_id": source["first_source_cohort_id"],
            "original_signed_source_receipt_sha256": source["original_signed_source_proof_sha256"],
            "original_independent_verifier_report_id": source["independent_verifier_report_id"],
            "source_available_at_ms": source["original_quote_available_ms"],
            "quote_expiry_ms": expire,
            "native_precommitted_deadline_ms": deadline,
            "native_issuer_request_at_ms": issued_at,
             "mac_reported_signed_wallclock_ms_untrusted": actual_mac_signed,
            "pocket4_received_signature_at_ms": pocket4_received_signature_at_ms,
            "cross_host_wallclock_order_used_for_admission": false,
            "native_witness_ack_at_ms": native_ack_ms,
            "native_witness_sequence": 1,
            "native_chain_digest": ack.chain_digest.to_string(),
            "mac_generator_pubkey_sha256": Digest32::of_bytes(&pubkey).to_string(),
            "original_canonical_writer_executed": true,
            "root_and_role_test_only": true,
            "original_opaque_llm_propensity_used": false,
            "no_model_inference": true,
            "selected_action": "NO_TRADE",
            "selected_logged_q32_propensity": 4294967296_u64,
            "candidate_universe": ["NO_TRADE"],
            "economic_reward_observed": false,
            "broker_orders_sent": 0,
            "production_authority": false
        }),
    );
    println!(
        "NATIVE_RESULT {}",
        json!({
            "status": "TEST_ONLY_REAL_SOURCE_NATIVE_ACK_BEFORE_ORIGINAL_EXPIRY",
            "native_ack_at_ms": native_ack_ms,
            "native_deadline_ms": deadline,
            "original_expiry_ms": expire,
            "witness_seq": 1,
            "orders_sent": 0,
            "product_authority": false
        })
    );
    io::stdout().flush().unwrap();
}
fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(
        args.len(),
        3,
        "usage PREWARM_PRIVATE_DIRECTORY MAC_TEST_PUBLIC_KEY_HEX"
    );
    run(Path::new(&args[1]), &args[2]);
}

#[cfg(test)]
mod native_abstain_contract_tests {
    use super::*;
    use std::panic::{AssertUnwindSafe, catch_unwind};

    fn original_test_source(available: u64) -> Value {
        json!({
            "schema": "heptatrader.train-v2-prewarmed-native-abstain-input.v1",
            "test_only": true,
            "no_model": true,
            "no_orders": true,
            "original_source_independently_verified": true,
            "original_signed_source_fsynced": true,
            "production_root_enrolled": false,
            "model_prediction_emitted": false,
            "actual_broker_prepared": false,
            "economic_return_attested": false,
            "source_event_id": "a".repeat(64),
            "original_quote_receipt": "b".repeat(64),
            "first_source_cohort_id": "c".repeat(64),
            "independent_verifier_report_id": "d".repeat(64),
            "original_signed_source_proof_sha256": "e".repeat(64),
            "original_source_prefix_sha256": "f".repeat(64),
            "registered_at_ms": available - 40_000,
            "original_quote_event_ms": available - 400,
            "original_quote_available_ms": available,
            "original_quote_expiry_ms": available + 4_300,
            "original_signed_source_durable_at_ms": available + 200,
            "original_source_decision_cutoff_ms": available + 650,
            "fixed_native_delay_ms": 3_500,
            "candidate_universe": ["NO_TRADE"],
            "actual_selected_action": "NO_TRADE",
            "selected_logged_behavior_q32": 4_294_967_296_u64,
            "trading_role": "NONE"
        })
    }

    #[test]
    fn predeclared_no_trade_valid_in_lab_only() {
        let at = 1_800_000_000_000_u64;
        let input = original_test_source(at);
        let (deadline, expiry, event, receipt) = verify_source(&input, at + 300);
        assert_eq!(deadline, at + 3_500);
        assert_eq!(expiry, at + 4_300);
        assert_eq!(event, "a".repeat(64));
        assert_eq!(receipt, "b".repeat(64));
    }

    #[test]
    fn physical_mac_wallclock_skew_does_not_override_native_deadline() {
        let request = 1_900_000_000_000_u64;
        let response = request + 110;
        let cutoff = request + 700;
        let original_expiry = request + 1500;
        let mac_reported_wallclock = request - 620;
        assert!(mac_reported_wallclock < request);
        assert!(signature_return_within_pocket4_cutoff(
            request,
            response,
            cutoff,
            original_expiry
        ));
        assert!(!signature_return_within_pocket4_cutoff(
            request,
            cutoff,
            cutoff,
            original_expiry
        ));
        assert!(!signature_return_within_pocket4_cutoff(
            request,
            original_expiry,
            cutoff + 3000,
            original_expiry
        ));
        assert!(!signature_return_within_pocket4_cutoff(
            request,
            request - 1,
            cutoff,
            original_expiry
        ));
    }

    #[test]
    fn all_native_authority_or_clock_substitutions_fail_closed() {
        let at = 1_800_000_000_000_u64;
        let base = original_test_source(at);
        let bad: Vec<(&str, Value)> = vec![
            (
                "fake-original-prepared",
                json!({"actual_broker_prepared":true}),
            ),
            (
                "fake-product-root",
                json!({"production_root_enrolled":true}),
            ),
            ("fake-model", json!({"model_prediction_emitted":true})),
            (
                "fake-economic-efficacy",
                json!({"economic_return_attested":true}),
            ),
            ("fake-trading-role", json!({"trading_role":"BUY"})),
            (
                "fake-selected-trade",
                json!({"actual_selected_action":"BUY"}),
            ),
            (
                "fake-universe",
                json!({"candidate_universe":["NO_TRADE","BUY"]}),
            ),
            (
                "fake-propensity",
                json!({"selected_logged_behavior_q32":4294967295_u64}),
            ),
            (
                "missing-original-source",
                json!({"original_source_independently_verified":false}),
            ),
            (
                "late-original-seal",
                json!({"original_signed_source_durable_at_ms":at+900}),
            ),
            (
                "expired-original-quote",
                json!({"original_quote_expiry_ms":at+1000}),
            ),
            ("not-predeclared", json!({"registered_at_ms":at-20_000})),
            (
                "invalid-sha",
                json!({"original_quote_receipt":"g".repeat(64)}),
            ),
            ("unexpected-field", json!({"untrusted_product_ack":true})),
        ];
        for (label, patch) in bad {
            let mut input = base.clone();
            for (key, value) in patch.as_object().expect("test-only change") {
                input[key] = value.clone();
            }
            let invalid = catch_unwind(AssertUnwindSafe(|| verify_source(&input, at + 300)));
            assert!(invalid.is_err(), "accepted forged lab fact: {label}");
        }
        for (label, now) in [
            ("too-early", at - 1),
            ("after-decision", at + 3_500),
            ("after-quote-expiry", at + 4_301),
        ] {
            let invalid = catch_unwind(AssertUnwindSafe(|| verify_source(&base, now)));
            assert!(invalid.is_err(), "accepted {label}");
        }
    }
}

#[cfg(test)]
mod original_frozen_packet_analysis {
    use super::*;
    #[test]
    #[ignore = "requires operator-supplied frozen, prior TEST-ONLY original input"]
    fn historical_packet_shape_at_original_receipt_instant() {
        let path = std::env::var("HEPTA_FROZEN_PAST_PACKET")
            .expect("private immutable historical TEST-ONLY packet location");
        let raw = std::fs::read(path).expect("read private original packet");
        let packet: Value = serde_json::from_slice(&raw).expect("original JSON");
        let available = packet["original_quote_available_ms"]
            .as_u64()
            .expect("original recorded availability");
        let (_, _, event, _) = verify_source(&packet, available + 350);
        assert_eq!(event.len(), 64);
    }
}

#[cfg(test)]
mod independently_recovered_native_ack {
    use super::*;

    fn test_original_binding() -> Digest32 {
        Digest32::of_bytes(b"LAB_PREWARM_TEST_ONLY_LEDGER")
    }

    fn read_copy(dir: &Path, name: &str) -> File {
        OpenOptions::new()
            .read(true)
            .write(true)
            .open(dir.join(name))
            .expect("only private copied native files")
    }

    #[test]
    #[ignore = "requires explicitly supplied prior signed native-ledger/witness copies"]
    fn original_typed_decision_reopens_against_separately_durable_witness() {
        let root = std::env::var("HEPTA_NATIVE_PREVIOUS_ACK_COPY")
            .expect("explicit private copied prior native files");
        let root = Path::new(&root);
        let original_ack: Value = serde_json::from_reader(
            File::open(root.join("native-prewarm-public-ack.json"))
                .expect("read unchanged copied ack"),
        )
        .expect("test-only native ack JSON");
        assert_eq!(original_ack["production_authority"], false);
        assert_eq!(original_ack["root_and_role_test_only"], true);
        assert_eq!(original_ack["native_witness_sequence"], 1);
        assert_eq!(original_ack["selected_action"], "NO_TRADE");
        let witness =
            LedgerWitnessStore::recover(read_copy(root, "witness"), test_original_binding())
                .expect("original independent copied witness");
        let frontier = witness.frontier().expect("recovered witness frontier");
        assert_eq!(frontier.anchor.sequence, 1);
        assert_eq!(
            frontier.anchor.chain_digest.to_string(),
            original_ack["native_chain_digest"].as_str().unwrap()
        );
        let ledger = DurableLedger::recover(
            read_copy(root, "ledger"),
            test_original_binding(),
            64,
            LedgerRecovery::Acknowledged(frontier.anchor),
        )
        .expect("reopen original copied native ledger with exact anchor");
        let records = ledger.records().expect("verified native records");
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].sequence.get(), 1);
        assert_eq!(records[0].chain_digest, frontier.anchor.chain_digest);
        match &records[0].event {
            LedgerEvent::AuthenticatedDecisionV2(decision) => {
                assert_eq!(decision.selected_candidate_id, id("abstain"));
                assert_eq!(decision.candidate_ids, vec![id("abstain")]);
                assert_eq!(decision.selected_propensity, ProbabilityQ32::ONE);
                assert_eq!(decision.generator_id, id("lab.mac.only.generator"));
                assert_eq!(
                    decision.support_digest,
                    dg(original_ack["source_event_id"].as_str().unwrap())
                );
                assert_ne!(decision.authentication_digest, Digest32::ZERO);
            }
            _ => panic!("copied typed event is not original V2 authenticated decision"),
        }
        println!("NATIVE_TYPED_TEST_ONLY_ABSTAIN_WITNESS_REOPENED");
        println!(
            "NATIVE_WRITER_CHAIN_DIGEST {}",
            frontier.anchor.chain_digest
        );
    }

    #[test]
    #[ignore = "requires separately created corrupted witness fixture"]
    fn modified_witness_frame_never_recovers() {
        let file =
            std::env::var("HEPTA_NATIVE_CORRUPTED_WITNESS").expect("private corrupt copy fixture");
        let attempt = LedgerWitnessStore::recover(
            OpenOptions::new()
                .read(true)
                .write(true)
                .open(file)
                .expect("private copied corrupted witness"),
            test_original_binding(),
        );
        assert!(
            attempt.is_err(),
            "corrupted independent witness was accepted"
        );
    }

    #[test]
    #[ignore = "requires separately created ledger-header-only test copy"]
    fn acknowledged_native_record_cannot_be_silently_lost() {
        let root = std::env::var("HEPTA_NATIVE_LOST_LEDGER_COPY")
            .expect("private copied missing-history fixture");
        let root = Path::new(&root);
        let witness =
            LedgerWitnessStore::recover(read_copy(root, "witness"), test_original_binding())
                .expect("original acknowledged witness");
        let frontier = witness.frontier().unwrap();
        assert_eq!(frontier.anchor.sequence, 1);
        let missing = DurableLedger::recover(
            read_copy(root, "ledger"),
            test_original_binding(),
            64,
            LedgerRecovery::Acknowledged(frontier.anchor),
        );
        assert!(
            missing.is_err(),
            "missing acknowledged history was silently recreated"
        );
    }
}
