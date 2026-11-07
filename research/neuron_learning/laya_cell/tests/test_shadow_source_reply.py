"""Read-only native quote failure diagnostics and original freshness guards."""
import json
import unittest
from laya_cell.shadow_source import parse_native_quote_reply

T = 1794030355422

def reply():
    return json.dumps({'status':'ok','payload':{
        'instrument':'EUR.USD','source':'IB','authoritative':True,'stale':False,
        'bid':'1.12500','ask':'1.12502','observed_at_ms':T,'stale_after_ms':T+5000,
        'subscription_id':'UNIT-FIXTURE','subscription_state':'active'}})

class NativeQuoteReplyTests(unittest.TestCase):
    def test_observed_discovery_exit_has_distinct_reviewed_reason(self):
        with self.assertRaisesRegex(ValueError, '^DISCOVERY_LIST_REJECTED$'):
            parse_native_quote_reply(10, '', T, 'EUR.USD',
                                     stderr='DISCOVERY_LIST_REJECTED\n')

    def test_unknown_mixed_and_long_stderr_never_escapes(self):
        for stderr in ('PRIVATE_CREDENTIAL', 'DISCOVERY_LIST_REJECTED\nSECRET',
                       'SECRET\nDISCOVERY_LIST_REJECTED\n', 'X'*65536):
            with self.subTest(length=len(stderr)):
                with self.assertRaisesRegex(ValueError, '^QUOTE_TOOL_NO_JSON_REPLY$'):
                    parse_native_quote_reply(10, '', T, 'EUR.USD', stderr=stderr)

    def test_successful_empty_reply_remains_malformed(self):
        with self.assertRaisesRegex(ValueError, '^QUOTE_TOOL_JSON_INVALID$'):
            parse_native_quote_reply(0, '', T, 'EUR.USD',
                                     stderr='DISCOVERY_LIST_REJECTED\n')

    def test_nonempty_malformed_reply_is_not_relabelled_by_stderr(self):
        with self.assertRaisesRegex(ValueError, '^QUOTE_TOOL_JSON_INVALID$'):
            parse_native_quote_reply(10, '{', T, 'EUR.USD',
                                     stderr='DISCOVERY_LIST_REJECTED\n')

    def test_error_json_retains_validated_owner_reason(self):
        raw=json.dumps({'status':'error','reason_code':'EXECUTION_OWNER_UNAVAILABLE'})
        with self.assertRaisesRegex(ValueError, '^EXECUTION_OWNER_UNAVAILABLE$'):
            parse_native_quote_reply(10, raw, T, 'EUR.USD', stderr='SECRET')

    def test_fresh_quote_and_original_freshness_boundary(self):
        self.assertEqual(parse_native_quote_reply(0, reply(), T+4999, 'EUR.USD',
                                                 stderr='SECRET')['observed_at_ms'], T)
        with self.assertRaisesRegex(ValueError, 'STALE'):
            parse_native_quote_reply(0, reply(), T+5000, 'EUR.USD')
        with self.assertRaisesRegex(ValueError, 'FUTURE'):
            parse_native_quote_reply(0, reply(), T-1, 'EUR.USD')

    def test_nonzero_exit_never_accepts_a_quote(self):
        with self.assertRaisesRegex(ValueError, '^QUOTE_TOOL_UNAVAILABLE$'):
            parse_native_quote_reply(10, reply(), T, 'EUR.USD')

if __name__=='__main__':
    unittest.main()
