"""Negative and positive analytical paths for PAPER recovery HOLD audit.

All tests use synthetic local snapshots. No IB Gateway, broker orders,
account operations, session changes, process signals or service restarts.
"""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

from train_v2.ib_paper_recovery_gate import (
    SCHEMA, GateHold, assess, count_incidents, parse_systemd,
    safe_file, strict, MIN_STABILITY_MS, SOURCE_RULE, digest, canonical,
)

T=1791892800000
PERIOD="f"*64
def good_exec(n=7):
    return dict(active_state="active",sub_state="running",main_pid_present=True,
                main_pid=701,invocation_id="a"*32,
                active_enter_monotonic_usec=1_000_000,
                restart_count=n,result="success")
def sources(state="READY_FOR_REVIEW_NOT_AUTHORIZATION",usable=24,age=1500):
    prefixes=[{"sha256":"a"*64,"bytes":1024}]
    counts={"USABLE":usable}
    if usable<24: counts["PATH_GAP"]=24-usable
    return dict(schema="heptatrader.quote-readiness-status.v1",
                period_sha256=PERIOD,state=state,at_ms=T-500,
                rule=dict(SOURCE_RULE),
                probes=[{"status":"USABLE"} for _ in range(usable)]+
                       [{"status":"PATH_GAP"} for _ in range(24-usable)],
                counts=counts,source_prefixes=prefixes,
                source_prefix_digest=digest(canonical(prefixes)),
                trading_authority=False,
                latest_source_quote_ms=T-age,latest_quote_age_ms=age,
                usable=usable,unusable=24-usable,automatic_trial_registration=False)
def pnl(*,complete=True,account=False):
    return dict(schema="heptatrader.pnl.v1",complete=complete,account_complete=account,
                observed_at_ms=T-500,totals=dict(net_total_pnl=("-1.00" if complete else None)))
def deps():
    return {s:dict(active_state="active",sub_state="running",main_pid_present=True,
                   restart_count=0) for s in
            ("heptatrader-paper-driver","heptatrader-pnl","heptatrader-learning-source-health")}
def previous(at=T-6*60000,restarts=7):
    return dict(schema=SCHEMA,observed_at_ms=at,
                execution=good_exec(restarts))
def gate(*,exec_=None,health=None,pnl_=None,prior=None,failures=None):
    return assess(T,good_exec() if exec_ is None else exec_,deps(),
                  pnl() if pnl_ is None else pnl_,
                  sources() if health is None else health,
                  PERIOD, previous() if prior is None else prior,failures or {})

