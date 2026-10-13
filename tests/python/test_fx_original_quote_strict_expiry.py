"""Strict end-exclusive freshness across original PAPER quote capture and consumers.

No IB connection, source mutation, orders, or subscription side effects.
"""
from __future__ import annotations

import inspect
import unittest

from laya_cell.shadow_source import valid_quote

NOW=1791885000000

def q(*,observed=NOW-100,expiry=NOW+1000):
    return {"instrument":"EUR.USD","source":"IB","authoritative":True,
            "stale":False,"subscription_id":"isolated-original-fixture",
            "observed_at_ms":observed,"stale_after_ms":expiry,
            "bid":"1.10000","ask":"1.10002"}

class OriginalQuoteExpiryTests(unittest.TestCase):
    def fails(self,quote,received):
        with self.assertRaisesRegex(ValueError,"^QUOTE_STALE_OR_FUTURE$"):
            valid_quote(quote,received,"EUR.USD")

    def test_fresh_quote_before_end_is_valid(self):
        sample=q(expiry=NOW+100)
        bid,ask=valid_quote(sample,NOW+99,"EUR.USD")
        self.assertLess(bid,ask)
    def test_expiry_boundary_is_exclusive(self):
        self.fails(q(expiry=NOW),NOW)
    def test_event_plus_5000_boundary_is_exclusive(self):
        sample=q(observed=NOW-5000,expiry=NOW+2000)
        self.fails(sample,NOW)
    def test_event_plus_4999_still_valid(self):
        sample=q(observed=NOW-4999,expiry=NOW+2000)
        valid_quote(sample,NOW,"EUR.USD")
    def test_after_ib_reported_expiry_refused(self):
        self.fails(q(expiry=NOW-1),NOW)
    def test_min_of_broker_expiry_and_five_seconds(self):
        # Earliest boundary wins; later broker stale_after never refreshes
        # the earlier original event's five-second age.
        self.fails(q(observed=NOW-5000,expiry=NOW+100000),NOW)
        self.fails(q(observed=NOW-400,expiry=NOW),NOW)
    def test_fresh_original_recovery_never_refreshes_old_timestamp(self):
        old=q(observed=NOW-400,expiry=NOW+120)
        valid_quote(old,NOW+119,"EUR.USD")
        self.fails(old,NOW+120)
        new=q(observed=NOW+180,expiry=NOW+900)
        valid_quote(new,NOW+200,"EUR.USD")
        self.assertEqual(old["observed_at_ms"],NOW-400)
        self.assertEqual(new["observed_at_ms"],NOW+180)
    def test_bool_event_clock_refused(self):
        self.fails(q(observed=True),NOW)
    def test_both_boundary_policies_share_strict_expiry(self):
        # Other original feature/decision paths already use now < valid_until.
        source=inspect.getsource(valid_quote)
        self.assertIn("received<min(",source)
        self.assertNotIn("observed_at_ms'] =",source)
        self.assertNotIn("subprocess",source)

if __name__=="__main__":unittest.main()
