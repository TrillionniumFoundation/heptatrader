"""Single full copied-source scan; compare frozen three-window PRICE oracle.

This is an operator-owned, independent, stdlib-only forensic audit.
No Pocket4 reader, model inference, source append, order or owner selection.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import json
import os
from pathlib import Path
import time

from paged_crosshost_custody import (
    CustodyError, require, canonical, private_file, sha, strict,
)
from paged_crosshost_singlepass import copied_original_multiwindow
from train_v2.independent_frozen_12cell import (
    FROZEN_PREFIX_269, EXPENSE_STRESS_BPS, HORIZONS,
    SOURCE_AUDIT_SHA, SOURCE_CLOSE_SHA, PINNED_STDLIB_CUSTODY_SHA,
    load_pinned, fixed_originals, independent_window_price,
)

OLD_ORACLE_SHA = "294bba1c272a23a0f27afe3dddfa1e0c20f9a6958c47bcdac0a24e362d86e3c7"
OLD_ORACLE_SCHEMA = "heptatrader.train-v2-qian-crosshost-frozen-3x4.v1"
OUTPUT_SCHEMA = "heptatrader.train-v2-independent-singlepass-frozen-3x4.v1"


def compare_exact_frozen_windows(
        multirange, originals, prior, closeout, oracle, evidence_root):
    """No normalized/fuzzy outcome comparisons; only exact original bytes."""
    require(multirange.get("complete_original_source_scans") == 1
            and multirange.get("zero_authorization_effect") is True,
            "SINGLEPASS_SOURCE_NOT_ACTUALLY_BATCHED")
    require(len(originals) == len(multirange.get("windows", [])) == 3,
            "SINGLEPASS_THREE_DECISIONS_REQUIRED")
    require(multirange["numeric_segments"] >= 269
            and multirange["frozen_prefix_sha256"](269) == FROZEN_PREFIX_269,
            "SINGLEPASS_FROZEN_269_PREFIX_CHANGED")
    require(multirange["numeric_segments"] == oracle["current_copied_sealed_segments"]
            and multirange["archive_pages"] == oracle["archive_page_count"]
            and multirange["archived_segments"] == oracle["archived_segments"]
            and multirange["source_fingerprint_sha256"] ==
                oracle["complete_snapshot_fingerprint"]
            and multirange["original_total_unique_quote_receipts"] ==
                oracle["original_verified_quote_ids"]
            and multirange["original_source_bytes"] ==
                oracle["total_complete_original_bytes"]
            and multirange["original_archive_checkpoint_chain_head"] ==
                oracle["archive_checkpoint_chain_head"],
            "SINGLEPASS_FROZEN_COPIED_SOURCE_FRONTIER_CHANGED")
    require(prior["captured_original_decision_slots"] == 3
            and closeout["verified_original_horizon_labels"] == 12
            and oracle["distinct_original_source_decisions"] == 3
            and oracle["correlated_price_horizon_observations"] == 12
            and oracle["actually_independent_broker_trade_samples"] == 0
            and oracle["original_model_predictions"] == 0
            and oracle["actual_slippage_attested"] is False
            and oracle["after_cost_trading_alpha_attested"] is False
            and oracle["broker_orders_sent"] == 0,
            "SINGLEPASS_ORIGINAL_ECONOMICS_OR_DENOMINATOR_CHANGED")
    verified = []
    all_labels = []
    for slot, (src, timer, outcomes) in enumerate(originals):
        begin = src["source_event_at_ms"] - 60000
        end = src["decision_at_ms"] + 60*60000 + 65000
        window = multirange["windows"][slot]
        require((window["from_ms"], window["until_ms"]) == (begin,end),
                "SINGLEPASS_ORIGINAL_PRECOMMITTED_WINDOW_CHANGED")
        labels = independent_window_price(
            {"window_rows": window["window_rows"]},
            src, outcomes, prior["all_slots"][slot]["outcomes"])
        require(len(labels) == 4, "SINGLEPASS_FOUR_LABELS_REQUIRED")
        source_input_sha = sha(private_file(
            evidence_root/"original-files"/f"slot-{slot:02d}.input.json",
            16384))
        entry = {
            "slot": slot,
            "source_input_sha256": source_input_sha,
            "original_source_id": src["original_source_id"],
            "original_event_ms": src["source_event_at_ms"],
            "decision_ms": src["decision_at_ms"],
            "from_ms": begin, "until_ms": end,
            "selected_quote_count": window["original_selected_quote_receipts"],
            "horizons": labels,
        }
        require(entry == oracle["source_windows"][slot],
                "SINGLEPASS_OLD_TRIPLE_SCAN_WINDOW_DIFFERS")
        verified.append(entry)
        all_labels.extend(labels)
    absolute_moves = [abs(Decimal(r["exact_original_mid_return_bps"]))
                      for r in all_labels]
    require(len(absolute_moves)==12
            and len({x["original_source_id"] for x in all_labels})==3
            and max(absolute_moves) ==
                Decimal(oracle["max_absolute_hindsight_midprice_bps"])
            and max(absolute_moves) ==
                Decimal(closeout["largest_abs_hindsight_midprice_move_bps"])
            and all(x < EXPENSE_STRESS_BPS for x in absolute_moves),
            "SINGLEPASS_HINDSIGHT_OR_COST_HURDLE_CHANGED")
    return verified, str(max(absolute_moves))


def verify_singlepass_copy(copy_root, original_root, period, evidence_root):
    evidence_root = Path(evidence_root)
    parser_path = Path(__file__).resolve().parents[1] / "paged_crosshost_custody.py"
    old_path = Path(__file__).with_name("independent_frozen_12cell.py")
    require(sha(parser_path.read_bytes()) == PINNED_STDLIB_CUSTODY_SHA
            and sha(old_path.read_bytes()) ==
                "8fee7a813039978aaa41bd414885f1329083deb1bd0a674ed799ae288859e671",
            "SINGLEPASS_ORIGINAL_INDEPENDENT_ORACLE_CODE_CHANGED")
    oracle_raw = private_file(
        evidence_root/"independent-full-3x4-v1.json",128*1024)
    require(sha(oracle_raw) == OLD_ORACLE_SHA,
            "SINGLEPASS_FROZEN_ORACLE_FILE_CHANGED")
    oracle = strict(oracle_raw)
    require(oracle.get("schema") == OLD_ORACLE_SCHEMA,
            "SINGLEPASS_ORACLE_SCHEMA_CHANGED")
    prior = load_pinned(
        evidence_root/"prior-full-audit.json", SOURCE_AUDIT_SHA,
        "heptatrader.train-v2-original-price-cohort-audit.v2")
    closeout = load_pinned(
        evidence_root/"prior-closeout.json", SOURCE_CLOSE_SHA,
        "heptatrader.train-v2-price-cohort-closeout.v1")
    campaign, roster, originals = fixed_originals(
        evidence_root/"original-files",prior,closeout)
    require(campaign["period_sha256"]==period
            and campaign["source_root"]==original_root
            and oracle["original_source_period_sha256"]==period
            and oracle["original_source_absolute_identity"]==original_root
            and oracle["original_campaign_id"]==campaign["campaign_id"]
            and oracle["original_roster_id"]==roster["roster_id"]
            and Decimal(prior["fixed_frozen_stressed_cost_plus_margin_bps"]) ==
                EXPENSE_STRESS_BPS,
            "SINGLEPASS_SCOPE_IDENTITY_CHANGED")
    windows = [
        (src["source_event_at_ms"]-60000,
         src["decision_at_ms"] + 60*60000 +65000)
        for src,_,_ in originals
    ]
    start = time.monotonic()
    source = copied_original_multiwindow(
        copy_root,original_root,period,windows)
    elapsed = round((time.monotonic()-start)*1000,3)
    checked,max_move = compare_exact_frozen_windows(
        source,originals,prior,closeout,oracle,evidence_root)
    body = {
        "schema": OUTPUT_SCHEMA,
        "state": "EXACT_MATCH_TO_PRESERVED_TRIPLE_SCAN_NO_TRADING_ALPHA",
        "original_oracle_sha256": OLD_ORACLE_SHA,
        "original_source_parser_sha256": PINNED_STDLIB_CUSTODY_SHA,
        "independent_singlepass_source_sha256": sha(
            (Path(__file__).resolve().parents[1] / "paged_crosshost_singlepass.py").read_bytes()),
        "independent_comparison_code_sha256": sha(Path(__file__).read_bytes()),
        "source_snapshot_fingerprint": source["source_fingerprint_sha256"],
        "frozen_original_269_prefix_sha256": FROZEN_PREFIX_269,
        "source_numeric_segments": source["numeric_segments"],
        "archived_pages": source["archive_pages"],
        "original_quote_receipts": source["original_total_unique_quote_receipts"],
        "complete_copied_source_bytes": source["original_source_bytes"],
        "full_source_scans": 1,
        "old_reference_full_source_scans": 3,
        "three_strict_original_decisions": 3,
        "twelve_correlated_price_horizons": 12,
        "three_windows_exactly_match_old_oracle": True,
        "all_twelve_price_results_match_old_original": True,
        "max_absolute_original_mid_move_bps": max_move,
        "frozen_hypothetical_cost_plus_margin_bps": str(EXPENSE_STRESS_BPS),
        "price_results_beating_cost_hurdle": 0,
        "original_price_windows": checked,
        "actual_single_source_scan_wall_ms": elapsed,
        "real_broker_model_orders": 0,
        "actually_independent_trading_rewards": 0,
        "new_forward_model_predictions": 0,
        "economic_alpha_qualified": False,
        "canonical_artifact_owner": False,
        "broker_requests_sent": 0,
        "broker_orders_sent": 0,
        "trade_authority": False,
    }
    return body


def main():
    p=argparse.ArgumentParser()
    for label in ("source-copy","original-root","period","evidence-root","output"):
        p.add_argument("--"+label, required=True)
    args=p.parse_args()
    report=verify_singlepass_copy(
        args.source_copy,args.original_root,args.period,args.evidence_root)
    output=Path(args.output)
    require(not output.is_symlink() and not output.exists(),
            "SINGLEPASS_CREATE_ONLY_RECEIPT_REQUIRED")
    output.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    require(output.parent.stat().st_mode&0o077==0,
            "SINGLEPASS_PRIVATE_OUTPUT_ROOT_REQUIRED")
    raw=canonical(report)+b"\n"
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    directory_fd=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory_fd)
    finally:os.close(directory_fd)
    print(json.dumps({
        "state": report["state"],"single_scan_wall_ms":
        report["actual_single_source_scan_wall_ms"],
        "copied_original_segments":report["source_numeric_segments"],
        "strict_original_decisions":3,"correlated_price_horizons":12,
        "trading_rewards":0,"broker_orders_sent":0,
        "sha256":sha(raw)},sort_keys=True))

if __name__=="__main__":
    main()
