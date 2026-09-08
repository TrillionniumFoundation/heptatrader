# Legacy QMT CSV bridge concept

Status: LEGACY  
Applies to: historical signal-import experiments

The historical concept converted a QMT-generated CSV basket into OMS-like JSONL records. It did not execute through the canonical Execution Service and could label an imported record `place_sent` without a broker send. That terminology is unsafe and is not part of the current runtime.

A future import path, if needed, must emit an untrusted `strategy_intent_imported` artifact rather than an execution event. The Execution Service must then validate schema, stable import identity, deduplication, session/capability, authoritative quote/state, risk, expiry, and command identity before producing its own durable mutation records.

Local absolute script/data paths and the old consumer recommendation are removed. No file watcher or JSONL bridge may become an alternative order authority.
