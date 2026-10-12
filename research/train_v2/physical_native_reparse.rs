//! TEST ONLY: reparse independently archived complete V2 signed bytes using
//! the ORIGINAL canonical SignedLearningEvidenceV1 Rust signing implementation.
//! This is not a product trust distribution, LedgerWriter or recovery grant.
#![forbid(unsafe_code)]
use codex_hepta_learning_ledger::{LearningEvidenceRoleV1, SignedLearningEvidenceV1};
use codex_hepta_types::{Digest32, StableId};
use ed25519_dalek::{Signature, VerifyingKey};
use serde_json::{Value, json};
use std::io::{self, Read};

type ResultT<T> = Result<T, String>;
const DOMAIN: &[u8] = b"hepta.learning-ledger.signed-evidence.v1";
const RECEIPT_DOMAIN: &[u8] = b"heptatrader.train-v2.test-only.physical-fullbyte-escrow.v1\0";
const REQUEST_SCHEMA: &str = "heptatrader.train-v2-complete-signed-native-escrow.v1";
const RECEIPT_SCHEMA: &str = "heptatrader.train-v2-physical-test-fullbytes-receipt.v1";
const INTENT_SCHEMA: &str = "heptatrader.train-v2-original-native-before-append-escrow-intent.v1";

fn field<'a>(v: &'a Value, key: &str) -> ResultT<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 524288)
        .ok_or_else(|| format!("FIELD_{key}"))
}
fn uint(v: &Value, key: &str) -> ResultT<u64> {
    v.get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("UINT_{key}"))
}
fn flag(v: &Value, key: &str, expected: bool) -> ResultT<()> {
    if v.get(key).and_then(Value::as_bool) != Some(expected) {
        return Err(format!("TEST_FLAG_{key}"));
    }
    Ok(())
}
fn bytes(s: &str, max: usize) -> ResultT<Vec<u8>> {
    if s.is_empty() || s.len() & 1 != 0 || s.len() > max * 2 || !s.is_ascii() {
        return Err("HEX_LENGTH".into());
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|_| "HEX_INVALID".to_string()))
        .collect()
}
fn array<const N: usize>(s: &str) -> ResultT<[u8; N]> {
    bytes(s, N)?.try_into().map_err(|_| "HEX_ARRAY_SIZE".into())
}
fn sha(raw: &[u8]) -> String {
    Digest32::of_bytes(raw).to_string()
}
fn sha_field(v: &Value, key: &str, raw: &[u8]) -> ResultT<()> {
    if field(v, key)? != sha(raw) {
        return Err(format!("ORIGINAL_DIGEST_MISMATCH_{key}"));
    }
    Ok(())
}
fn parse_json(raw: &[u8]) -> ResultT<Value> {
    if raw.len() < 3 || raw.last() != Some(&b'\n') {
        return Err("CANONICAL_NEWLINE_MISSING".into());
    }
    let parsed: Value =
        serde_json::from_slice(&raw[..raw.len() - 1]).map_err(|_| "JSON_MALFORMED".to_string())?;
    let mut canonical = serde_json::to_vec(&parsed).map_err(|_| "JSON_SERIALIZE".to_string())?;
    canonical.push(b'\n');
    if canonical != raw {
        return Err("NONCANONICAL_OR_DUPLICATE_JSON".into());
    }
    Ok(parsed)
}
fn read_u64(b: &[u8], offset: &mut usize) -> ResultT<u64> {
    let a: [u8; 8] = b
        .get(*offset..*offset + 8)
        .ok_or("NATIVE_LENGTH")?
        .try_into()
        .map_err(|_| "NATIVE_LENGTH".to_string())?;
    *offset += 8;
    Ok(u64::from_be_bytes(a))
}
fn read_digest(b: &[u8], offset: &mut usize) -> ResultT<Digest32> {
    let a: [u8; 32] = b
        .get(*offset..*offset + 32)
        .ok_or("NATIVE_DIGEST_LENGTH")?
        .try_into()
        .map_err(|_| "NATIVE_DIGEST_LENGTH".to_string())?;
    *offset += 32;
    if a == [0; 32] {
        return Err("ZERO_NATIVE_DIGEST".into());
    }
    Ok(Digest32::from_array(a))
}
fn read_id(b: &[u8], offset: &mut usize) -> ResultT<StableId> {
    let n: usize = read_u64(b, offset)?
        .try_into()
        .map_err(|_| "NATIVE_ID_BOUND")?;
    if n == 0 || n > 128 {
        return Err("NATIVE_ID_BOUND".into());
    }
    let raw = b.get(*offset..*offset + n).ok_or("NATIVE_ID_SHORT")?;
    if raw.iter().any(|v| !(33..=126).contains(v)) {
        return Err("NATIVE_ID_ASCII".into());
    }
    *offset += n;
    let name = std::str::from_utf8(raw).map_err(|_| "NATIVE_ID_UTF8")?;
    StableId::new(name.to_owned()).map_err(|_| "NATIVE_STABLE_ID".into())
}
fn validate(stage: &str, host: &str, raw: &[u8], receipt: &[u8]) -> ResultT<Value> {
    let (role, tag, principal, expected_host, predecessor, sequence) = match stage {
        "decision" => (
            LearningEvidenceRoleV1::Generator,
            0,
            "lab.generator",
            "qian-ThinkPad-X230",
            0,
            1,
        ),
        "outcome" => (
            LearningEvidenceRoleV1::Observer,
            1,
            "lab.observer",
            "MacBook-Pro.local",
            1,
            2,
        ),
        _ => return Err("UNKNOWN_STAGE".into()),
    };
    if host != expected_host {
        return Err("HOST_ROLE_MISMATCH".into());
    }
    let original = parse_json(raw)?;
    if field(&original, "schema")? != REQUEST_SCHEMA {
        return Err("REQUEST_SCHEMA".into());
    }
    flag(&original, "test_only", true)?;
    flag(&original, "trade_authority", false)?;
    let intent = &original["original_intent"];
    if field(intent, "schema")? != INTENT_SCHEMA
        || field(intent, "stage")? != stage
        || field(intent, "native_signer")?
            != (if stage == "decision" {
                "generator"
            } else {
                "observer"
            })
        || field(intent, "source_policy")? != "SHADOW_RESEARCH_PREACTION_TWO_BIT_NO_ORDER_V1"
        || uint(intent, "expected_previous_witness_sequence")? != predecessor
        || uint(intent, "next_ledger_sequence")? != sequence
    {
        return Err("ORIGINAL_INTENT_SEMANTICS".into());
    }
    flag(intent, "test_only", true)?;
    flag(intent, "product_trading_authority", false)?;
    let payload = bytes(field(&original, "signed_payload_hex")?, 65536)?;
    let msg = bytes(field(&original, "signed_evidence_message_hex")?, 65536)?;
    let actor_sig = array::<64>(field(&original, "detached_signature_hex")?)?;
    let actor_public = array::<32>(field(&original, "original_signer_public_hex")?)?;
    sha_field(intent, "original_native_payload_sha256", &payload)?;
    sha_field(intent, "original_native_evidence_message_sha256", &msg)?;
    sha_field(intent, "original_detached_signature_sha256", &actor_sig)?;
    if !payload.starts_with(b"SYNTHETIC-PHASE4Q-NATIVE-PAYLOAD-NO-IB-NO-BROKER-") {
        return Err("ONLY_EXACT_SYNTHETIC_TEST_PAYLOAD".into());
    }
    if !msg.starts_with(DOMAIN) {
        return Err("NATIVE_SIGNATURE_DOMAIN".into());
    }
    let mut offset = DOMAIN.len();
    let evidence_id = read_id(&msg, &mut offset)?;
    let principal_id = read_id(&msg, &mut offset)?;
    let found_tag = *msg.get(offset).ok_or("NATIVE_ROLE_TAG_MISSING")?;
    offset += 1;
    if found_tag != tag || principal_id.as_str() != principal {
        return Err("NATIVE_GENERATOR_OBSERVER_ROLE_MISMATCH".into());
    }
    // The canonical Rust SignedLearningEvidenceV1 order is TRUST, SCOPE,
    // OBJECTIVE. Older Phase4Q test-only bytes used SCOPE, OBJECTIVE, TRUST.
    let trust_digest = read_digest(&msg, &mut offset)?;
    let scope_digest = read_digest(&msg, &mut offset)?;
    let objective_digest = read_digest(&msg, &mut offset)?;
    if trust_digest != Digest32::of_bytes(b"TEST_TRUST")
        || scope_digest != Digest32::of_bytes(b"TEST_SCOPE")
        || objective_digest != Digest32::of_bytes(b"TEST_OBJECTIVE")
    {
        return Err("ORIGINAL_TEST_DIGEST_SEMANTICS_DRIFT".into());
    }
    let authority_epoch = read_u64(&msg, &mut offset)?;
    let issued_at = read_u64(&msg, &mut offset)?;
    let expires_at = read_u64(&msg, &mut offset)?;
    let payload_digest = read_digest(&msg, &mut offset)?;
    if offset != msg.len()
        || authority_epoch != 19
        || issued_at >= expires_at
        || payload_digest != Digest32::of_bytes(&payload)
    {
        return Err("NATIVE_SIGNED_PAYLOAD_OR_TIME".into());
    }
    let rebuilt = SignedLearningEvidenceV1 {
        evidence_id,
        principal_id,
        role,
        trust_digest,
        scope_digest,
        objective_digest,
        authority_epoch,
        issued_at,
        expires_at,
        payload_digest,
        signature: actor_sig,
    };
    if rebuilt.signing_bytes() != msg {
        return Err("REAL_CANONICAL_SIGNING_BYTES_DRIFT".into());
    }
    let verifier = VerifyingKey::from_bytes(&actor_public)
        .map_err(|_| "ACTOR_PUBLIC_KEY_INVALID".to_string())?;
    verifier
        .verify_strict(&rebuilt.signing_bytes(), &Signature::from_bytes(&actor_sig))
        .map_err(|_| "ORIGINAL_ACTOR_SIGNATURE_INVALID".to_string())?;
    let remote = parse_json(receipt)?;
    if field(&remote, "schema")? != RECEIPT_SCHEMA
        || field(&remote, "stage")? != stage
        || field(&remote, "signing_host")? != host
        || field(&remote, "original_request_sha256")? != sha(raw)
        || field(&remote, "native_original_message_sha256")? != sha(&msg)
    {
        return Err("PHYSICAL_RECEIPT_SCOPE_MISMATCH".into());
    }
    flag(&remote, "test_only", true)?;
    flag(&remote, "remote_directory_fsynced_before_reply", true)?;
    flag(&remote, "product_root_enrolled", false)?;
    flag(&remote, "native_ledger_appended", false)?;
    let stored_at = uint(&remote, "stored_at_ms")?;
    let source_at = uint(intent, "source_time_ms")?;
    if stored_at < source_at || stored_at > source_at + 180000 {
        return Err("PHYSICAL_RECEIPT_TIMESTAMP".into());
    }
    let receipt_key = array::<32>(field(&remote, "receipt_public_hex")?)?;
    let receipt_sig = array::<64>(field(&remote, "receipt_signature_hex")?)?;
    let mut signed = Vec::from(RECEIPT_DOMAIN);
    signed.extend_from_slice(sha(raw).as_bytes());
    signed.extend_from_slice(stage.as_bytes());
    signed.extend_from_slice(host.as_bytes());
    signed.extend_from_slice(&stored_at.to_be_bytes());
    VerifyingKey::from_bytes(&receipt_key)
        .map_err(|_| "RECEIPT_KEY_INVALID".to_string())?
        .verify_strict(&signed, &Signature::from_bytes(&receipt_sig))
        .map_err(|_| "PHYSICAL_HOST_RECEIPT_SIGNATURE_INVALID".to_string())?;
    Ok(json!({
        "schema":"heptatrader.train-v2-original-rust-typed-physical-reparse.v1",
        "status":"TEST_ONLY_ORIGINAL_RUST_TYPED_BYTES_AND_TWO_SIGNATURES_VERIFIED",
        "stage":stage,"physical_host":host,
        "original_archive_sha256":sha(raw),"host_receipt_sha256":sha(receipt),
        "typed_payload_sha256":sha(&payload),"original_native_signed_bytes_sha256":sha(&msg),
        "original_signed_evidence_rust_roundtrip_exact":true,
        "native_source_role_and_trust_scope_objective_order_verified":true,
        "original_actor_ed25519_verified":true,
        "separate_physical_host_receipt_ed25519_verified":true,
        "native_original_signature_expired_or_current_auth_not_evaluated":true,
        "native_ledger_writer_append_performed":false,
        "native_witness_acknowledged":false,
        "product_root_trust_enrolled":false,
        "product_actor_or_model_data_present":false,
        "independent_product_recovery_grant_present":false,
        "model_promoted":false,"broker_orders_sent":0,
    }))
}
fn main() {
    let mut data = Vec::new();
    let mut reader = io::stdin().take(262145);
    let outcome = (|| -> ResultT<Value> {
        reader
            .read_to_end(&mut data)
            .map_err(|_| "INPUT_READ".to_string())?;
        if data.len() > 262144 {
            return Err("INPUT_OVERSIZE".into());
        }
        let root: Value = serde_json::from_slice(&data).map_err(|_| "INPUT_JSON".to_string())?;
        if field(&root, "schema")? != "heptatrader.train-v2-original-native-reparse-request.v1" {
            return Err("INPUT_SCHEMA".into());
        }
        let raw = bytes(field(&root, "original_hex")?, 262144)?;
        let receipt = bytes(field(&root, "receipt_hex")?, 262144)?;
        if field(&root, "expected_original_sha256")? != sha(&raw)
            || field(&root, "expected_receipt_sha256")? != sha(&receipt)
        {
            return Err("EXPECTED_PHYSICAL_SOURCE_SHA_MISMATCH".into());
        }
        validate(
            field(&root, "stage")?,
            field(&root, "host")?,
            &raw,
            &receipt,
        )
    })();
    match outcome {
        Ok(v) => println!("{v}"),
        Err(reason) => {
            eprintln!("REJECTED:{reason}");
            std::process::exit(2);
        }
    }
}