class PaperRecoveryTests(unittest.TestCase):
    def test_true_paper_ready_without_product_root_still_no_release(self):
        result=gate(pnl_=pnl(account=True))
        self.assertEqual(result["state"],"PAPER_RESTORATION_AND_PRODUCTION_RELEASE_HOLD")
        self.assertEqual(result["hold_reasons"],
             ["MISSING_NATIVE_OWNER_WITNESS_AND_ACCOUNT_ECONOMICS"])
        self.assertFalse(result["paper_order_submission_authorized_by_this_audit"])
        self.assertFalse(result["model_promotion_allowed"])
        self.assertEqual(result["broker_queries_or_orders_sent_by_auditor"],0)
    def test_no_prior_stability_snapshot_is_hold(self):
        result=assess(T,good_exec(),deps(),pnl(account=True),sources(),
                      PERIOD,None,{})
        self.assertIn("NO_COMPLETED_FIVE_MIN_STABILITY_WITNESS",result["hold_reasons"])
    def test_restart_count_increment_is_hold_even_if_active(self):
        report=gate(exec_=good_exec(8))
        self.assertIn("EXECUTION_RESTARTED_SINCE_PRIOR_SAMPLE",report["hold_reasons"])
    def test_stability_less_than_five_minutes_does_not_pass(self):
        report=gate(prior=previous(at=T-MIN_STABILITY_MS+1))
        self.assertIn("INSUFFICIENT_STABILITY_OBSERVATION",report["hold_reasons"])
    def test_active_but_no_main_pid_not_healthy(self):
        ex=good_exec();ex["main_pid_present"]=False
        self.assertIn("EXECUTION_NOT_RUNNING",gate(exec_=ex)["hold_reasons"])
    def test_health_ready_but_quote_twenty_minutes_old_is_hold(self):
        health=sources(age=20*60*1000)
        self.assertIn("ORIGINAL_IB_QUOTE_UNAVAILABLE_OR_EXPIRED",gate(health=health)["hold_reasons"])
    def test_health_file_stale_even_with_recent_quote_is_hold(self):
        h=sources();h["at_ms"]=T-31000
        self.assertIn("SOURCE_STATUS_NOT_CONTEMPORANEOUS",gate(health=h)["hold_reasons"])
    def test_minimum_usable_health_not_met(self):
        h=sources(state="HOLD_NEW_FUSION_WINDOW",usable=5)
        self.assertIn("SOURCE_READINESS_HOLD",gate(health=h)["hold_reasons"])
    def test_health_rule_downgrade_cannot_pass(self):
        h=sources()
        h["rule"]["minimum_usable"]=1
        self.assertIn("SOURCE_HEALTH_RULE_CHANGED",gate(health=h)["hold_reasons"])
    def test_health_fake_usability_denominator_does_not_pass(self):
        h=sources()
        h["counts"]["USABLE"]=22
        self.assertIn("SOURCE_HEALTH_PROBES_OR_COUNTS_INVALID",gate(health=h)["hold_reasons"])
    def test_health_source_prefix_digest_change_holds(self):
        h=sources()
        h["source_prefixes"][0]["bytes"]=10000
        self.assertIn("SOURCE_HEALTH_PREFIX_CHAIN_UNATTESTED",gate(health=h)["hold_reasons"])
    def test_health_authority_escalation_not_trusted(self):
        h=sources()
        h["trading_authority"]=True
        self.assertIn("SOURCE_HEALTH_UNEXPECTED_AUTHORITY",gate(health=h)["hold_reasons"])
    def test_source_period_mismatch_is_hold(self):
        h=sources();h["period_sha256"]="e"*64
        self.assertIn("SOURCE_PERIOD_ID_MISMATCH",gate(health=h)["hold_reasons"])
    def test_source_missing_never_claims_quote(self):
        report=assess(T,good_exec(),deps(),pnl(),None,PERIOD,previous(),{})
        self.assertIn("MISSING_ORIGINAL_SOURCE_STATUS",report["hold_reasons"])
    def test_pnl_missing_is_hold(self):
        report=assess(T,good_exec(),deps(),None,sources(),PERIOD,previous(),{})
        self.assertIn("MISSING_CURRENT_PNL_SNAPSHOT",report["hold_reasons"])
    def test_pnl_account_and_strategy_are_separate(self):
        result=gate(pnl_=pnl(complete=False,account=False))
        self.assertIn("STRATEGY_PNL_MARK_INCOMPLETE",result["hold_reasons"])
        self.assertIn("ACCOUNT_NAV_NOT_RECONCILED",result["hold_reasons"])
    def test_recent_callback_failure_always_holds_release(self):
        report=gate(failures={"IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE":3})
        self.assertIn("RECENT_EXECUTION_QUOTE_ADMISSION_FAILURES",report["hold_reasons"])
    def test_excluded_other_services_not_called(self):
        self.assertFalse(gate()["driver_or_service_mutated"])
    def test_systemctl_parser_rejects_duplicate_fields(self):
        with self.assertRaisesRegex(GateHold,"DUPLICATED_SERVICE_PROPERTY"):
            parse_systemd("ActiveState=active\nActiveState=failed\nSubState=running\n")
    def test_systemctl_active_only_not_proof(self):
        s=parse_systemd("ActiveState=active\nSubState=running\nNRestarts=336\nMainPID=1\nResult=success\n")
        self.assertEqual(s["restart_count"],336)
        self.assertFalse(gate(exec_=s)["paper_order_submission_authorized_by_this_audit"])
    def test_journal_codes_sanitized_and_counted(self):
        raw="\n".join(json.dumps({"MESSAGE":txt}) for txt in (
            "IB PAPER runtime startup rejected: IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE",
            "IB PAPER runtime startup rejected: IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE",
            "IB PAPER runtime startup rejected: IB_PAPER_ADAPTER_CONNECT_FAILED",
            "IB PAPER readiness timeout account=1 positions=1",
            "IB error 10197 market data competing session"))
        result=count_incidents(raw)
        self.assertEqual(result["IB_PAPER_QUOTE_ADMISSION_CALLBACK_UNSAFE"],2)
        self.assertEqual(result["IB_PAPER_ADAPTER_CONNECT_FAILED"],1)
        self.assertEqual(result["IB_10197_SOURCE_RECORDED_COMPETING_SESSION"],1)
        self.assertNotIn("account",str(result))
    def test_untrusted_journal_is_not_emitted(self):
        j=count_incidents(json.dumps({"MESSAGE":"INJECT secret-account-NAME"})+"\nnot-json\n")
        self.assertEqual(j,{})
    def test_json_duplicate_key_is_rejected(self):
        with self.assertRaisesRegex(GateHold,"DUPLICATE_JSON_KEY"):
            strict(b'{"pnl":1,"pnl":2}')
    def test_json_nonfinite_is_rejected(self):
        with self.assertRaisesRegex(GateHold,"NONFINITE_JSON"):
            strict(b'{"gross":NaN}')
    def test_private_owner_file_required(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"test.json";p.write_text('{"complete":false}');p.chmod(0o644)
            with self.assertRaisesRegex(GateHold,"SOURCE_FILE_NOT_PRIVATE"):
                safe_file(p)
    def test_private_source_digest_exact(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"test.json";p.write_text('{"complete":false}');p.chmod(0o600)
            data,sha=safe_file(p)
            self.assertIs(data["complete"],False)
            self.assertEqual(len(sha),64)

    def test_manual_restart_with_nrestarts_reset_is_caught(self):
        ex=good_exec()
        ex["main_pid"]=999
        ex["restart_count"]=7
        r=gate(exec_=ex)
        self.assertIn("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE",r["hold_reasons"])

    def test_same_pid_but_new_invocation_is_caught(self):
        ex=good_exec()
        ex["invocation_id"]="b"*32
        self.assertIn("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE",
                      gate(exec_=ex)["hold_reasons"])

    def test_same_pid_invocation_but_new_start_time_is_caught(self):
        ex=good_exec()
        ex["active_enter_monotonic_usec"]=1_500_000
        self.assertIn("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE",
                      gate(exec_=ex)["hold_reasons"])

    def test_missing_prior_invocation_never_passes_identity(self):
        r=previous()
        del r["execution"]["invocation_id"]
        self.assertIn("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE",
                      gate(prior=r)["hold_reasons"])

    def test_missing_current_invocation_never_passes_identity(self):
        ex=good_exec()
        ex["invocation_id"]=None
        reasons=gate(exec_=ex)["hold_reasons"]
        self.assertIn("EXECUTION_PROCESS_IDENTITY_NOT_ATTESTED",reasons)
        self.assertIn("EXECUTION_PROCESS_CHANGED_SINCE_PRIOR_SAMPLE",reasons)

    def test_real_systemd_identity_fields_parsed(self):
        text=("ActiveState=active\nSubState=running\nMainPID=701\n"
              "NRestarts=0\nInvocationID="+"c"*32+
              "\nActiveEnterTimestampMonotonic=1987259672\nResult=success\n")
        v=parse_systemd(text)
        self.assertEqual(v["main_pid"],701)
        self.assertEqual(v["invocation_id"],"c"*32)
        self.assertEqual(v["active_enter_monotonic_usec"],1987259672)

if __name__=="__main__":unittest.main()
