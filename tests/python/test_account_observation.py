"""Account observation success is independent of broader return qualification."""
from __future__ import annotations
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
import hepta_account_pnl as account
import hepta_nav_assurance as assurance
import hepta_pnl_report as report
from test_account_cohort_lifecycle import v3_snapshot
from test_account_pnl import period


class AccountObservationTests(unittest.TestCase):
    def observation(self, at_offset=0):
        raw = v3_snapshot()
        nav = account.normalize_nav(raw, period())
        facts = assurance.assess_nav_assurance(
            nav, raw, period(), raw["observed_at_ms"] + at_offset)
        result = assurance.enforce_strict_account_report(
            {"reason_codes": [], "complete": True, "nav": "forged",
             "broker_reported_account_net_pnl": "forged",
             "all_in_account_net_pnl": "forged"}, facts, nav)
        return result, nav

    def invoke(self, value, args=(), error=None):
        stream = io.StringIO()
        previous_umask = os.umask(0o077)
        try:
            with patch.object(report, "NativeReader"), \
                 patch.object(report, "cached", return_value=value), \
                 patch.object(report, "attach_account", return_value=value, side_effect=error), \
                 contextlib.redirect_stdout(stream):
                code = report.main(["--cached", "--format", "json", *args])
        finally:
            os.umask(previous_umask)
        return code, json.loads(stream.getvalue())

    def test_valid_observation_does_not_claim_strict_nav_or_returns(self):
        value, nav = self.observation()
        self.assertTrue(value["observation_complete"])
        self.assertEqual(value["observed_nav"], nav["nav"])
        self.assertEqual(value["observation_observed_at_ms"],
                         value["nav_assurance"]["observed_at_ms"])
        for field in ("complete", "nav_complete", "strict_nav_eligible", "all_in_complete"):
            self.assertFalse(value[field])
        for field in ("nav", "broker_reported_account_net_pnl", "all_in_account_net_pnl"):
            self.assertIsNone(value[field])
        self.assertFalse(value["nav_assurance"]["mutation_authorized"])

    def test_stale_observation_cannot_succeed_or_display_old_nav(self):
        value, _ = self.observation(30000)
        self.assertFalse(value["observation_complete"])
        self.assertIsNone(value["observed_nav"])
        self.assertIsNone(value["observation_observed_at_ms"])
        code, body = self.invoke({"complete": True, "account": value}, ["--account"])
        self.assertEqual(code, 2)
        self.assertFalse(body["observation_complete"])
        self.assertIn("Account NAV observation=UNKNOWN", report.render_account(body))

    def test_account_exit_code_uses_valid_observation_despite_unknown_returns(self):
        value, nav = self.observation()
        code, body = self.invoke({"complete": False, "account": value}, ["--account"])
        self.assertEqual(code, 0)
        self.assertEqual(body["observed_nav"], nav["nav"])
        self.assertIsNone(body["all_in_account_net_pnl"])
        self.assertIn("Funding-adjusted account return=UNKNOWN", report.render_account(body))

    def test_strategy_exit_code_still_uses_execution_accounting(self):
        value, _ = self.observation()
        for complete, expected in ((True, 0), (False, 2)):
            with self.subTest(complete=complete):
                code, _ = self.invoke({"complete": complete, "account": value})
                self.assertEqual(code, expected)

    def test_failed_attachment_clears_previous_observation_fields(self):
        value, _ = self.observation()
        cached = {"complete": True, "account": value,
                  "account_observation_complete": True, "account_observed_nav": "123"}
        code, body = self.invoke(cached, error=ValueError("synthetic invalid account"))
        self.assertEqual(code, 0)
        self.assertFalse(body["account_observation_complete"])
        self.assertIsNone(body["account_observed_nav"])
        self.assertFalse(body["account"]["observation_complete"])
        self.assertIsNone(body["account"]["observed_nav"])


if __name__ == "__main__":
    unittest.main()
