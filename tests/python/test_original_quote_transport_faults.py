"""No-broker synthetic response faults for original PAPER quote collection."""
from __future__ import annotations
import copy
import inspect
import json
import unittest

from laya_cell import shadow_source as source

NOW=1791884000000

def fresh(**changes):
    result={
        "instrument":"EUR.USD","source":"IB","authoritative":True,
        "stale":False,"bid":"1.10000","ask":"1.10002",
        "observed_at_ms":NOW-250,"stale_after_ms":NOW+1500,
        "subscription_id":"isolated-fixture-subscription",
        "subscription_state":"active",
    }
    result.update(changes)
    return result

def payload(q=None,*,status="ok",reason_code=None):
    obj={"status":status,"payload":fresh() if q is None else q}
    if reason_code is not None:obj["reason_code"]=reason_code
    return json.dumps(obj,sort_keys=True)

def decode(data,returncode=0,at=NOW):
    return source.parse_native_quote_reply(returncode,data,at,"EUR.USD")

class QuoteTransportFaultTests(unittest.TestCase):
    def check_bad(self,raw,code,*,rc=0,at=NOW):
        with self.assertRaisesRegex(ValueError,"^"+code+"$"):
            decode(raw,rc,at)
    def test_original_fresh_quote_remains_valid(self):
        q=decode(payload())
        self.assertEqual(q["subscription_state"],"active")
        self.assertLess(q["observed_at_ms"],NOW)
    def test_broker_10197_preserved_without_fabricated_price(self):
        self.check_bad(payload(status="error",reason_code=
                           "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197"),
                       "IB_PAPER_REALTIME_MARKET_DATA_UNAVAILABLE_10197",rc=9)
    def test_gateway_execution_response_read_failure_keeps_reason(self):
        self.check_bad(payload(status="error",
            reason_code="EXECUTION_EVENT_RESPONSE_READ_FAILED"),
            "EXECUTION_EVENT_RESPONSE_READ_FAILED")
    def test_authoritative_quote_stale_keeps_reason(self):
        self.check_bad(payload(status="error",reason_code="AUTHORITATIVE_QUOTE_STALE"),
                       "AUTHORITATIVE_QUOTE_STALE")
    def test_unready_subscription_refused_even_if_wrong_flags_say_good(self):
        self.check_bad(payload(fresh(subscription_state="inactive")),
                       "QUOTE_SUBSCRIPTION_NOT_ACTIVE")
    def test_subscription_is_missing(self):
        q=fresh();del q["subscription_state"]
        self.check_bad(payload(q),"QUOTE_SUBSCRIPTION_NOT_ACTIVE")
    def test_missing_payload(self):
        self.check_bad(json.dumps({"status":"ok"}),"QUOTE_TOOL_PAYLOAD_INVALID")
    def test_invalid_status(self):
        self.check_bad(payload(status="ERROR",reason_code="QUOTE_UNAVAILABLE"),
                       "QUOTE_UNAVAILABLE")
    def test_bad_error_code_does_not_escape_to_receipt(self):
        self.check_bad(payload(status="error",reason_code="unsafe/secret\n"),
                       "QUOTE_TOOL_UNAVAILABLE")
    def test_nonzero_exit_even_with_status_ok(self):
        self.check_bad(payload(), "QUOTE_TOOL_UNAVAILABLE",rc=1)
    def test_broken_stdout(self):
        self.check_bad('{"status":"ok"', "QUOTE_TOOL_JSON_INVALID")
    def test_empty_stdout(self):
        self.check_bad('',"QUOTE_TOOL_JSON_INVALID")
    def test_duplicate_json_fields(self):
        self.check_bad('{"status":"ok","status":"ok"}',"QUOTE_TOOL_JSON_INVALID")
    def test_nan_payload(self):
        self.check_bad('{"status":"ok","payload":{"bid":NaN}}',"QUOTE_TOOL_JSON_INVALID")
    def test_untrusted_source(self):
        self.check_bad(payload(fresh(source="CACHE")),"QUOTE_AUTHORITY")
    def test_false_authority(self):
        self.check_bad(payload(fresh(authoritative=False)),"QUOTE_AUTHORITY")
    def test_stale_flag(self):
        self.check_bad(payload(fresh(stale=True)),"QUOTE_AUTHORITY")
    def test_quote_observed_in_future(self):
        self.check_bad(payload(fresh(observed_at_ms=NOW+1)),"QUOTE_STALE_OR_FUTURE")
    def test_quote_expires_exactly_at_receive(self):
        self.check_bad(payload(fresh(stale_after_ms=NOW)),"QUOTE_STALE_OR_FUTURE")
    def test_quote_after_expiry(self):
        self.check_bad(payload(fresh(stale_after_ms=NOW-1)),"QUOTE_STALE_OR_FUTURE")
    def test_quote_lifetime_max_five_seconds(self):
        self.check_bad(payload(fresh(observed_at_ms=NOW-5001,
                      stale_after_ms=NOW+1000)),"QUOTE_STALE_OR_FUTURE")
    def test_bool_time_refused(self):
        self.check_bad(payload(fresh(observed_at_ms=True)),"QUOTE_STALE_OR_FUTURE")
    def test_invalid_bid_ask_crossed(self):
        self.check_bad(payload(fresh(bid="1.3",ask="1.2")),"QUOTE_NUMERIC")
    def test_nonfinite_price_string(self):
        self.check_bad(payload(fresh(bid="Infinity")),"QUOTE_NUMERIC")
    def test_negative_price(self):
        self.check_bad(payload(fresh(bid="-1")),"QUOTE_NUMERIC")
    def test_wrong_instrument(self):
        self.check_bad(payload(fresh(instrument="BTC.USD")),"QUOTE_AUTHORITY")
    def test_explicit_recovery_is_new_quote_not_stale_reuse(self):
        q0=fresh(observed_at_ms=NOW-400,stale_after_ms=NOW+300)
        self.assertEqual(decode(payload(q0))["observed_at_ms"],NOW-400)
        self.check_bad(payload(q0),"QUOTE_STALE_OR_FUTURE",at=NOW+301)
        # A fresh original quote from another real subscription is
        # accepted as new data, but NO old timestamp is refreshed.
        q1=fresh(observed_at_ms=NOW+320,stale_after_ms=NOW+2000,
                 subscription_id="isolated-new-subscription")
        out=decode(payload(q1),at=NOW+500)
        self.assertEqual(out["observed_at_ms"],NOW+320)
        self.assertNotEqual(out["subscription_id"],q0["subscription_id"])
    def test_no_order_or_subscription_side_effects_in_pure_parser(self):
        code=inspect.getsource(source.parse_native_quote_reply)
        self.assertNotIn("subprocess",code)
        self.assertNotIn("call\u0027,\u0027market.get_quote",code)
        self.assertNotIn("orders.place",code)
        self.assertNotIn("resubscribe",code.lower())
        self.assertNotIn("observed_at_ms'] =",code)

if __name__=="__main__":unittest.main()
