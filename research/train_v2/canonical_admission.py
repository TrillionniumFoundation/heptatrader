"""Canonical LedgerWriter requirements audit from REAL quoted PAPER evidence.

A source quote/Ed25519 research signature is not a native V2 decision,
generator propensity, durable LedgerWriter ACK or Agentd owner approval.
This inspector refuses to mint any of them.
"""
from __future__ import annotations
from .contracts import ContractError, digest, validate_spec, LIVE
from .canonical_native import PIN, FIELDS, WRITER_METHODS, SOURCE

BLOCKERS = (
 "CANONICAL_PRODUCER_CONTROLLER_NOT_ENROLLED",
 "NATIVE_COMPLETE_CANDIDATE_SET_NOT_ATTESTED",
 "TRUE_LOGGED_BEHAVIOR_PROPENSITY_NOT_ATTESTED",
 "ORIGINAL_REQUEST_AND_PREPARED_COMMAND_NOT_BOUND",
 "NATIVE_ROOT_SIGNED_TRUST_DISTRIBUTION_NOT_ACTIVATED",
 "NATIVE_SIGNED_GENERATOR_DECISION_NOT_APPENDED",
 "INDEPENDENT_OBSERVER_CONTROLLER_NOT_ENROLLED",
 "SIGNED_NATIVE_OUTCOME_WITH_CORRECTIONS_NOT_APPENDED",
 "INDEPENDENT_DURABLE_LEDGER_WITNESS_NOT_ACKNOWLEDGED",
 "CANONICAL_DATASET_SNAPSHOT_V3_NOT_FROZEN",
 "CANONICAL_WITHDRAWAL_CURRENT_OWNER_NOT_ADMITTED",
 "UNSEEN_NET_AFTER_COST_PROSPECTIVE_EFFICACY_MISSING",
)
FORBIDDEN = (
 "canonical_agentd_owner_accepted", "native_writer_ack",
 "native_selected_propensity", "native_candidate_completeness",
 "native_observer_ack", "native_dataset_snapshot",
 "native_owner_current", "native_trust_enrolled",
)

def inspect_readiness(layout, spec, source_record, original_segment,
                      decision_at_ms, remote_confidence=None):
 if (not isinstance(layout,dict) or layout.get("schema") !=
      "heptatrader.train-v2-pinned-native-layout.v1"
      or layout.get("exact_native_commit")!=PIN
      or layout.get("layout_id")!=digest({
       k:v for k,v in layout.items() if k!="layout_id"})):
  raise ContractError("CANONICAL_SOURCE_LAYOUT_NOT_VERIFIED")
 if (layout.get("original_rust_fields") !=
        {k:list(v) for k,v in FIELDS.items()}
      or layout.get("writer_methods") != list(WRITER_METHODS)
      or not isinstance(layout.get("native_source_sha256"),dict)
      or set(layout["native_source_sha256"]) != set(SOURCE)
      or layout.get("native_ledger_writer_compiled") is not False
      or layout.get("native_ledger_writer_executed") is not False
      or layout.get("trade_authority") is not False):
  raise ContractError("CANONICAL_NATIVE_LAYOUT_NOT_SAFE_OR_COMPLETE")
 validate_spec(spec)
 if spec.get("source_kind")!=LIVE:
  raise ContractError("CANONICAL_REVISED_HISTORY_NOT_LIVE_SOURCE")
 if not isinstance(source_record,dict):
  raise ContractError("CANONICAL_SOURCE_RECORD_MISSING")
 if any(k in source_record for k in FORBIDDEN):
  raise ContractError("RESEARCH_EVENT_CANNOT_SELF_MINT_NATIVE_AUTHORITY")
 if source_record.get("trade_authority") is True:
  raise ContractError("RESEARCH_EVENT_CANNOT_GRANT_TRADING")
 from .adapters import sealed_live_segment
 if spec.get("quote_expiry_policy") is not None:
  from .causal_ttl import sealed_original_broker_expiry
  records,provenance=sealed_original_broker_expiry(original_segment,spec)
 else:
  records,provenance=sealed_live_segment(original_segment,spec)
 matching=[row for row in records
           if row["source_id"]==source_record.get("source_id")]
 if len(matching)!=1 or source_record!=matching[0]:
  raise ContractError("CANONICAL_SOURCE_NOT_EXACT_ORIGINAL_CHAIN_RECORD")
 from .contracts import compile_example
 item=compile_example(spec,records,decision_at_ms,decision_at_ms)
 if (item.get("label_status")!="PENDING_OUTCOME"
      or item.get("trade_authority") is not False
      or item.get("economic_return_attested") is not False):
  raise ContractError("CANONICAL_NOT_PREOUTCOME_PRICE_INPUT")

 # Original quote/feature semantic parity, not model inference or a broker fill.
 if not any(r["source_id"]==source_record["source_id"]
     for r in item["features"]["input_receipts"]):
  raise ContractError("CANONICAL_DECISION_FEATURE_NOT_SOURCE_BOUND")
 # Deliberately no support for converting remote scalar confidence to Q32:
 # confidence is not a recorded behavior-policy action propensity.
 if remote_confidence is not None:
  from decimal import Decimal,InvalidOperation
  try:
   c=Decimal(str(remote_confidence))
  except (TypeError,ValueError,InvalidOperation) as exc:
   raise ContractError("CANONICAL_INVALID_REMOTE_CONFIDENCE") from exc
  if not c.is_finite() or not 0<=c<=1:
   raise ContractError("CANONICAL_INVALID_REMOTE_CONFIDENCE")
 body={
  "schema":"heptatrader.train-v2-canonical-readiness-audit.v1",
  "native_source_commit":PIN,
  "native_layout_id":layout["layout_id"],
  "source_id":source_record["source_id"],
  "source_sha256":source_record["source_sha256"],
  "feature_id":item["feature_id"],
  "original_source_chain_head":provenance.get(
   "original_chain_head",provenance.get("chain_head")),
  "source_reverified_as_original_local_paper":True,
  "decision_after_source_available":(
   decision_at_ms>=source_record["available_at_ms"]),
  "original_quote_expiry_satisfied":(
   decision_at_ms<=source_record["valid_until_ms"]),
  "label_preoutcome_status":"PENDING_OUTCOME",
  "remote_confidence_present":remote_confidence is not None,
  "remote_confidence_is_logged_behavior_propensity":False,
  "source_timestamp_is_current_fresh_qualification":False,
  "missing_native_requirements":list(BLOCKERS),
  "assessment":"HOLD_MISSING_NATIVE_LEDGER_WRITER_AND_OWNER_FACTS",
  "true_behavior_propensity_established":False,
  "native_decision_appended":False,
  "native_ledger_writer_executed":False,
  "native_witness_acknowledged":False,
  "canonical_dataset_frozen":False,
  "canonical_owner_current_selected":False,
  "automatic_training":False,
  "candidate_selected":False,
  "broker_queries_sent":0,
  "broker_orders_sent":0,
  "trade_authority":False,
 }
 return {**body,"audit_id":digest(body)}
